"""运行时装配层。

把「配置 / HTTP 客户端 / 缓存 / 用量统计 / 持久化」装成一个 Runtime 对象，
Tool、Command、Service 都只跟 Runtime 打交道，保持组件类足够薄。

计费规则（DeepSeek 官方）：
- 每 1000 次「成功请求」5 元；
- 失败请求、无结果请求不计费；
- 本层只在真正调用上游且拿到结果时计数，缓存命中不计数。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any, Callable

from .cache import TTLCache
from .client import DeepSeekClient, validate_fetch_url
from .compressor import CompressedText, ContentCompressor
from .credentials import resolve_api_key
from .errors import (
    BudgetExceededError,
    DeepSeekError,
    MissingAPIKeyError,
    PluginDisabledError,
)
from .models import FetchOutcome, SearchOutcome, SearchResult
from .query import build_query
from .usage import UsageTracker

#: storage_api 使用的命名空间与键名。
USAGE_STORE = "deepseek_websearch"
USAGE_KEY = "usage"

#: ori_question 最大长度（帮助搜索引擎理解意图，不必塞整段上下文）。
MAX_ORI_QUESTION = 200

#: 用量落盘最小间隔（秒），避免每次请求都写盘。
PERSIST_INTERVAL = 30.0


class UsageStore:
    """通过 storage_api 的 JSON 存储读写用量（懒导入，方便脱离框架测试）。"""

    def __init__(self, store_name: str = USAGE_STORE, name: str = USAGE_KEY) -> None:
        self.store_name = store_name
        self.name = name

    async def load(self) -> dict[str, Any] | None:
        """读取历史用量；任何异常都等价于「没有历史」。"""

        from src.app.plugin_system.api import storage_api

        data = await storage_api.load_json(self.store_name, self.name)
        return data if isinstance(data, dict) else None

    async def save(self, data: dict[str, Any]) -> None:
        """写入用量。"""

        from src.app.plugin_system.api import storage_api

        await storage_api.save_json(self.store_name, self.name, data)


class Runtime:
    """插件运行时。

    Args:
        config: 插件配置实例。
        logger: 日志器。
        client: 可选的 DeepSeekClient（测试注入）。
        cache: 可选缓存实例。
        usage: 可选用量统计实例。
        store: 可选持久化实现（需提供 load / save 协程）。
        clock: 单调时钟。
        now: 墙上时间提供者。
    """

    def __init__(
        self,
        config: Any,
        *,
        logger: Any = None,
        client: DeepSeekClient | None = None,
        cache: TTLCache | None = None,
        usage: UsageTracker | None = None,
        store: Any = None,
        llm_call: Callable[[str, str, int], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self.logger = logger
        self.client = client
        self.usage = usage or UsageTracker()
        self.store = store if store is not None else UsageStore()
        self._clock = clock
        self._now = now
        self._started = False
        self._last_persist = float("-inf")
        self._api_key_source = ""
        self.cache = cache or TTLCache(
            max_entries=int(getattr(config.cache, "max_entries", 128)),
            ttl_seconds=float(getattr(config.cache, "ttl_seconds", 600)),
            clock=clock,
        )

        # 正文压缩：默认关闭；llm_call 为空时压缩器等价于「只截断」
        compress_config = getattr(config, "compress", None)
        self.compressor = (
            ContentCompressor(compress_config, call_llm=llm_call, logger=logger)
            if compress_config is not None
            else None
        )
        # 压缩结果缓存：同一段正文（含缓存命中的抓取结果）只压一次
        ttl = float(getattr(config.cache, "ttl_seconds", 600) or 600)
        self._shrink_cache = TTLCache(
            max_entries=256, ttl_seconds=ttl if ttl > 0 else 3600, clock=clock
        )

    # -- 生命周期 -----------------------------------------------------------

    async def start(self) -> None:
        """加载历史用量（幂等）。"""

        if self._started:
            return
        self._started = True
        if not self._persist_enabled():
            return
        try:
            data = await self.store.load()
        except Exception as exc:  # 存储异常不应阻断插件启动
            self._warn(f"读取历史用量失败，本次从零开始计数：{exc}")
            return
        if data:
            self.usage = UsageTracker.from_dict(data)
        self.usage.prune(self._now())

    async def aclose(self) -> None:
        """落盘并关闭 HTTP 连接池。"""

        await self._persist(force=True)
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        self._started = False

    # -- 正文压缩 -----------------------------------------------------------

    @property
    def compressor_enabled(self) -> bool:
        """小模型压缩是否可用（开关打开且已注入模型调用）。"""

        return bool(self.compressor is not None and self.compressor.enabled)

    async def shrink_text(
        self,
        text: str,
        *,
        fallback_chars: int = 0,
        kind: str = "fetch",
    ) -> CompressedText:
        """压缩（或截断）一段正文。

        压缩关闭 / 失败时返回截断结果；成功结果按正文哈希缓存，避免同一段内容
        因缓存命中或多次渲染而重复调用小模型。

        Args:
            text: 原始正文。
            fallback_chars: 压缩不可用时的截断上限（0 表示不截断）。
            kind: 内容类别（fetch / search），仅用于日志。

        Returns:
            CompressedText。
        """

        if self.compressor is None:
            from .formatting import truncate_text

            if fallback_chars and len(text) > fallback_chars:
                return CompressedText(
                    truncate_text(text, fallback_chars), "truncate", len(text)
                )
            return CompressedText(text, "passthrough", len(text))

        if not self.compressor_enabled:
            return await self.compressor.shrink(
                text, fallback_chars=fallback_chars, kind=kind
            )

        target = self.compressor.target_chars
        key = _compress_key(self.compressor.model, target, text)
        cached = self._shrink_cache.get(key)
        if isinstance(cached, str):
            self.usage.record_cache_hit()
            return CompressedText(cached, "llm", len(text))

        result = await self.compressor.shrink(
            text, fallback_chars=fallback_chars, kind=kind
        )
        if result.method == "llm":
            self._shrink_cache.set(key, result.text)
        return result

    # -- 对外能力 -----------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        count: int = 0,
        need_content: bool | None = None,
        site: str = "",
        exclude_site: str = "",
        lang: str = "",
        after: str = "",
        before: str = "",
        ori_question: str = "",
        use_cache: bool = True,
    ) -> SearchOutcome:
        """执行一次网页搜索。

        Args:
            query: 检索关键词。
            count: 返回条数，0 表示使用配置默认值。
            need_content: 是否返回正文，None 表示使用配置默认值。
            site: 限定站点。
            exclude_site: 排除站点。
            lang: 语言过滤。
            after: 起始日期。
            before: 结束日期。
            ori_question: 用户原始问题，帮助理解搜索意图。
            use_cache: 是否允许命中缓存。

        Returns:
            SearchOutcome。

        Raises:
            DeepSeekError: 配置、参数、网络或上游错误。
        """

        self._require_enabled()

        composed = build_query(
            query,
            site=site,
            exclude_site=exclude_site,
            lang=lang,
            after=after,
            before=before,
        )
        resolved_count = self._resolve_count(count)
        want_content = (
            bool(getattr(self.config.search, "default_need_content", False))
            if need_content is None
            else bool(need_content)
        )
        question = (ori_question or "").strip()[:MAX_ORI_QUESTION]

        key = _cache_key(
            "search",
            query=composed,
            count=resolved_count,
            need_content=want_content,
            ori_question=question,
        )
        if use_cache and self._cache_enabled:
            cached = self.cache.get(key)
            if cached is not None:
                self.usage.record_cache_hit()
                await self._persist()
                results = tuple(
                    SearchResult.from_api(item) for item in cached if isinstance(item, dict)
                )
                return self._search_outcome(composed, results, 0, cached=True, cost=0.0)

        self._check_budget()
        client = self._ensure_client()
        started = self._clock()
        try:
            results = await client.search(
                composed,
                count=resolved_count,
                need_content=want_content,
                ori_question=question,
            )
        except DeepSeekError:
            self.usage.record_failure(self._now())
            await self._persist(force=True)
            raise

        elapsed_ms = int((self._clock() - started) * 1000)
        now = self._now()
        if results:
            self.usage.record_success("search", now)
        else:
            self.usage.record_empty(now)

        if use_cache and self._cache_enabled and results:
            self.cache.set(key, [item.to_dict() for item in results])

        await self._persist()
        cost = self._price_per_request() if results else 0.0
        return self._search_outcome(
            composed, tuple(results), elapsed_ms, cached=False, cost=cost
        )

    async def fetch(self, url: str, *, max_chars: int = 0, use_cache: bool = True) -> FetchOutcome:
        """抓取单个网页正文。

        Args:
            url: 目标链接。
            max_chars: 正文上限（仅影响返回，不影响缓存内容）。
            use_cache: 是否允许命中缓存。

        Returns:
            FetchOutcome。

        Raises:
            DeepSeekError: 配置、参数、网络或上游错误。
        """

        self._require_enabled()

        target = validate_fetch_url(
            url,
            block_private_hosts=bool(getattr(self.config.fetch, "block_private_hosts", True)),
        )
        key = _cache_key("fetch", url=target)

        if use_cache and self._cache_enabled:
            cached = self.cache.get(key)
            if isinstance(cached, dict):
                self.usage.record_cache_hit()
                await self._persist()
                return self._fetch_outcome(
                    target,
                    str(cached.get("title") or ""),
                    str(cached.get("content") or ""),
                    0,
                    cached=True,
                    cost=0.0,
                )

        self._check_budget()
        client = self._ensure_client()
        started = self._clock()
        try:
            title, content = await client.fetch(target)
        except DeepSeekError:
            self.usage.record_failure(self._now())
            await self._persist(force=True)
            raise

        elapsed_ms = int((self._clock() - started) * 1000)
        self.usage.record_success("fetch", self._now())
        if use_cache and self._cache_enabled:
            self.cache.set(key, {"title": title, "content": content})

        await self._persist()
        return self._fetch_outcome(
            target, title, content, elapsed_ms, cached=False, cost=self._price_per_request()
        )

    # -- 时间访问器（供上层做冷却 / 按日计数）---------------------------------

    def now(self) -> datetime:
        """当前墙上时间（测试可注入固定值）。"""

        return self._now()

    def monotonic(self) -> float:
        """单调时钟秒数（测试可注入）。"""

        return self._clock()

    # -- 展示 / 管理 --------------------------------------------------------

    def usage_snapshot(self) -> dict[str, Any]:
        """用量统计快照（可直接 JSON 化）。"""

        snapshot = self.usage.snapshot(
            self._now(),
            price_per_1000=self._price_per_1000(),
            daily_limit=int(getattr(self.config.budget, "daily_request_limit", 0) or 0),
        )
        snapshot["cache"] = self.cache.stats()
        snapshot["api_key_source"] = self._api_key_source or "未使用"
        return snapshot

    def cache_stats(self) -> dict[str, Any]:
        """缓存统计。"""

        return self.cache.stats()

    async def clear_cache(self) -> int:
        """清空缓存，返回清理条数。"""

        removed = self.cache.clear()
        await self._persist(force=True)
        return removed

    async def reset_usage(self) -> None:
        """把用量统计归零并立即落盘。"""

        self.usage = UsageTracker()
        await self._persist(force=True)

    # -- 内部实现 -----------------------------------------------------------

    def _search_outcome(
        self,
        query: str,
        results: tuple[SearchResult, ...],
        elapsed_ms: int,
        *,
        cached: bool,
        cost: float,
    ) -> SearchOutcome:
        """组装 SearchOutcome 并补上配额信息。"""

        return SearchOutcome(
            query=query,
            results=results,
            elapsed_ms=elapsed_ms,
            cached=cached,
            cost_cny=cost,
            today_requests=self.usage.today_count(self._now()),
            daily_limit=int(getattr(self.config.budget, "daily_request_limit", 0) or 0),
        )

    def _fetch_outcome(
        self,
        url: str,
        title: str,
        content: str,
        elapsed_ms: int,
        *,
        cached: bool,
        cost: float,
    ) -> FetchOutcome:
        """组装 FetchOutcome 并补上配额信息。"""

        return FetchOutcome(
            url=url,
            title=title,
            content=content,
            elapsed_ms=elapsed_ms,
            cached=cached,
            cost_cny=cost,
            today_requests=self.usage.today_count(self._now()),
            daily_limit=int(getattr(self.config.budget, "daily_request_limit", 0) or 0),
        )

    def _require_enabled(self) -> None:
        """插件总开关。"""

        if not bool(getattr(self.config.plugin, "enabled", True)):
            raise PluginDisabledError("DeepSeek 搜索插件已在配置中禁用")

    def _check_budget(self) -> None:
        """检查当日请求配额。"""

        limit = int(getattr(self.config.budget, "daily_request_limit", 0) or 0)
        if limit <= 0:
            return
        used = self.usage.today_count(self._now())
        if used >= limit:
            raise BudgetExceededError(
                f"今日请求配额已用尽：{used}/{limit}",
                hint="等待次日重置，或调整 [budget].daily_request_limit",
            )

    def _ensure_client(self) -> DeepSeekClient:
        """按需创建客户端（没有 Key 时才报错，不影响插件加载）。"""

        if self.client is not None:
            return self.client

        key, source = resolve_api_key(
            str(getattr(self.config.api, "api_key", "") or ""),
            model_config_path=str(
                getattr(self.config.api, "model_config_path", "") or "config/model.toml"
            ),
            provider_name=str(getattr(self.config.api, "key_provider", "") or ""),
        )
        if not key:
            raise MissingAPIKeyError("未找到 DeepSeek API Key")
        self._api_key_source = source
        self.client = DeepSeekClient(
            api_key=key,
            base_url=str(getattr(self.config.api, "base_url", "") or ""),
            timeout=float(getattr(self.config.api, "timeout_seconds", 60) or 60),
            max_retries=int(getattr(self.config.api, "max_retries", 2) or 0),
            use_proxy=bool(getattr(self.config.api, "use_proxy", True)),
            proxy=str(getattr(self.config.api, "proxy", "") or ""),
            verify_ssl=bool(getattr(self.config.api, "verify_ssl", True)),
            logger=self.logger,
        )
        self._info(f"DeepSeek 搜索客户端已就绪（Key 来源：{source}）")
        return self.client

    def _resolve_count(self, count: int) -> int:
        """把请求条数夹到配置允许的区间。"""

        configured_max = int(getattr(self.config.search, "max_count", 20) or 20)
        upper = max(1, min(50, configured_max))
        configured_default = int(getattr(self.config.search, "default_count", 8) or 8)
        default = max(1, min(upper, configured_default))
        try:
            value = int(count or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return default
        return max(1, min(value, upper))

    @property
    def _cache_enabled(self) -> bool:
        """缓存开关。"""

        return bool(getattr(self.config.cache, "enabled", True))

    def _price_per_1000(self) -> float:
        """每千次请求单价（元）。"""

        try:
            return float(getattr(self.config.budget, "price_per_1000", 5.0))
        except (TypeError, ValueError):
            return 5.0

    def _price_per_request(self) -> float:
        """单次请求费用（元），保留 6 位小数避免浮点噪音。"""

        return round(self._price_per_1000() / 1000.0, 6)

    def _persist_enabled(self) -> bool:
        """是否启用用量落盘。"""

        return bool(getattr(self.config.budget, "persist_usage", True)) and self.store is not None

    async def _persist(self, *, force: bool = False) -> None:
        """按最小间隔把用量写入存储。"""

        if not self._persist_enabled():
            return
        now = self._clock()
        if not force and (now - self._last_persist) < PERSIST_INTERVAL:
            return
        self._last_persist = now
        try:
            await self.store.save(self.usage.to_dict())
        except Exception as exc:  # 落盘失败不影响主流程
            self._warn(f"用量落盘失败：{exc}")

    def _info(self, message: str) -> None:
        """安全地写一条 info 日志。"""

        if self.logger is not None:
            try:
                self.logger.info(message)
            except Exception:  # pragma: no cover
                pass

    def _warn(self, message: str) -> None:
        """安全地写一条 warning 日志。"""

        if self.logger is not None:
            try:
                self.logger.warning(message)
            except Exception:  # pragma: no cover
                pass


def _compress_key(model: str, target: int, text: str) -> str:
    """压缩缓存键：模型 + 目标长度 + 正文内容。"""

    raw = f"{model}|{int(target)}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(kind: str, **fields: Any) -> str:
    """根据请求内容生成稳定的缓存键。"""

    payload = {"kind": kind, **fields}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
