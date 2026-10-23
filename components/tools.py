"""给 LLM 调用的联网检索工具。

两个 Tool 都只做「查询」：

- deepseek_search：调用 DeepSeek 官方 /v1/tools/search
- deepseek_fetch：调用 DeepSeek 官方 /v1/tools/fetch

按 Neo-MoFox 组件规范，Tool 用于返回信息，不承担副作用。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseTool

from ..core.errors import DeepSeekError
from ..core.formatting import format_error, format_fetch_markdown, format_search_markdown
from .common import extract_trigger_text, get_runtime

logger = get_logger("deepseek_websearch")


class DeepSeekSearchTool(BaseTool):
    """使用 DeepSeek 官方搜索引擎联网检索。"""

    name = "deepseek_search"
    description = (
        "用 DeepSeek 官方搜索引擎联网检索最新网页信息，并按相关度返回标题、链接、站点、"
        "日期、权威度与长摘要。适合查新闻、文档、版本发布、价格、事件进展，或核实事实。"
        "支持 site / exclude_site / lang / after / before 结构化过滤；"
        "需要读全文时把 need_content 设为 true。不用于闲聊、识图或代替对话。"
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

            text = format_search_markdown(
                outcome,
                max_summary_chars=int(getattr(config.search, "max_summary_chars", 700)),
                max_content_chars=int(getattr(config.search, "max_content_chars", 3000)),
                show_meta=bool(getattr(config.search, "show_meta", True)),
                include_notice=bool(
                    getattr(config.search, "include_untrusted_notice", True)
                ),
            )
            return True, text
        except DeepSeekError as exc:
            logger.warning(f"deepseek_search 失败：{exc.describe()}")
            return False, format_error(exc)
        except Exception as exc:  # 兜底：绝不让异常冒泡打断对话
            logger.error(f"deepseek_search 未预期异常：{exc!r}")
            return False, f"❌ 搜索失败：{exc}"


class DeepSeekFetchTool(BaseTool):
    """使用 DeepSeek 官方抓取服务读取网页正文。"""

    name = "deepseek_fetch"
    description = (
        "用 DeepSeek 官方抓取服务把一个网页转换成 Markdown 正文（含标题）。"
        "当用户给出链接、或搜索结果摘要不足以回答问题需要读全文时使用。"
        "纯 JS 渲染的站点可能抓不到正文，此时应改用 deepseek_search 找同话题的其他来源。"
    )

    async def execute(
        self,
        url: Annotated[str, "要抓取的网页链接，必须是完整的 http:// 或 https:// 地址"],
        max_chars: Annotated[int, "正文最多返回多少字符，0 表示使用配置默认值"] = 0,
    ) -> tuple[bool, str]:
        """抓取网页正文。

        Args:
            url: 目标链接。
            max_chars: 正文截断上限，0 表示使用配置默认值。

        Returns:
            (是否成功, 渲染好的 Markdown 正文或错误说明)
        """

        try:
            runtime = await get_runtime(self)
            config = runtime.config
            limit = int(max_chars or 0)
            if limit <= 0:
                limit = int(getattr(config.fetch, "max_chars", 12000))

            outcome = await runtime.fetch(url, max_chars=limit)

            if bool(getattr(config.plugin, "debug_log", False)):
                logger.info(
                    f"deepseek_fetch：url={outcome.url}，{outcome.content_chars} 字符，"
                    f"耗时 {outcome.elapsed_ms}ms，缓存={outcome.cached}"
                )

            text = format_fetch_markdown(
                outcome,
                max_chars=limit,
                include_notice=bool(
                    getattr(config.search, "include_untrusted_notice", True)
                ),
            )
            return True, text
        except DeepSeekError as exc:
            logger.warning(f"deepseek_fetch 失败：{exc.describe()}")
            return False, format_error(exc)
        except Exception as exc:  # 兜底
            logger.error(f"deepseek_fetch 未预期异常：{exc!r}")
            return False, f"❌ 抓取失败：{exc}"
