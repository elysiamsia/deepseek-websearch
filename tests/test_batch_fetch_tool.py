"""批量抓取：deepseek_fetch 多 URL 与 deepseek_search 的 fetch_top。"""

from __future__ import annotations

import unittest

from tests import framework_stub
from tests.helpers import FakePlugin, build_runtime

tools_mod = framework_stub.import_component("components.tools")
models = framework_stub.import_component("core.models")
errors = framework_stub.import_component("core.errors")


class FakeBatchItem:
    """BatchFetchItem 的测试替身，只实现组件真正用到的属性。"""

    def __init__(self, url, outcome=None, error=None) -> None:
        self.url = url
        self.outcome = outcome
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None and self.outcome is not None

    @property
    def title(self) -> str:
        return self.outcome.title if self.outcome is not None else self.url


class StubRuntime:
    """只提供 get_runtime 需要的接口，不触发任何真实网络请求。"""

    def __init__(self, config, *, results=(), failing=()) -> None:
        self.config = config
        self.results = tuple(results)
        self.failing = set(failing)
        self.fetch_many_calls: list[list[str]] = []
        self.fetch_calls: list[str] = []
        self.search_calls: list[dict] = []

    async def fetch(self, url):
        self.fetch_calls.append(url)
        return models.FetchOutcome(url=url, title="单条标题", content="正文内容")

    async def fetch_many(
        self, urls, *, max_chars=0, concurrency=0, use_cache=True
    ):
        self.fetch_many_calls.append(list(urls))
        items = []
        for url in urls:
            if url in self.failing:
                items.append(FakeBatchItem(url, error=errors.TransportError("网络错误")))
            else:
                items.append(
                    FakeBatchItem(
                        url,
                        outcome=models.FetchOutcome(
                            url=url, title=f"标题-{url}", content="正文内容"
                        ),
                    )
                )
        return items

    async def search(self, query, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        return models.SearchOutcome(
            query=query, results=self.results, elapsed_ms=5
        )


def make_config():
    """借真实 Runtime 的配置对象，保证字段与线上一致。"""

    runtime, _, _, _ = build_runtime()
    return runtime.config


def make_results(count: int):
    return tuple(
        models.SearchResult(
            title=f"标题{i}",
            url=f"https://example.com/{i}",
            summary="摘要",
        )
        for i in range(count)
    )


class BatchFetchToolTests(unittest.IsolatedAsyncioTestCase):
    """deepseek_fetch 的批量分支。"""

    async def test_merges_and_dedupes_keeping_order(self) -> None:
        runtime = StubRuntime(make_config())
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute(
            url="https://a.com",
            urls=["https://b.com", "https://a.com", "https://c.com"],
        )

        self.assertTrue(ok)
        self.assertEqual(
            runtime.fetch_many_calls, [["https://a.com", "https://b.com", "https://c.com"]]
        )
        self.assertIn("## 1. 标题-https://a.com", text)
        self.assertIn("## 3. 标题-https://c.com", text)
        self.assertIn("链接：https://b.com", text)

    async def test_batch_size_capped_by_config(self) -> None:
        config = make_config()
        config.fetch.max_batch = 2
        runtime = StubRuntime(config)
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute(
            urls=["https://a.com", "https://b.com", "https://c.com"]
        )

        self.assertTrue(ok)
        self.assertEqual(runtime.fetch_many_calls, [["https://a.com", "https://b.com"]])
        self.assertIn("## 2.", text)
        self.assertNotIn("## 3.", text)

    async def test_empty_urls_report_error(self) -> None:
        runtime = StubRuntime(make_config())
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute()

        self.assertFalse(ok)
        self.assertIn("请给出要抓取的链接", text)
        self.assertEqual(runtime.fetch_many_calls, [])

    async def test_failed_item_is_isolated(self) -> None:
        runtime = StubRuntime(make_config(), failing={"https://bad.com"})
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute(urls=["https://good.com", "https://bad.com"])

        self.assertTrue(ok)
        self.assertIn("正文内容", text)
        self.assertIn("## 2.", text)
        self.assertIn("抓取失败：网络错误", text)

    async def test_all_failed_returns_readable_summary(self) -> None:
        runtime = StubRuntime(
            make_config(), failing={"https://a.com", "https://b.com"}
        )
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute(urls=["https://a.com", "https://b.com"])

        self.assertFalse(ok)
        self.assertIn("全部抓取失败", text)
        self.assertIn("https://a.com：网络错误", text)
        self.assertIn("https://b.com：网络错误", text)

    async def test_single_url_goes_through_batch_when_available(self) -> None:
        runtime = StubRuntime(make_config())
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute("https://a.com")

        self.assertTrue(ok)
        self.assertEqual(runtime.fetch_many_calls, [["https://a.com"]])
        self.assertEqual(runtime.fetch_calls, [])
        self.assertIn("正文内容", text)


class SearchFetchTopTests(unittest.IsolatedAsyncioTestCase):
    """deepseek_search 的 fetch_top 分支。"""

    async def test_fetch_top_appends_content_section(self) -> None:
        runtime = StubRuntime(make_config(), results=make_results(3))
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))

        ok, text = await tool.execute("q", fetch_top=2)

        self.assertTrue(ok)
        self.assertEqual(
            runtime.fetch_many_calls,
            [["https://example.com/0", "https://example.com/1"]],
        )
        self.assertIn("## 正文（前 2 条）", text)
        self.assertIn("正文内容", text)

    async def test_fetch_top_zero_skips_fetching(self) -> None:
        runtime = StubRuntime(make_config(), results=make_results(3))
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))

        ok, text = await tool.execute("q")

        self.assertTrue(ok)
        self.assertEqual(runtime.fetch_many_calls, [])
        self.assertNotIn("## 正文", text)

    async def test_fetch_top_defaults_from_config(self) -> None:
        config = make_config()
        config.search.fetch_top = 1
        runtime = StubRuntime(config, results=make_results(3))
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))

        await tool.execute("q")

        self.assertEqual(
            runtime.fetch_many_calls, [["https://example.com/0"]]
        )

    async def test_fetch_top_clamped_to_five(self) -> None:
        runtime = StubRuntime(make_config(), results=make_results(8))
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))

        ok, text = await tool.execute("q", fetch_top=99)

        self.assertTrue(ok)
        self.assertEqual(len(runtime.fetch_many_calls[0]), 5)
        self.assertIn("## 正文（前 5 条）", text)


if __name__ == "__main__":
    unittest.main()
