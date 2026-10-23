"""搜索与抓取的数据模型。

全部使用不可变 dataclass，便于缓存与跨组件传递；from_api / to_dict
负责与 DeepSeek 原始 JSON 的双向转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _as_text(value: Any) -> str:
    """把任意上游字段安全转成字符串（None 转成空串）。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    """把上游字段安全转成浮点数。"""

    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = -1) -> int:
    """把上游字段安全转成整数。"""

    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class SearchResult:
    """单条搜索结果。"""

    title: str
    url: str
    site_name: str = ""
    date: str = ""
    summary: str = ""
    snippet: str = ""
    content: str = ""
    authority: int = -1
    rerank_score: float = 0.0
    site_icon: str = ""

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "SearchResult":
        """从 DeepSeek /v1/tools/search 的单项构造结果。"""

        return cls(
            title=_as_text(raw.get("title")).strip(),
            url=_as_text(raw.get("url")).strip(),
            site_name=_as_text(raw.get("site_name")).strip(),
            date=_as_text(raw.get("date")).strip(),
            summary=_as_text(raw.get("summary")).strip(),
            snippet=_as_text(raw.get("snippet")).strip(),
            content=_as_text(raw.get("content")),
            authority=_as_int(raw.get("authority")),
            rerank_score=_as_float(raw.get("rerank_score")),
            site_icon=_as_text(raw.get("site_icon")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为缓存用的普通字典。"""

        return {
            "title": self.title,
            "url": self.url,
            "site_name": self.site_name,
            "date": self.date,
            "summary": self.summary,
            "snippet": self.snippet,
            "content": self.content,
            "authority": self.authority,
            "rerank_score": self.rerank_score,
            "site_icon": self.site_icon,
        }

    def best_text(self) -> str:
        """返回最适合展示的摘要文本：summary 优先，其次 snippet。"""

        return self.summary or self.snippet


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """一次搜索的完整结果与计费信息。"""

    query: str
    results: tuple[SearchResult, ...] = field(default_factory=tuple)
    elapsed_ms: int = 0
    cached: bool = False
    cost_cny: float = 0.0
    today_requests: int = 0
    daily_limit: int = 0

    @property
    def count(self) -> int:
        """结果条数。"""

        return len(self.results)


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """一次网页抓取的完整结果。"""

    url: str
    title: str = ""
    content: str = ""
    elapsed_ms: int = 0
    cached: bool = False
    cost_cny: float = 0.0
    today_requests: int = 0
    daily_limit: int = 0

    @property
    def content_chars(self) -> int:
        """正文原始字符数。"""

        return len(self.content)
