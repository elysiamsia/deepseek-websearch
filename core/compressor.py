"""正文压缩：用一个小模型把长文压到目标长度。

设计取舍：

- **默认关闭**：关闭时由调用方按旧逻辑截断，行为和 1.2.x 完全一致。
- **不依赖框架**：模型调用以 call_llm 协程注入，本模块只负责采样、提示词、
  超时与结果清洗，因此可以完全离线单测。
- **超长输入做头尾采样**：网页动辄几十万字，直接塞给模型既超上下文又烧钱；
  默认只取前 65% 与后 35%，中间标注省略字符数。
- **任何失败都降级为截断**：调用方拿到 method="truncate" 就按上限截断，
  绝不让压缩环节把整次抓取弄挂。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .formatting import truncate_text

#: 内置压缩提示词（配置里留空时使用）。
COMPRESS_SYSTEM_PROMPT = (
    "你是网页正文压缩器。只输出压缩后的正文本身，不要解释、不要前言、不要代码围栏。"
    "必须保留：事实、数字、日期、专有名词、结论、关键步骤、参数、URL 与代码片段。"
    "应当删除：导航栏、广告、版权声明、评论区、重复段落、无信息量的客套话。"
    "输出用简洁的短段落或要点列表；原文是中文就用中文，是英文就保持英文。"
)

#: call_llm 的签名：(system, user, max_tokens) -> 文本
LLMCallable = Callable[[str, str, int], Awaitable[str]]

#: 代码围栏（避免在源码里出现三反引号字面量）。
_FENCE = "<FENCE>"


class CompressionError(Exception):
    """压缩失败（调用方应回退到截断）。"""


@dataclass(frozen=True, slots=True)
class CompressedText:
    """压缩结果。

    Attributes:
        text: 最终要展示的文本。
        method: passthrough（原样）/ llm（模型压缩）/ truncate（截断）。
        original_chars: 原始字符数。
        reason: 回退原因（仅截断且由失败导致时有值）。
    """

    text: str
    method: str
    original_chars: int
    reason: str = ""


class ContentCompressor:
    """把长正文压到目标长度的压缩器。

    Args:
        config: [compress] 配置节。
        call_llm: 模型调用协程，签名见 LLMCallable；为空时等价于关闭。
        logger: 日志器。
    """

    def __init__(
        self,
        config: Any,
        *,
        call_llm: LLMCallable | None = None,
        logger: Any = None,
    ) -> None:
        self.config = config
        self._call_llm = call_llm
        self._logger = logger

    # -- 基本属性 -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """压缩是否可用（开关打开且已注入模型调用）。"""

        return bool(getattr(self.config, "enabled", False)) and self._call_llm is not None

    @property
    def model(self) -> str:
        """配置的小模型标识（任务名或模型名）。"""

        return str(getattr(self.config, "model", "") or "utils_small").strip() or "utils_small"

    @property
    def target_chars(self) -> int:
        """压缩目标长度。"""

        try:
            value = int(getattr(self.config, "target_chars", 2000))
        except (TypeError, ValueError):
            value = 2000
        return max(200, value)

    # -- 对外接口 -----------------------------------------------------------

    async def shrink(
        self,
        text: str,
        *,
        fallback_chars: int = 0,
        target_chars: int | None = None,
        kind: str = "fetch",
    ) -> CompressedText:
        """压缩一段正文。

        Args:
            text: 原始正文。
            fallback_chars: 压缩不可用／失败时的截断上限（<=0 表示不截断）。
            target_chars: 覆盖配置里的目标长度。
            kind: 内容类别，仅用于日志。

        Returns:
            CompressedText。
        """

        original = len(text or "")
        if not (text or "").strip():
            return CompressedText("", "passthrough", 0)

        target = max(200, int(target_chars or self.target_chars))

        if not self.enabled:
            return self._fallback(text, fallback_chars, original)

        if original <= target:
            return CompressedText(text, "passthrough", original)

        try:
            compressed = await self._compress(text, target, kind=kind)
        except Exception as exc:  # 任何失败都降级为截断
            self._warn(f"正文压缩失败（{kind}），回退截断：{exc!r}")
            return self._fallback(text, fallback_chars, original, reason=str(exc))

        if not compressed:
            self._warn(f"正文压缩返回空内容（{kind}），回退截断")
            return self._fallback(text, fallback_chars, original, reason="模型返回空内容")

        return CompressedText(compressed, "llm", original)

    # -- 内部实现 -----------------------------------------------------------

    def _fallback(
        self, text: str, fallback_chars: int, original: int, *, reason: str = ""
    ) -> CompressedText:
        """按上限截断（压缩关闭或失败时使用）。"""

        if fallback_chars and original > fallback_chars:
            return CompressedText(
                truncate_text(text, fallback_chars), "truncate", original, reason=reason
            )
        return CompressedText(text, "passthrough", original, reason=reason)

    async def _compress(self, text: str, target: int, *, kind: str) -> str:
        """真正调用模型。"""

        if self._call_llm is None:  # pragma: no cover - enabled 已保证
            raise CompressionError("没有可用的模型调用")

        body = self._sample(text, self._max_input_chars())
        system = str(getattr(self.config, "system_prompt", "") or "").strip()
        if not system:
            system = COMPRESS_SYSTEM_PROMPT
        user = (
            f"请把下面的网页正文压缩到 {target} 字以内。\n"
            f"只输出压缩结果本身，不要任何解释。\n\n"
            f"<<<正文开始>>>\n{body}\n<<<正文结束>>>"
        )

        timeout = float(getattr(self.config, "timeout_seconds", 90) or 90)
        result = await asyncio.wait_for(
            self._call_llm(system, user, self._max_tokens(target)),
            timeout=max(5.0, timeout),
        )
        cleaned = _strip_wrapping(str(result or ""))
        if not cleaned:
            raise CompressionError("模型返回空内容")
        # 防模型无视长度要求：超出目标两倍就硬截断
        if len(cleaned) > target * 2:
            cleaned = truncate_text(cleaned, target)
        return cleaned

    def _max_input_chars(self) -> int:
        """送进模型的正文上限。"""

        try:
            value = int(getattr(self.config, "max_input_chars", 30000))
        except (TypeError, ValueError):
            value = 30000
        return max(1000, value)

    def _max_tokens(self, target: int) -> int:
        """输出 token 预算：配置优先，否则按目标长度估算。"""

        try:
            configured = int(getattr(self.config, "max_tokens", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        if configured > 0:
            return configured
        # 中文约 1 字 ≈ 1 token，留 1.6 倍余量并封顶
        return max(256, min(8000, int(target * 1.6)))

    @staticmethod
    def _sample(text: str, limit: int) -> str:
        """超长正文做「头 + 尾」采样，保留开头信息与结尾结论。"""

        if limit <= 0 or len(text) <= limit:
            return text
        head_len = int(limit * 0.65)
        tail_len = max(0, limit - head_len)
        omitted = len(text) - head_len - tail_len
        head = text[:head_len]
        tail = text[-tail_len:] if tail_len else ""
        return f"{head}\n…（中间省略 {omitted} 字符）…\n{tail}"

    def _warn(self, message: str) -> None:
        """安全写日志。"""

        if self._logger is not None:
            try:
                self._logger.warning(message)
            except Exception:  # pragma: no cover
                pass


def _strip_wrapping(text: str) -> str:
    """去掉模型可能加的代码围栏与首尾空白。"""

    fence = chr(96) * 3
    value = (text or "").strip()
    if value.startswith(fence) and value.endswith(fence) and len(value) > 2 * len(fence):
        first_line, _, rest = value[len(fence):].partition("\n")
        # 允许围栏后直接跟语言名（如 python）
        if rest and first_line.strip().lower() in ("", "text", "markdown", "md", "plaintext"):
            value = rest[: -len(fence)].strip() if rest.rstrip().endswith(fence) else rest.strip()
    if value.startswith("~~~") and value.endswith("~~~") and len(value) > 6:
        value = value[3:-3].strip()
    return value.strip()
