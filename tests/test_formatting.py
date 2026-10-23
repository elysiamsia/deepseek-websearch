"""结果格式化测试。"""

from __future__ import annotations

import unittest

from tests import framework_stub

fmt = framework_stub.import_component("core.formatting")
models = framework_stub.import_component("core.models")


def make_result(**kwargs):
    defaults = dict(
        title="示例标题",
        url="https://example.com/a",
        site_name="示例站",
        date="2026-08-27T16:00:00+00:00",
        summary="这是一段摘要",
        snippet="短摘要",
        content="",
        authority=3,
        rerank_score=2.1,
    )
    defaults.update(kwargs)
    return models.SearchResult(**defaults)


class SearchFormattingTests(unittest.TestCase):
    """搜索结果渲染。"""

    def test_markdown_contains_core_fields(self) -> None:
        outcome = models.SearchOutcome(
            query="测试", results=(make_result(),), elapsed_ms=420, cached=False
        )
        text = fmt.format_search_markdown(outcome)
        self.assertIn("测试", text)
        self.assertIn("示例标题", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn("权威度 3/4", text)
        self.assertIn("2026-08-27", text)
        self.assertIn("0.42s", text)
        self.assertIn(fmt.UNTRUSTED_NOTICE, text)

    def test_summary_truncated(self) -> None:
        outcome = models.SearchOutcome(
            query="q", results=(make_result(summary="长" * 500),), elapsed_ms=1
        )
        text = fmt.format_search_markdown(outcome, max_summary_chars=50)
        self.assertIn("…", text)
        self.assertLess(len(text), 1000)

    def test_dedupe(self) -> None:
        item = make_result()
        outcome = models.SearchOutcome(query="q", results=(item, item), elapsed_ms=1)
        text = fmt.format_search_markdown(outcome)
        self.assertEqual(text.count("https://example.com/a"), 1)

    def test_empty_result(self) -> None:
        outcome = models.SearchOutcome(query="空查询", results=(), elapsed_ms=1)
        text = fmt.format_search_markdown(outcome)
        self.assertIn("没有找到", text)

    def test_cache_flag(self) -> None:
        outcome = models.SearchOutcome(
            query="q", results=(make_result(),), elapsed_ms=0, cached=True
        )
        self.assertIn("缓存命中", fmt.format_search_markdown(outcome))

    def test_notice_can_be_disabled(self) -> None:
        outcome = models.SearchOutcome(query="q", results=(make_result(),), elapsed_ms=1)
        text = fmt.format_search_markdown(outcome, include_notice=False)
        self.assertNotIn(fmt.UNTRUSTED_NOTICE, text)

    def test_plain_text(self) -> None:
        outcome = models.SearchOutcome(query="q", results=(make_result(),), elapsed_ms=1)
        text = fmt.format_search_text(outcome, max_items=1)
        self.assertIn("🔍", text)
        self.assertIn("示例标题", text)
        self.assertNotIn("**", text)


class FetchFormattingTests(unittest.TestCase):
    """抓取结果渲染。"""

    def test_truncation_notice(self) -> None:
        outcome = models.FetchOutcome(
            url="https://example.com", title="标题", content="正" * 100
        )
        text = fmt.format_fetch_markdown(outcome, max_chars=20)
        self.assertIn("已截断", text)
        self.assertIn(fmt.UNTRUSTED_NOTICE, text)

    def test_plain_text(self) -> None:
        outcome = models.FetchOutcome(
            url="https://example.com", title="标题", content="正文"
        )
        text = fmt.format_fetch_text(outcome, max_chars=100)
        self.assertIn("📄 标题", text)


class UsageFormattingTests(unittest.TestCase):
    """用量文本。"""

    def test_usage_text(self) -> None:
        text = fmt.format_usage_text(
            {
                "total_requests": 12,
                "total_search": 10,
                "total_fetch": 2,
                "today_requests": 3,
                "daily_limit": 100,
                "total_failed": 1,
                "total_empty": 2,
                "cache_hits": 5,
                "cost_cny": 0.06,
                "today_cost_cny": 0.015,
                "first_seen": "2026-10-01T10:00:00",
                "last_seen": "2026-10-23T21:00:00",
            }
        )
        self.assertIn("3/100", text)
        self.assertIn("¥0.060", text)
        self.assertIn("缓存命中：5", text)

    def test_error_text(self) -> None:
        error = framework_stub.import_component("core.errors").AuthError("HTTP 401: bad key")
        text = fmt.format_error(error)
        self.assertTrue(text.startswith("❌"))
        self.assertIn("api_key", text)


if __name__ == "__main__":
    unittest.main()
