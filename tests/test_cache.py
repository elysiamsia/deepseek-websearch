"""TTLCache 测试。"""

from __future__ import annotations

import unittest

from tests import framework_stub

cache_mod = framework_stub.import_component("core.cache")


class FakeClock:
    """可手动推进的单调时钟。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TTLCacheTests(unittest.TestCase):
    """缓存命中、过期与淘汰。"""

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.cache = cache_mod.TTLCache(max_entries=2, ttl_seconds=60, clock=self.clock)

    def test_hit_and_miss(self) -> None:
        self.assertIsNone(self.cache.get("k"))
        self.cache.set("k", {"v": 1})
        self.assertEqual(self.cache.get("k"), {"v": 1})

    def test_expiry(self) -> None:
        self.cache.set("k", "value")
        self.clock.advance(61)
        self.assertIsNone(self.cache.get("k"))
        self.assertEqual(self.cache.expired, 1)

    def test_lru_eviction(self) -> None:
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.get("a")  # a 变热
        self.cache.set("c", 3)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), 1)
        self.assertEqual(self.cache.get("c"), 3)
        self.assertEqual(self.cache.evictions, 1)

    def test_clear(self) -> None:
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.assertEqual(self.cache.clear(), 2)
        self.assertEqual(self.cache.size, 0)

    def test_stats(self) -> None:
        self.cache.set("a", 1)
        self.cache.get("a")
        self.cache.get("b")
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 50.0)

    def test_prune(self) -> None:
        self.cache.set("a", 1)
        self.clock.advance(120)
        self.assertEqual(self.cache.prune(), 1)

    def test_ttl_zero_never_expires(self) -> None:
        cache = cache_mod.TTLCache(max_entries=2, ttl_seconds=0, clock=self.clock)
        cache.set("k", "v")
        self.clock.advance(10_000_000)
        self.assertEqual(cache.get("k"), "v")


if __name__ == "__main__":
    unittest.main()
