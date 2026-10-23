"""DeepSeek 官方 Search / Fetch API 客户端。

设计约束（硬性）：

- 只允许调用 /v1/tools/search 与 /v1/tools/fetch 两个端点，
  不接受对话（/v1/chat/completions）、识图、embedding 等任何其他用途；
  越界调用会直接抛 EndpointNotAllowedError，且没有任何通用请求入口可以绕过。
- 不做本地重试风暴：只对 429 / 5xx / 网络错误做有限指数退避，
  401 / 402 / 403 / 404 / 400 立即失败并给出可执行的修复建议。
- 传输层可注入（http_post 参数），方便单元测试完全不联网。

API 计费：每 1000 次成功请求 5 元，失败与无结果不计费。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import (
    AuthError,
    BadRequestError,
    ConfigError,
    DeepSeekError,
    EndpointNotAllowedError,
    ForbiddenError,
    InsufficientBalanceError,
    InvalidArgumentError,
    NotFoundError,
    RateLimitedError,
    TransportError,
    UpstreamError,
)
from .models import SearchResult

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_USER_AGENT = "Neo-MoFox-DeepSeek-WebSearch/1.0 (+https://docs.mofox.chat)"

#: 唯一允许访问的两个端点。
ALLOWED_PATHS: frozenset[str] = frozenset({"/v1/tools/search", "/v1/tools/fetch"})

#: 单次 query 上限（与官方一致）。
MAX_URL_CHARS = 2048

_PRIVATE_HOST_PREFIXES = (
    "127.",
    "10.",
    "192.168.",
    "169.254.",
    "0.",
    "::1",
    "fc",
    "fd",
)
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """传输层的统一响应结构。"""

    status: int
    headers: dict[str, str]
    body: str


#: 传输函数签名：(url, headers, payload, timeout) -> HttpResponse
TransportCallable = Callable[[str, dict[str, str], dict[str, Any], float], Awaitable[HttpResponse]]


def normalize_base_url(raw: str) -> str:
    """规范化 API 基址，避免出现 /v1/v1/tools/search 这类拼接错误。

    Args:
        raw: 用户配置的基址，例如 https://api.deepseek.com 或 https://api.deepseek.com/v1。

    Returns:
        规范化后的基址（无结尾斜杠、无 /v1 后缀）。

    Raises:
        ConfigError: 基址不是合法的 http(s) 地址。
    """

    value = (raw or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_BASE_URL

    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"[api].base_url 不是合法的 http(s) 地址：{raw!r}",
            hint="通常保持默认 https://api.deepseek.com 即可",
        )
    if parsed.query or parsed.fragment:
        raise ConfigError(
            f"[api].base_url 不应包含查询串或锚点：{raw!r}",
            hint="填写形如 https://api.deepseek.com 的基址",
        )

    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _is_private_host(host: str) -> bool:
    """判断主机名是否指向本机 / 内网。"""

    lowered = (host or "").strip().lower()
    if not lowered:
        return True
    if lowered in ("localhost", "localhost.localdomain", "::", "::1"):
        return True
    if lowered.startswith(_PRIVATE_HOST_PREFIXES):
        return True
    if lowered.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    # 172.16.0.0/12
    if lowered.startswith("172."):
        parts = lowered.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def validate_fetch_url(url: str, *, block_private_hosts: bool = True) -> str:
    """校验待抓取 URL。

    Args:
        url: 目标地址。
        block_private_hosts: 是否拒绝本机 / 内网地址。

    Returns:
        清理后的 URL。

    Raises:
        InvalidArgumentError: URL 不合法或被安全策略拒绝。
    """

    value = (url or "").strip()
    if not value:
        raise InvalidArgumentError("url 不能为空", hint="给出要抓取的 http(s) 链接")

    if len(value) > MAX_URL_CHARS:
        raise InvalidArgumentError(
            f"url 过长：{len(value)} 字符，上限 {MAX_URL_CHARS}",
            hint="换用短链接或直接搜索",
        )

    if any(ch.isspace() for ch in value):
        raise InvalidArgumentError("url 中不能包含空白字符", hint="检查链接是否被换行截断")

    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise InvalidArgumentError(
            f"只支持 http/https 链接：{value!r}",
            hint="不要省略 https:// 前缀",
        )
    if not parsed.netloc:
        raise InvalidArgumentError(f"url 缺少主机名：{value!r}")
    if "@" in parsed.netloc:
        raise InvalidArgumentError(
            "url 中不允许携带用户名或密码",
            hint="去掉 user:pass@ 部分后再试",
        )
    if block_private_hosts and _is_private_host(parsed.hostname or ""):
        raise InvalidArgumentError(
            f"出于安全考虑，拒绝抓取本机 / 内网地址：{parsed.hostname}",
            hint="如需抓取内网地址，请在配置中关闭 [fetch].block_private_hosts",
        )
    return value


def _extract_error_message(body: str) -> str:
    """从上游错误响应里提取人类可读信息。"""

    text = (body or "").strip()
    if not text:
        return "上游返回空响应"
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return text[:300]


class DeepSeekClient:
    """DeepSeek 官方搜索客户端。

    Args:
        api_key: DeepSeek API Key。
        base_url: API 基址。
        timeout: 单次请求超时秒数。
        max_retries: 可重试错误的重试次数（不含首次）。
        use_proxy: 是否读取系统代理环境变量。
        proxy: 显式代理地址，空串表示不指定。
        verify_ssl: 是否校验 TLS 证书。
        user_agent: 请求 UA。
        http_post: 自定义传输函数（测试注入用）。
        sleep: 自定义 sleep 函数（测试注入用）。
        clock: 单调时钟。
        logger: 日志器，需提供 warning 方法。
        rng: 抖动随机源。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 2,
        use_proxy: bool = True,
        proxy: str = "",
        verify_ssl: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        http_post: TransportCallable | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        logger: Any = None,
        rng: random.Random | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ConfigError(
                "DeepSeek API Key 为空",
                hint="在 config/plugins/deepseek_websearch/config.toml 的 [api].api_key 填写，"
                "或设置环境变量 DEEPSEEK_API_KEY",
            )

        self.api_key = key
        self.base_url = normalize_base_url(base_url)
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self.use_proxy = bool(use_proxy)
        self.proxy = (proxy or "").strip()
        self.verify_ssl = bool(verify_ssl)
        self.user_agent = user_agent or DEFAULT_USER_AGENT

        self._http_post = http_post or self._httpx_post
        self._sleep = sleep or asyncio.sleep
        self._clock = clock
        self._logger = logger
        self._rng = rng or random.Random()

        self._client: Any = None
        self.request_count = 0

    # -- 只读属性 -----------------------------------------------------------

    @property
    def masked_key(self) -> str:
        """日志里使用的脱敏 Key。"""

        if len(self.api_key) <= 8:
            return "****"
        return f"{self.api_key[:4]}****{self.api_key[-4:]}"

    # -- 公开 API -----------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        count: int = 10,
        need_content: bool = False,
        ori_question: str = "",
    ) -> list[SearchResult]:
        """调用 /v1/tools/search 搜索网页。

        Args:
            query: 已构造好的查询词。
            count: 返回条数 1~50。
            need_content: 是否同时返回网页正文。
            ori_question: 用户原始问题，帮助搜索引擎理解意图。

        Returns:
            搜索结果列表（上游已按相关度排序）。

        Raises:
            DeepSeekError: 上游或网络错误。
        """

        payload: dict[str, Any] = {
            "query": query,
            "count": int(count),
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = ori_question

        data = await self._post("/v1/tools/search", payload)
        raw_results = data.get("results")
        if raw_results is None:
            return []
        if not isinstance(raw_results, list):
            raise UpstreamError(
                "搜索接口返回结构异常：results 不是列表",
                hint="这是上游格式变化，请反馈插件作者",
            )
        results: list[SearchResult] = []
        for item in raw_results:
            if isinstance(item, Mapping):
                results.append(SearchResult.from_api(item))
        return results

    async def fetch(self, url: str) -> tuple[str, str]:
        """调用 /v1/tools/fetch 抓取网页正文。

        Args:
            url: 已校验的目标链接。

        Returns:
            (标题, Markdown 正文)。

        Raises:
            DeepSeekError: 上游或网络错误。
        """

        data = await self._post("/v1/tools/fetch", {"url": url})
        title = data.get("title")
        content = data.get("content")
        title_text = title.strip() if isinstance(title, str) else ""
        content_text = content if isinstance(content, str) else ""
        if not content_text.strip():
            raise NotFoundError(
                "抓取成功但正文为空，可能是纯 JS 渲染页面",
                hint="换一个可信来源，或改用 deepseek_search 找同话题的其他页面",
            )
        return title_text, content_text

    async def aclose(self) -> None:
        """关闭底层 HTTP 连接池。"""

        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - 关闭失败不应影响卸载
                pass

    # -- 内部实现 -----------------------------------------------------------

    def _build_url(self, path: str) -> str:
        """校验端点白名单并拼接完整 URL。"""

        if path not in ALLOWED_PATHS:
            raise EndpointNotAllowedError(
                f"拒绝调用未授权端点：{path}",
            )
        return f"{self.base_url}{path}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """带重试与错误映射的 POST。"""

        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

        last_error: DeepSeekError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._http_post(url, headers, payload, self.timeout)
            except TransportError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                await self._sleep(self._backoff(attempt))
                continue

            if 200 <= response.status < 300:
                self.request_count += 1
                return self._parse_json(response)

            error = self._map_http_error(response)
            last_error = error
            if error.retryable and attempt < self.max_retries:
                delay = self._retry_delay(response, attempt)
                self._warn(
                    f"DeepSeek {path} 返回 HTTP {response.status}，"
                    f"{delay:.2f}s 后重试（第 {attempt + 1}/{self.max_retries} 次）"
                )
                await self._sleep(delay)
                continue
            raise error

        # 理论上不会走到这里
        raise last_error or UpstreamError("请求失败", hint="稍后重试")

    def _parse_json(self, response: HttpResponse) -> dict[str, Any]:
        """解析成功响应。"""

        text = response.body or ""
        if not text.strip():
            raise UpstreamError(
                "上游返回空响应体",
                hint="稍后重试；若持续出现请检查 [api].base_url 是否指向了代理",
            )
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise UpstreamError(
                f"上游返回的不是合法 JSON：{text[:120]!r}",
                hint="检查 [api].base_url，确认没有把请求指向网页或其他服务",
            ) from exc
        if not isinstance(data, dict):
            raise UpstreamError("上游返回的 JSON 顶层不是对象", hint="这是上游格式变化")
        return data

    def _map_http_error(self, response: HttpResponse) -> DeepSeekError:
        """把 HTTP 状态码映射成带修复建议的异常。"""

        message = _extract_error_message(response.body)
        detail = f"HTTP {response.status}: {message}"

        if response.status == 400:
            return BadRequestError(detail, status=400)
        if response.status == 401:
            return AuthError(detail, status=401)
        if response.status == 402:
            return InsufficientBalanceError(detail, status=402)
        if response.status == 403:
            return ForbiddenError(detail, status=403)
        if response.status == 404:
            return NotFoundError(detail, status=404)
        if response.status == 413:
            return BadRequestError(
                detail,
                hint="请求体过大：抓取链接过长时请改用短链接",
                status=413,
            )
        if response.status == 429:
            return RateLimitedError(detail, retryable=True, status=429)
        if response.status in (408, 504):
            return UpstreamError(detail, retryable=True, status=response.status)
        if 500 <= response.status < 600:
            return UpstreamError(detail, retryable=True, status=response.status)
        return DeepSeekError(detail, hint="未预期的状态码，可反馈给插件作者", status=response.status)

    def _backoff(self, attempt: int) -> float:
        """指数退避加抖动，最大 8 秒。"""

        base = min(0.5 * (2 ** attempt), 8.0)
        return base + self._rng.uniform(0, 0.3)

    def _retry_delay(self, response: HttpResponse, attempt: int) -> float:
        """优先遵循上游 Retry-After，其次使用指数退避。"""

        raw = ""
        for key, value in response.headers.items():
            if key.lower() == "retry-after":
                raw = str(value)
                break
        if raw:
            try:
                return min(max(float(raw.strip()), 0.0), 10.0)
            except ValueError:
                pass
        return self._backoff(attempt)

    def _warn(self, message: str) -> None:
        """安全地写一条 warning 日志。"""

        if self._logger is not None:
            try:
                self._logger.warning(message)
            except Exception:  # pragma: no cover - 日志失败不应影响主流程
                pass

    # -- 默认传输层（httpx） -------------------------------------------------

    async def _httpx_post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> HttpResponse:
        """基于 httpx 的默认传输实现。"""

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - 核心必然带了 httpx
            raise TransportError(
                "缺少 httpx 依赖",
                hint="Neo-MoFox 核心自带 httpx，出现此错误请检查运行环境",
                retryable=False,
            ) from exc

        if self._client is None:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(timeout),
                "trust_env": self.use_proxy,
                "verify": self.verify_ssl,
                "limits": httpx.Limits(max_connections=8, max_keepalive_connections=4),
                "follow_redirects": False,
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(**kwargs)

        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except Exception as exc:  # httpx 各种异常统一转成 TransportError
            timeout_hit = type(exc).__name__ in {
                "TimeoutException",
                "ConnectTimeout",
                "ReadTimeout",
                "WriteTimeout",
                "PoolTimeout",
            }
            raise TransportError(
                f"请求 DeepSeek API 失败：{exc}",
                timeout=timeout_hit,
                retryable=True,
            ) from exc

        return HttpResponse(
            status=response.status_code,
            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
            body=response.text,
        )
