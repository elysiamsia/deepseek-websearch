"""调用内并发：搜索正文压缩并行、批量抓取并发。"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from tests import framework_stub
from tests.helpers import FakePlugin, build_runtime

render = framework_stub.import_component("components.render")
tools_mod = framework_stub.import_component("components.tools")
models = framework_stub.import_component("core.models")


class SlowShrinkRuntime:
    """记录并发峰值、每条压缩固定耗时的假运行时。"""

    def __init__(self, config, *, delay: float = 0.1) -> None:
        self.config = config
        self.delay = delay
        self.compressor_enabled = True
        self.active = 0
        self.peak = 0

    async def shrink_text(self, text, *, fallback_chars=0, kind="fetch"):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        return SimpleNamespace(text="压缩正文", method="llm", original_chars=len(text))


class FakeBatchItem:
    """BatchFetchItem 的测试替身。"""

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


class BarrierRuntime:
    """fetch_many 用 Event 卡住，验证多个链接确实同时在跑。"""

    def __init__(self, config, *, expected: int = 2) -> None:
        self.config = config
        self.expected = expected
        self.active = 0
        self.peak = 0
        self.released = asyncio.Event()

    async def fetch_many(self, urls, *, max_chars=0, concurrency=0, use_cache=True):
        async def one(url):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active >= self.expected:
                self.released.set()
            try:
                await asyncio.wait_for(self.released.wait(), timeout=1)
            finally:
                self.active -= 1
            return FakeBatchItem(
                url,
                outcome=models.FetchOutcome(url=url, title=url, content="正文"),
            )

        return list(await asyncio.gather(*(one(url) for url in urls)))


def make_search_outcome(count: int, chars: int = 100):
    return models.SearchOutcome(
        query="q",
        results=tuple(
            models.SearchResult(
                title=f"标题{i}",
                url=f"https://example.com/{i}",
                content="正" * chars,
            )
            for i in range(count)
        ),
        elapsed_ms=5,
    )


class PrepareSearchConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """prepare_search 的多条正文压缩应并发执行。"""

    async def test_three_items_finish_in_parallel(self) -> None:
        base, _, _, _ = build_runtime(
            compress={"enabled": True, "search_item_limit": 3}
        )
        runtime = SlowShrinkRuntime(base.config, delay=0.1)

        started = time.perf_counter()
        prepared, note = await render.prepare_search(
            runtime, make_search_outcome(3)
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(runtime.peak, 3)
        self.assertLess(elapsed, 0.2)
        self.assertIn("已由小模型压缩：3 条", note)
        self.assertEqual(
            [item.url for item in prepared.results],
            [f"https://example.com/{i}" for i in range(3)],
        )

    async def test_item_limit_still_caps_concurrency(self) -> None:
        base, _, _, _ = build_runtime(
            compress={"enabled": True, "search_item_limit": 1}
        )
        runtime = SlowShrinkRuntime(base.config, delay=0.01)

        prepared, note = await render.prepare_search(
            runtime, make_search_outcome(3)
        )

        self.assertEqual(runtime.peak, 1)
        self.assertIn("已由小模型压缩：1 条", note)
        self.assertEqual(prepared.results[0].content, "压缩正文")


class FetchBatchConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """deepseek_fetch 把整批链接交给 fetch_many 并发抓取。"""

    async def test_two_urls_overlap(self) -> None:
        base, _, _, _ = build_runtime()
        runtime = BarrierRuntime(base.config, expected=2)
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))

        ok, text = await tool.execute(urls=["https://a.com", "https://b.com"])

        self.assertTrue(ok)
        self.assertGreater(runtime.peak, 1)
        self.assertIn("## 2.", text)


if __name__ == "__main__":
    unittest.main()
