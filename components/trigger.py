"""自然语言触发器。

群友发「帮我搜一下 Neo-MoFox 最新文档」时，不经过 LLM 决策、也不要求打斜杠命令，
插件自己发起一次 DeepSeek 官方检索，把结果作为「系统注入」追加到本轮消息里，
随后正常走 chatter 流程 —— 模型回答时就能看到这批实时资料。

要点：

- 默认开启，但有三道闸门：触发正则、同一聊天流冷却、每日自动触发上限；
- 检索超时 / 失败 / 无结果都直接放行原消息，绝不阻断聊天；
- 注入内容带明确边界标记，并复用「外部数据不可信」提示，降低提示词注入风险。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision

from ..core.errors import DeepSeekError
from ..core.formatting import truncate_text
from .common import get_runtime
from .render import render_search_markdown

logger = get_logger("deepseek_websearch")

#: 注入内容的边界标记。看到它就说明这段不是用户说的。
INJECT_HEADER = "\n\n【系统注入：DeepSeek 联网检索（自动触发，不是用户说的话，仅供回答参考）】\n"
INJECT_FOOTER = "\n【系统注入结束】"

#: 从口语消息里剥掉「帮我搜一下」这类前缀。
QUERY_PREFIX_RE = re.compile(
    r"^(?:帮我|帮忙|麻烦|请|替我|给我)?\s*"
    r"(?:搜一下|搜搜|搜索一下|搜索|搜|查一下|查查|查询一下|查询|查|找一下|找找|找)\s*"
)

#: 剥掉句尾语气词与客套话。
QUERY_SUFFIX_RE = re.compile(
    r"(?:吧|呢|啊|呀|哈|嘛|哦|啦|谢谢|多谢|可以吗|好吗|\s)+$"
)

#: 剥掉首尾标点。
_STRIP_CHARS = " \t\r\n，。！？、,.!?;；:：\"'“”‘’（）()【】[]"

#: 冷却表最多保留多少条，防止长期运行内存膨胀。
_MAX_COOLDOWN_ENTRIES = 256


def clean_query(text: str, max_chars: int = 120) -> str:
    """把一句口语消息清理成检索词。

    Args:
        text: 原始消息文本。
        max_chars: 结果最大长度。

    Returns:
        清理后的检索词；无法提取时返回空串。
    """

    value = (text or "").strip()
    if not value:
        return ""
    value = QUERY_PREFIX_RE.sub("", value)
    value = QUERY_SUFFIX_RE.sub("", value)
    value = value.strip(_STRIP_CHARS)
    value = " ".join(value.split())
    return value[: max(1, max_chars)].strip()


def _iter_segments(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """取出信封里的消息段列表（兼容单段 dict 与段列表）。"""

    raw = envelope.get("message_segment")
    if raw is None:
        raw = envelope.get("message_chain")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _segment_text(segment: dict[str, Any]) -> str:
    """读取文本段的文本（data 可能是 str 或 {"text": str}）。"""

    if segment.get("type") != "text":
        return ""
    data = segment.get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        inner = data.get("text")
        if isinstance(inner, str):
            return inner
    return ""


def _write_segment_text(segment: dict[str, Any], text: str) -> None:
    """就地改写文本段。"""

    data = segment.get("data")
    if isinstance(data, dict):
        data["text"] = text
    else:
        segment["data"] = text


class DeepSeekNaturalTrigger(BaseEventHandler):
    """自然语言触发联网检索。"""

    name = "deepseek_natural_trigger"
    description = "命中检索意图时自动联网搜索，并把结果注入本轮消息（不拦截消息）"
    weight = 0
    intercept_message = False
    init_subscribe = [EventType.BEFORE_MESSAGE_RECEIVED]
    # 自己用 asyncio.wait_for 控制超时，避免被事件总线的全局超时提前打断
    timeout = 0

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._last_fire: dict[str, float] = {}
        self._day = ""
        self._count = 0
        self._compiled: list[re.Pattern[str]] | None = None
        self._patterns_source: tuple[str, ...] = ()

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理入站消息：必要时自动检索并注入结果。"""

        try:
            return await self._try_trigger(params)
        except Exception as exc:  # 任何异常都不能阻断消息
            logger.warning(f"自然语言触发器异常，已放行原消息：{exc!r}")
            return EventDecision.PASS, params

    # -- 主流程 -------------------------------------------------------------

    async def _try_trigger(
        self, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """判断是否触发，并在触发时注入检索结果。"""

        envelope = params.get("envelope")
        if not isinstance(envelope, dict):
            return EventDecision.PASS, params
        info = envelope.get("message_info")
        if not isinstance(info, dict):
            return EventDecision.PASS, params

        segments = _iter_segments(envelope)
        target = next((seg for seg in segments if _segment_text(seg).strip()), None)
        if target is None:
            return EventDecision.PASS, params
        original = _segment_text(target)

        # 已经注入过（重投递等极端情况）就不再重复检索
        if INJECT_HEADER.strip() in original:
            return EventDecision.PASS, params

        runtime = await get_runtime(self)
        config = runtime.config
        trigger = config.trigger
        if not bool(getattr(trigger, "enabled", False)):
            return EventDecision.PASS, params
        if not self._chat_allowed(info, trigger):
            return EventDecision.PASS, params
        if self._is_skipped(original, trigger):
            return EventDecision.PASS, params
        if not self._matches(original, trigger):
            return EventDecision.PASS, params

        stream_key = self._stream_key(info)
        if not self._cooldown_ok(runtime, stream_key, trigger):
            return EventDecision.PASS, params
        if not self._daily_allowed(runtime, trigger):
            return EventDecision.PASS, params

        query = clean_query(original, int(getattr(trigger, "max_query_chars", 120)))
        if len(query) < int(getattr(trigger, "min_query_chars", 2)):
            return EventDecision.PASS, params

        try:
            outcome = await asyncio.wait_for(
                runtime.search(
                    query,
                    count=int(getattr(trigger, "count", 5)),
                    need_content=bool(getattr(trigger, "need_content", False)),
                    ori_question=original[:200],
                ),
                timeout=max(1, int(getattr(trigger, "timeout_seconds", 20))),
            )
        except asyncio.TimeoutError:
            logger.warning(f"自动检索超时（{query!r}），本轮放行原消息")
            return EventDecision.PASS, params
        except DeepSeekError as exc:
            logger.warning(f"自动检索失败（{query!r}）：{exc.describe()}")
            return EventDecision.PASS, params

        if outcome.count == 0:
            logger.info(f"自动检索没有结果（{query!r}），不注入")
            return EventDecision.PASS, params

        budget = int(getattr(trigger, "max_inject_chars", 1800))
        block = await render_search_markdown(runtime, outcome)
        block = truncate_text(block, max(200, budget))

        _write_segment_text(target, f"{original}{INJECT_HEADER}{block}{INJECT_FOOTER}")
        self._record_fire(runtime, stream_key)

        logger.info(
            f"自然语言触发：{query!r} → {outcome.count} 条，"
            f"注入 {len(block)} 字符（缓存={outcome.cached}）"
        )
        return EventDecision.SUCCESS, params

    # -- 判定 ---------------------------------------------------------------

    @staticmethod
    def _chat_allowed(info: dict[str, Any], trigger: Any) -> bool:
        """群聊 / 私聊开关。"""

        group_info = info.get("group_info")
        is_group = isinstance(group_info, dict) and bool(group_info.get("group_id"))
        return bool(getattr(trigger, "allow_group", True)) if is_group else bool(
            getattr(trigger, "allow_private", True)
        )

    @staticmethod
    def _is_skipped(text: str, trigger: Any) -> bool:
        """命令消息不重复触发。"""

        stripped = text.lstrip()
        for prefix in getattr(trigger, "skip_prefixes", []) or []:
            if isinstance(prefix, str) and prefix and stripped.startswith(prefix):
                return True
        return False

    def _matches(self, text: str, trigger: Any) -> bool:
        """正则匹配触发意图（编译结果按模式列表缓存）。"""

        patterns = [p for p in (getattr(trigger, "patterns", []) or []) if isinstance(p, str) and p]
        if not patterns:
            return False

        source = tuple(patterns)
        if self._compiled is None or self._patterns_source != source:
            compiled: list[re.Pattern[str]] = []
            for pattern in patterns:
                try:
                    compiled.append(re.compile(pattern, re.IGNORECASE))
                except re.error as exc:
                    logger.warning(f"触发正则无效，已忽略：{pattern!r}（{exc}）")
            self._compiled = compiled
            self._patterns_source = source

        return any(rx.search(text) for rx in self._compiled or [])

    @staticmethod
    def _stream_key(info: dict[str, Any]) -> str:
        """按「平台 + 群号/用户号」生成冷却键。"""

        group_info = info.get("group_info")
        user_info = info.get("user_info")
        group_id = group_info.get("group_id") if isinstance(group_info, dict) else ""
        user_id = user_info.get("user_id") if isinstance(user_info, dict) else ""
        return f"{info.get('platform', '')}:{group_id or user_id or 'unknown'}"

    def _cooldown_ok(self, runtime: Any, key: str, trigger: Any) -> bool:
        """同一聊天流冷却。"""

        cooldown = float(getattr(trigger, "cooldown_seconds", 60) or 0)
        if cooldown <= 0:
            return True
        now = runtime.monotonic()
        last = self._last_fire.get(key, float("-inf"))
        if now - last < cooldown:
            return False
        return True

    def _daily_allowed(self, runtime: Any, trigger: Any) -> bool:
        """每日自动触发上限（与 [budget] 的配额相互独立）。"""

        today = runtime.now().strftime("%Y-%m-%d")
        if self._day != today:
            self._day = today
            self._count = 0
        limit = int(getattr(trigger, "daily_limit", 0) or 0)
        return limit <= 0 or self._count < limit

    def _record_fire(self, runtime: Any, key: str) -> None:
        """记录一次成功触发。"""

        if len(self._last_fire) >= _MAX_COOLDOWN_ENTRIES:
            self._last_fire.clear()
        self._last_fire[key] = runtime.monotonic()
        self._count += 1
