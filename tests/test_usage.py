"""UsageTracker 测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from tests import framework_stub

usage_mod = framework_stub.import_component("core.usage")


class UsageTrackerTests(unittest.TestCase):
    """计数、费用与序列化。"""

    def setUp(self) -> None:
        self.tracker = usage_mod.UsageTracker()
        self.now = datetime(2026, 10, 23, 21, 0, 0)

    def test_record_success(self) -> None:
        self.tracker.record_success("search", self.now)
        self.tracker.record_success("fetch", self.now)
        self.assertEqual(self.tracker.total_search, 1)
        self.assertEqual(self.tracker.total_fetch, 1)
        self.assertEqual(self.tracker.total_requests, 2)
        self.assertEqual(self.tracker.today_count(self.now), 2)

    def test_day_rollover(self) -> None:
        yesterday = self.now - timedelta(days=1)
        self.tracker.record_success("search", yesterday)
        self.tracker.record_success("search", self.now)
        self.assertEqual(self.tracker.today_count(self.now), 1)
        self.assertEqual(self.tracker.count_on(yesterday.strftime("%Y-%m-%d")), 1)

    def test_failures_and_empty_not_billed(self) -> None:
        self.tracker.record_failure(self.now)
        self.tracker.record_empty(self.now)
        self.assertEqual(self.tracker.total_requests, 0)
        self.assertEqual(self.tracker.today_count(self.now), 0)
        self.assertEqual(self.tracker.total_failed, 1)
        self.assertEqual(self.tracker.total_empty, 1)

    def test_cost(self) -> None:
        for _ in range(1000):
            self.tracker.record_success("search", self.now)
        self.assertAlmostEqual(self.tracker.cost_cny(5.0), 5.0)
        self.assertAlmostEqual(self.tracker.cost_cny(5.0, self.now.strftime("%Y-%m-%d")), 5.0)

    def test_roundtrip(self) -> None:
        self.tracker.record_success("search", self.now)
        self.tracker.record_cache_hit()
        restored = usage_mod.UsageTracker.from_dict(self.tracker.to_dict())
        self.assertEqual(restored.total_requests, 1)
        self.assertEqual(restored.cache_hits, 1)
        self.assertEqual(restored.today_count(self.now), 1)

    def test_from_dict_is_defensive(self) -> None:
        for garbage in (None, 42, "x", {"days": "oops"}, {"total_search": "abc"}):
            with self.subTest(garbage=garbage):
                restored = usage_mod.UsageTracker.from_dict(garbage)
                self.assertEqual(restored.total_requests, 0)

    def test_prune(self) -> None:
        for offset in range(100):
            self.tracker.record_success("search", self.now - timedelta(days=offset))
        self.tracker.prune(self.now, keep_days=10)
        self.assertEqual(len(self.tracker.days), 10)

    def test_snapshot(self) -> None:
        self.tracker.record_success("search", self.now)
        snapshot = self.tracker.snapshot(self.now, price_per_1000=5.0, daily_limit=10)
        self.assertEqual(snapshot["today_requests"], 1)
        self.assertEqual(snapshot["daily_limit"], 10)
        self.assertAlmostEqual(snapshot["today_cost_cny"], 0.005)


if __name__ == "__main__":
    unittest.main()
