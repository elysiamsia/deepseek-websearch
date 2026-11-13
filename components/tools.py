"""给 LLM 调用的联网检索工具。

两个 Tool 都只做「查询」：

- deepseek_search：调用 DeepSeek 官方 /v1/tools/search
- deepseek_fetch：调用 DeepSeek 官方 /v1/tools/fetch

按 Neo-MoFox 组件规范，Tool 用于返回信息，不承担副作用。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..core.errors import DeepSeekError
from ..core.formatting import format_error
from .common import extract_trigger_text, get_runtime
from .render import (
    render_fetch_batch_markdown,
    render_fetch_markdown,
    render_search_markdown,
)

logger = get_logger("deepseek_websearch")

#: 搜索后最多额外抓取几条正文。
MAX_FETCH_TOP = 5


def _merge_urls(url: str, urls: list[str] | None, *, limit: int) -> list[str]:
    """合并单个与批量链接：按出现顺序去重，再按上限截断。"""

    merged: list[str] = []
    seen: set[str] = set()
    for value in [url, *(urls or [])]:
        target = str(value or "").strip()
        if not target or target in seen:
            continue
        seen.add(target)
        merged.append(target)
    cap = int(limit or 0)
    return merged[:cap] if cap > 0 else merged


class DeepSeekSearchTool(BaseTool):
    """使用 DeepSeek 官方搜索引擎联网检索。"""

    name = "deepseek_search"
    description = (
        "用 DeepSeek 官方搜索引擎联网检索最新网页信息，并按相关度返回标题、链接、站点、"
        "日期、权威度与长摘要。适合查新闻、文档、版本发布、价格、事件进展，或核实事实。"
        "支持 site / exclude_site / lang / after / before 结构化过滤；"
        "需要读全文时把 need_content 设为 true，或用 fetch_top 并行抓取前 N 条正文。"
        "不用于闲聊、识图或代替对话。"
    )

    async def execute(
        self,
        query: Annotated[
            str,
            "检索关键词，1~128 字符。可以是自然语言问题或关键词组合；"
            "也支持内联语法：\"精确短语\"、-排除词、site:域名、lang:zh、after:2025-01-01",
        ],
        count: Annotated[int, "返回条数，1~20；填 0 表示使用插件配置的默认值"] = 0,
        need_content: Annotated[
            bool, "是否同时抓取每个网页的正文（更慢、更占上下文），默认 false"
        ] = False,
        fetch_top: Annotated[
            int,
            "搜索后额外并行抓取前 N 条结果的正文，0~5；填 0 表示使用插件配置的默认值",
        ] = 0,
        site: Annotated[str, "只检索这些站点，空格分隔多个，例如：github.com zhihu.com"] = "",
        exclude_site: Annotated[str, "排除这些站点，空格分隔多个，例如：pinterest.com"] = "",
        lang: Annotated[str, "限定语言，ISO 639-1 代码，逗号分隔，例如：zh 或 zh,en"] = "",
        after: Annotated[str, "只保留该日期之后的内容，格式：2025 或 2025-06-01"] = "",
        before: Annotated[str, "只保留该日期之前的内容，格式：2026 或 2026-12-31"] = "",
    ) -> tuple[bool, str]:
        """联网搜索网页。

        Args:
            query: 检索关键词。
            count: 返回条数，0 表示使用配置默认值。
            need_content: 是否同时返回网页正文。
            fetch_top: 搜索后额外抓取前 N 条正文，0 表示使用配置默认值。
            site: 限定站点。
            exclude_site: 排除站点。
            lang: 语言过滤。
            after: 起始日期。
            before: 结束日期。

        Returns:
            (是否成功, 渲染好的 Markdown 结果或错误说明)
        """

        try:
            runtime = await get_runtime(self)
            config = runtime.config
            ori_question = ""
            if bool(getattr(config.search, "auto_ori_question", True)):
                ori_question = extract_trigger_text(getattr(self, "trigger_message", None))

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
            )

            if bool(getattr(config.plugin, "debug_log", False)):
                logger.info(
                    f"deepseek_search：query={outcome.query!r}，"
                    f"得到 {outcome.count} 条，耗时 {outcome.elapsed_ms}ms，"
                    f"缓存={outcome.cached}"
                )

            text = await render_search_markdown(runtime, outcome)
            return True, await self._append_top_content(runtime, outcome, text, fetch_top)
        except DeepSeekError as exc:
            logger.warning(f"deepseek_search 失败：{exc.describe()}")
            return False, format_error(exc)
        except Exception as exc:  # 兜底：绝不让异常冒泡打断对话
            logger.error(f"deepseek_search 未预期异常：{exc!r}")
            return False, f"❌ 搜索失败：{exc}"

    async def _append_top_content(
        self, runtime: Any, outcome: Any, text: str, fetch_top: int
    ) -> str:
        """按 fetch_top 并行抓取前 N 条结果的正文，拼在搜索结果之后。"""

        config = runtime.config
        top = int(fetch_top or 0)
        if top <= 0:
            top = int(getattr(config.search, "fetch_top", 0) or 0)
        top = max(0, min(MAX_FETCH_TOP, top))
        urls = [item.url for item in outcome.results[:top] if item.url]

        fetch_many = getattr(runtime, "fetch_many", None)
        if not urls or fetch_many is None:
            return text

        items = await fetch_many(urls, max_chars=0)
        block = await render_fetch_batch_markdown(
            runtime,
            items,
            int(getattr(config.search, "max_content_chars", 3000) or 0),
        )
        return f"{text}\n\n## 正文（前 {len(urls)} 条）\n\n{block}"


class DeepSeekFetchTool(BaseTool):
    """使用 DeepSeek 官方抓取服务读取网页正文。"""

    name = "deepseek_fetch"
    description = (
        "用 DeepSeek 官方抓取服务把网页转换成 Markdown 正文（含标题）。"
        "当用户给出链接、或搜索结果摘要不足以回答问题需要读全文时使用。"
        "支持一次抓取多个链接（url 与 urls 合并去重，最多 5 个，并行抓取，"
        "单个失败不影响其余结果）。"
        "纯 JS 渲染的站点可能抓不到正文，此时应改用 deepseek_search 找同话题的其他来源。"
    )

    async def execute(
        self,
        url: Annotated[str, "要抓取的网页链接（单个）；批量抓取时可只填 urls"] = "",
        max_chars: Annotated[int, "正文最多返回多少字符，0 表示使用配置默认值"] = 0,
        urls: Annotated[
            list[str] | None,
            "批量抓取的链接列表，最多 5 个；与 url 合并后按出现顺序去重",
        ] = None,
    ) -> tuple[bool, str]:
        """抓取网页正文。

        Args:
            url: 单个目标链接，可与 urls 混用。
            max_chars: 正文截断上限，0 表示使用配置默认值。
            urls: 批量目标链接，上限由 [fetch].max_batch 决定（默认 5）。

        Returns:
            (是否成功, 渲染好的 Markdown 正文或错误说明)
        """

        try:
            runtime = await get_runtime(self)
            config = runtime.config
            limit = int(max_chars or 0)
            if limit <= 0:
                limit = int(getattr(config.fetch, "max_chars", 12000))

            targets = _merge_urls(
                url,
                urls,
                limit=int(getattr(config.fetch, "max_batch", 5) or 5),
            )
            if not targets:
                return False, "❌ 请给出要抓取的链接，例如：https://example.com"

            fetch_many = getattr(runtime, "fetch_many", None)
            if fetch_many is not None:
                items = await fetch_many(targets, max_chars=limit)
                if bool(getattr(config.plugin, "debug_log", False)):
                    logger.info(
                        f"deepseek_fetch：批量 {len(targets)} 个链接，"
                        f"{sum(1 for item in items if item.ok)} 个成功"
                    )
                text = await render_fetch_batch_markdown(runtime, items, limit)
                return any(item.ok for item in items), text

            # 运行时尚未提供批量接口时，单条链接保持原有单条抓取路径
            if len(targets) > 1:
                return False, "❌ 当前运行时不支持一次抓取多个链接，请逐个抓取"
            outcome = await runtime.fetch(targets[0])

            if bool(getattr(config.plugin, "debug_log", False)):
                logger.info(
                    f"deepseek_fetch：url={outcome.url}，{outcome.content_chars} 字符，"
                    f"耗时 {outcome.elapsed_ms}ms，缓存={outcome.cached}"
                )

            text = await render_fetch_markdown(runtime, outcome, fallback_chars=limit)
            return True, text
        except DeepSeekError as exc:
            logger.warning(f"deepseek_fetch 失败：{exc.describe()}")
            return False, format_error(exc)
        except Exception as exc:  # 兜底
            logger.error(f"deepseek_fetch 未预期异常：{exc!r}")
            return False, f"❌ 抓取失败：{exc}"
