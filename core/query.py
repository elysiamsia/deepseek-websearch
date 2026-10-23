"""查询词构造与校验。

DeepSeek 官方搜索支持在 query 里内联过滤语法：

- site:github.com / -site:example.com —— 限定 / 排除站点
- "精确短语" / -"排除短语"
- lang:zh,en —— 语言
- after:2025-01-01 / before:2026-06-01 —— 时间范围

本模块把这些语法包装成结构化的 site / exclude_site / lang / after / before
参数，让 LLM 不用背语法也能精确检索，同时避免重复追加用户已经写过的过滤词。
"""

from __future__ import annotations

import re

from .errors import InvalidArgumentError

#: DeepSeek 官方限制：query 长度 1~128 字符。
MAX_QUERY_CHARS = 128

_WHITESPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")
_LANG_RE = re.compile(r"^[a-zA-Z]{2,8}(?:-[a-zA-Z]{2,8})?$")


def normalize_query(text: str) -> str:
    """清洗查询词：去首尾空白、把连续空白折叠成单个空格。

    Args:
        text: 用户或 LLM 给出的查询词。

    Returns:
        清洗后的查询词。

    Raises:
        InvalidArgumentError: 查询词为空或超长。
    """

    if not isinstance(text, str):
        raise InvalidArgumentError("query 必须是字符串")

    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    if not cleaned:
        raise InvalidArgumentError("query 不能为空", hint="给出 1~128 个字符的检索关键词")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise InvalidArgumentError(
            f"query 过长：{len(cleaned)} 字符，DeepSeek 官方上限为 {MAX_QUERY_CHARS} 字符",
            hint="精简关键词后再试，过滤条件可以改用 site/lang/after/before 参数",
        )
    return cleaned


def normalize_site(value: str) -> str:
    """规范化站点过滤值：去掉协议前缀、路径与空白。

    Args:
        value: 例如 github.com 或 https://github.com/。

    Returns:
        纯域名；输入为空时返回空串。
    """

    token = _WHITESPACE_RE.sub("", value or "").strip()
    if not token:
        return ""
    token = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", token)
    token = token.split("/", 1)[0].strip(".")
    if not token:
        raise InvalidArgumentError(
            f"站点值无效：{value!r}",
            hint="填写域名即可，例如 github.com",
        )
    return token


def normalize_lang(value: str) -> str:
    """规范化语言过滤值，支持 zh / zh,en 这种多语言写法。"""

    raw = (value or "").strip()
    if not raw:
        return ""
    langs = [part.strip().lower() for part in raw.split(",") if part.strip()]
    for lang in langs:
        if not _LANG_RE.match(lang):
            raise InvalidArgumentError(
                f"语言代码无效：{lang!r}",
                hint="使用 ISO 639-1 代码，例如 zh、en、ja，多个用逗号分隔",
            )
    return ",".join(langs)


def normalize_date(value: str, field: str = "date") -> str:
    """校验并规范化日期过滤值。

    支持 2025、2025-06、2025-06-01 三种粒度。

    Args:
        value: 原始日期字符串。
        field: 字段名，用于错误提示。

    Returns:
        规范化后的日期字符串；输入为空时返回空串。
    """

    raw = (value or "").strip()
    if not raw:
        return ""

    match = _DATE_RE.match(raw)
    if not match:
        raise InvalidArgumentError(
            f"{field} 日期格式无效：{raw!r}",
            hint="支持 2025 / 2025-06 / 2025-06-01",
        )

    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    if not 1970 <= year <= 2999:
        raise InvalidArgumentError(
            f"{field} 年份超出范围：{year}",
            hint="使用 1970~2999 之间的年份",
        )
    if not 1 <= month <= 12:
        raise InvalidArgumentError(f"{field} 月份无效：{month}", hint="月份范围 01~12")
    if not 1 <= day <= 31:
        raise InvalidArgumentError(f"{field} 日期无效：{day}", hint="日期范围 01~31")

    parts = [f"{year:04d}"]
    if match.group(2):
        parts.append(f"{month:02d}")
    if match.group(3):
        parts.append(f"{day:02d}")
    return "-".join(parts)


def _contains_token(query: str, prefix: str) -> bool:
    """判断 query 里是否已经手写过某类过滤词。"""

    return re.search(rf"(?:^|\s){re.escape(prefix)}", query, flags=re.IGNORECASE) is not None


def build_query(
    query: str,
    *,
    site: str = "",
    exclude_site: str = "",
    lang: str = "",
    after: str = "",
    before: str = "",
) -> str:
    """把结构化过滤条件拼进 query。

    Args:
        query: 基础查询词。
        site: 只检索这些站点（空格分隔多个）。
        exclude_site: 排除这些站点（空格分隔多个）。
        lang: 语言代码，逗号分隔。
        after: 起始日期。
        before: 结束日期。

    Returns:
        拼装好的查询词（长度不超过 128）。

    Raises:
        InvalidArgumentError: 参数非法或拼装后超长。
    """

    base = normalize_query(query)
    parts: list[str] = [base]

    site_domains = [normalize_site(item) for item in (site or "").split() if item.strip()]
    if site_domains and not _contains_token(base, "site:"):
        parts.append(" ".join(f"site:{domain}" for domain in site_domains))

    exclude_domains = [
        normalize_site(item) for item in (exclude_site or "").split() if item.strip()
    ]
    if exclude_domains and not _contains_token(base, "-site:"):
        parts.append(" ".join(f"-site:{domain}" for domain in exclude_domains))

    langs = normalize_lang(lang)
    if langs and not _contains_token(base, "lang:"):
        parts.append(f"lang:{langs}")

    date_after = normalize_date(after, "after")
    if date_after and not _contains_token(base, "after:"):
        parts.append(f"after:{date_after}")

    date_before = normalize_date(before, "before")
    if date_before and not _contains_token(base, "before:"):
        parts.append(f"before:{date_before}")

    composed = " ".join(part for part in parts if part).strip()
    if len(composed) > MAX_QUERY_CHARS:
        raise InvalidArgumentError(
            f"加上过滤条件后 query 变成 {len(composed)} 字符，超过官方上限 {MAX_QUERY_CHARS}",
            hint="精简关键词，或减少过滤条件",
        )
    return composed
