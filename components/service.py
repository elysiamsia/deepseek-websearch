"""跨插件服务组件。

其他插件可以通过签名 deepseek_websearch:service:websearch 拿到本服务：

    from src.app.plugin_system.api.service_api import get_service

    service = get_service("deepseek_websearch:service:websearch")
    data = await service.search("DeepSeek V4 发布")
    if data["ok"]:
        for item in data["results"]:
            print(item["title"], item["url"])

为了不把异常耦合给调用方，本服务的方法一律不抛异常，而是返回带 ok 字段的字典。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from ..core.errors import DeepSeekError
from .common import get_runtime

logger = get_logger("deepseek_websearch")


class DeepSeekWebSearchService(BaseService):
    """DeepSeek 官方联网搜索服务。"""

    name = "websearch"
    description = "把 DeepSeek 官方 Search / Fetch 能力暴露给其他插件（结构化返回，不抛异常）"
    version = "1.0.0"

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
    ) -> dict[str, Any]:
        """搜索网页。

        Returns:
            {"ok": True, "query", "count", "cached", "elapsed_ms", "cost_cny", "results"}
            或 {"ok": False, "error", "hint", "error_type"}
        """

        try:
            runtime = await get_runtime(self)
            outcome = await runtime.search(
                query,
                count=count,
                need_content=need_content,
                site=site,
                exclude_site=exclude_site,
                lang=lang,
                after=after,
                before=before,
                ori_question=ori_question,
                use_cache=use_cache,
            )
        except DeepSeekError as exc:
            logger.warning(f"service.search 失败：{exc.describe()}")
            return {
                "ok": False,
                "error": exc.message,
                "hint": exc.hint,
                "error_type": type(exc).__name__,
            }

        return {
            "ok": True,
            "query": outcome.query,
            "count": outcome.count,
            "cached": outcome.cached,
            "elapsed_ms": outcome.elapsed_ms,
            "cost_cny": outcome.cost_cny,
            "today_requests": outcome.today_requests,
            "results": [item.to_dict() for item in outcome.results],
        }

    async def fetch(
        self,
        url: str,
        *,
        max_chars: int = 0,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """抓取网页正文。

        Returns:
            {"ok": True, "url", "title", "content", "content_chars", "cached", "elapsed_ms"}
            或 {"ok": False, "error", "hint", "error_type"}
        """

        try:
            runtime = await get_runtime(self)
            outcome = await runtime.fetch(url, max_chars=max_chars, use_cache=use_cache)
        except DeepSeekError as exc:
            logger.warning(f"service.fetch 失败：{exc.describe()}")
            return {
                "ok": False,
                "error": exc.message,
                "hint": exc.hint,
                "error_type": type(exc).__name__,
            }

        content = outcome.content
        if max_chars and max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars]
        return {
            "ok": True,
            "url": outcome.url,
            "title": outcome.title,
            "content": content,
            "content_chars": outcome.content_chars,
            "cached": outcome.cached,
            "elapsed_ms": outcome.elapsed_ms,
            "cost_cny": outcome.cost_cny,
        }

    async def usage(self) -> dict[str, Any]:
        """返回用量与费用快照。"""

        try:
            runtime = await get_runtime(self)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "usage": runtime.usage_snapshot()}

    async def cache_stats(self) -> dict[str, Any]:
        """返回缓存统计。"""

        try:
            runtime = await get_runtime(self)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "cache": runtime.cache_stats()}

    async def clear_cache(self) -> dict[str, Any]:
        """清空缓存。"""

        try:
            runtime = await get_runtime(self)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        removed = await runtime.clear_cache()
        return {"ok": True, "removed": removed}
