"""用量与费用统计。

DeepSeek 官方搜索按「成功请求」计费：每 1000 次 5 元，失败与无结果请求不收费。
本模块只维护计数与费用估算，不负责落盘（持久化由 Runtime 通过 storage_api 完成）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_KINDS = ("search", "fetch")
_DAY_FORMAT = "%Y-%m-%d"


@dataclass
class UsageTracker:
    """累计用量与按天配额统计。"""

    total_search: int = 0
    total_fetch: int = 0
    total_failed: int = 0
    total_empty: int = 0
    cache_hits: int = 0
    days: dict[str, dict[str, int]] = field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""

    # -- 记录 ---------------------------------------------------------------

    def record_success(self, kind: str, now: datetime) -> None:
        """记录一次成功的计费请求。"""

        if kind not in _KINDS:
            raise ValueError(f"未知的请求类型：{kind}")
        day = now.strftime(_DAY_FORMAT)
        bucket = self.days.setdefault(day, {"search": 0, "fetch": 0})
        bucket[kind] = bucket.get(kind, 0) + 1
        if kind == "search":
            self.total_search += 1
        else:
            self.total_fetch += 1
        stamp = now.isoformat(timespec="seconds")
        if not self.first_seen:
            self.first_seen = stamp
        self.last_seen = stamp

    def record_failure(self, now: datetime) -> None:
        """记录一次失败请求（不产生费用）。"""

        self.total_failed += 1
        self.last_seen = now.isoformat(timespec="seconds")

    def record_empty(self, now: datetime) -> None:
        """记录一次「请求成功但没有结果」（上游不计费）。"""

        self.total_empty += 1
        self.last_seen = now.isoformat(timespec="seconds")

    def record_cache_hit(self) -> None:
        """记录一次缓存命中（不产生费用）。"""

        self.cache_hits += 1

    # -- 查询 ---------------------------------------------------------------

    @property
    def total_requests(self) -> int:
        """成功请求总数。"""

        return self.total_search + self.total_fetch

    def count_on(self, day: str) -> int:
        """某一天（YYYY-MM-DD）的成功请求数。"""

        bucket = self.days.get(day) or {}
        return int(bucket.get("search", 0)) + int(bucket.get("fetch", 0))

    def today_count(self, now: datetime) -> int:
        """今天的成功请求数。"""

        return self.count_on(now.strftime(_DAY_FORMAT))

    def cost_cny(self, price_per_1000: float = 5.0, day: str | None = None) -> float:
        """按每千次单价估算费用。"""

        count = self.total_requests if day is None else self.count_on(day)
        return round(count * float(price_per_1000) / 1000.0, 4)

    def prune(self, now: datetime, keep_days: int = 60) -> None:
        """只保留最近 keep_days 天的按天统计。"""

        if keep_days <= 0 or not self.days:
            return
        keys = sorted(self.days)
        for key in keys[:-keep_days]:
            self.days.pop(key, None)

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """导出为可 JSON 化的字典。"""

        return {
            "total_search": self.total_search,
            "total_fetch": self.total_fetch,
            "total_failed": self.total_failed,
            "total_empty": self.total_empty,
            "cache_hits": self.cache_hits,
            "days": {day: dict(bucket) for day, bucket in self.days.items()},
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "UsageTracker":
        """从字典恢复；脏数据一律降级为默认值，避免插件因历史文件起不来。"""

        if not isinstance(data, dict):
            return cls()

        tracker = cls()
        tracker.total_search = _safe_int(data.get("total_search"))
        tracker.total_fetch = _safe_int(data.get("total_fetch"))
        tracker.total_failed = _safe_int(data.get("total_failed"))
        tracker.total_empty = _safe_int(data.get("total_empty"))
        tracker.cache_hits = _safe_int(data.get("cache_hits"))
        tracker.first_seen = str(data.get("first_seen") or "")
        tracker.last_seen = str(data.get("last_seen") or "")

        raw_days = data.get("days")
        if isinstance(raw_days, dict):
            for day, bucket in raw_days.items():
                if not isinstance(day, str) or not isinstance(bucket, dict):
                    continue
                tracker.days[day] = {
                    "search": _safe_int(bucket.get("search")),
                    "fetch": _safe_int(bucket.get("fetch")),
                }
        return tracker

    def snapshot(self, now: datetime, price_per_1000: float = 5.0, daily_limit: int = 0) -> dict[str, Any]:
        """生成给命令 / 服务展示的统计快照。"""

        today = self.today_count(now)
        return {
            "total_search": self.total_search,
            "total_fetch": self.total_fetch,
            "total_requests": self.total_requests,
            "total_failed": self.total_failed,
            "total_empty": self.total_empty,
            "cache_hits": self.cache_hits,
            "today_requests": today,
            "daily_limit": daily_limit,
            "cost_cny": self.cost_cny(price_per_1000),
            "today_cost_cny": round(today * float(price_per_1000) / 1000.0, 4),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


def _safe_int(value: Any) -> int:
    """把任意值安全转成非负整数。"""

    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, number)
