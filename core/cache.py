"""带 TTL 的 LRU 缓存。

只做内存缓存、不做 I/O：同一条 query 在 TTL 内重复出现时直接复用结果，
省掉一次真实计费请求（DeepSeek 搜索按「成功请求」计费，每 1000 次 5 元）。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class _Entry:
    """缓存条目。"""

    value: Any
    expires_at: float
    created_at: float


class TTLCache:
    """线程内安全的 TTL + LRU 缓存（asyncio 单线程下无需加锁）。

    Args:
        max_entries: 最多保留多少条，超出时淘汰最久未使用的条目。
        ttl_seconds: 条目存活秒数。
        clock: 单调时钟，测试时可注入。
    """

    def __init__(
        self,
        max_entries: int = 128,
        ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expired = 0

    def get(self, key: str) -> Any | None:
        """取缓存；未命中或已过期返回 None。"""

        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            self.expired += 1
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, *, ttl_seconds: float | None = None) -> None:
        """写入缓存，必要时淘汰旧条目。"""

        ttl = self.ttl_seconds if ttl_seconds is None else max(0.0, float(ttl_seconds))
        now = self._clock()
        # ttl <= 0 表示永不过期（见配置项 cache.ttl_seconds 的说明）
        expires_at = now + ttl if ttl > 0 else float("inf")
        self._entries[key] = _Entry(value=value, expires_at=expires_at, created_at=now)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.evictions += 1

    def clear(self) -> int:
        """清空缓存并返回被清掉的条目数。"""

        removed = len(self._entries)
        self._entries.clear()
        return removed

    def prune(self) -> int:
        """主动清理已过期条目，返回清理数量。"""

        now = self._clock()
        stale = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in stale:
            self._entries.pop(key, None)
        self.expired += len(stale)
        return len(stale)

    @property
    def size(self) -> int:
        """当前条目数（含尚未被清理的过期条目）。"""

        return len(self._entries)

    def stats(self) -> dict[str, int | float]:
        """返回命中率等统计信息。"""

        lookups = self.hits + self.misses
        rate = (self.hits / lookups * 100.0) if lookups else 0.0
        return {
            "size": self.size,
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expired": self.expired,
            "hit_rate": round(rate, 2),
        }
