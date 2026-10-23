"""渲染层：把「压缩 / 截断」和「格式化」串成一条链。

三条使用路径（LLM 工具、/ds 命令、自然语言注入）都走这里，保证同一份配置
在三条路径上语义一致：

1. 正文先过压缩器（关闭时它只做截断）；
2. 用 max_content_chars / max_chars = 0 交给格式化器，避免二次截断；
3. 把「已压缩 / 已截断」的说明作为脚注拼在结果后面。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..core.formatting import (
    format_fetch_markdown,
    format_fetch_text,
    format_search_markdown,
    format_search_text,
    truncate_text,
)
from ..core.models import FetchOutcome, SearchOutcome


async def prepare_search(
    runtime: Any, outcome: SearchOutcome
) -> tuple[SearchOutcome, str]:
    """按配置处理搜索结果里的正文。

    Returns:
        (新 outcome, 汇总说明)。说明为空时表示无需附加脚注。
    """

    config = runtime.config
    fallback = int(getattr(config.search, "max_content_chars", 3000) or 0)
    # 两个开关同时成立才压缩：全局压缩开关 + 作用于搜索正文
    use_compress = bool(getattr(runtime, "compressor_enabled", False)) and bool(
        getattr(config.compress, "apply_to_search_content", True)
    )
    item_limit = (
        int(getattr(config.compress, "search_item_limit", 3) or 0) if use_compress else 0
    )

    used = 0
    compressed_items = 0
    original_total = 0
    items = []

    for item in outcome.results:
        content = item.content
        if not content.strip():
            items.append(item)
            continue

        if not use_compress:
            text = truncate_text(content, fallback) if fallback else content
            items.append(replace(item, content=text))
            continue

        if item_limit and used >= item_limit:
            text = truncate_text(content, fallback) if fallback else content
            items.append(replace(item, content=text))
            continue

        shrunk = await runtime.shrink_text(content, fallback_chars=fallback, kind="search")
        used += 1
        if shrunk.method == "llm":
            compressed_items += 1
            original_total += shrunk.original_chars
        items.append(replace(item, content=shrunk.text))

    note = ""
    if compressed_items:
        note = f"> 正文已由小模型压缩：{compressed_items} 条，原始共 {original_total} 字符"
    return replace(outcome, results=tuple(items)), note


async def prepare_fetch(
    runtime: Any, outcome: FetchOutcome, *, fallback_chars: int
) -> tuple[FetchOutcome, str]:
    """按配置处理抓取到的正文。

    Args:
        runtime: 运行时。
        outcome: 抓取结果。
        fallback_chars: 压缩不可用时按多少字符截断（0 表示不截断）。

    Returns:
        (新 outcome, 说明)。
    """

    config = runtime.config
    apply = bool(getattr(config.compress, "apply_to_fetch", True))
    if not (getattr(runtime, "compressor_enabled", False) and apply):
        text = (
            truncate_text(outcome.content, fallback_chars)
            if fallback_chars
            else outcome.content
        )
        note = ""
        if fallback_chars and len(outcome.content) > fallback_chars:
            note = f"> 正文共 {len(outcome.content)} 字符，已截断到 {fallback_chars} 字符"
        return replace(outcome, content=text), note

    shrunk = await runtime.shrink_text(
        outcome.content, fallback_chars=fallback_chars, kind="fetch"
    )
    if shrunk.method == "llm":
        note = (
            f"> 正文 {shrunk.original_chars} 字符，"
            f"已由小模型压缩到 {len(shrunk.text)} 字符"
        )
    elif shrunk.method == "truncate":
        note = (
            f"> 正文共 {shrunk.original_chars} 字符，压缩未生效，"
            f"已截断到 {fallback_chars} 字符"
        )
    else:
        note = ""
    return replace(outcome, content=shrunk.text), note


# ---------------------------------------------------------------------------
# 四条渲染入口
# ---------------------------------------------------------------------------


async def render_search_markdown(runtime: Any, outcome: SearchOutcome) -> str:
    """搜索结果 → 给 LLM 的 Markdown。"""

    config = runtime.config
    prepared, note = await prepare_search(runtime, outcome)
    text = format_search_markdown(
        prepared,
        max_summary_chars=int(getattr(config.search, "max_summary_chars", 700)),
        max_content_chars=0,
        show_meta=bool(getattr(config.search, "show_meta", True)),
        include_notice=bool(getattr(config.search, "include_untrusted_notice", True)),
    )
    return f"{text}\n\n{note}" if note else text


def render_search_text(runtime: Any, outcome: SearchOutcome) -> str:
    """搜索结果 → 给 /ds 命令的纯文本（命令不展示正文，因此无需压缩）。"""

    config = runtime.config
    return format_search_text(
        outcome,
        max_summary_chars=140,
        max_items=int(getattr(config.search, "max_items_in_command", 6)),
    )


async def render_fetch_markdown(
    runtime: Any, outcome: FetchOutcome, *, fallback_chars: int
) -> str:
    """抓取结果 → 给 LLM 的 Markdown。"""

    config = runtime.config
    prepared, note = await prepare_fetch(runtime, outcome, fallback_chars=fallback_chars)
    text = format_fetch_markdown(
        prepared,
        max_chars=0,
        include_notice=bool(getattr(config.search, "include_untrusted_notice", True)),
    )
    return f"{text}\n\n{note}" if note else text


async def render_fetch_text(
    runtime: Any, outcome: FetchOutcome, *, fallback_chars: int
) -> str:
    """抓取结果 → 给 /ds 命令的纯文本。"""

    prepared, note = await prepare_fetch(runtime, outcome, fallback_chars=fallback_chars)
    text = format_fetch_text(prepared, max_chars=0)
    return f"{text}\n\n{note}" if note else text
