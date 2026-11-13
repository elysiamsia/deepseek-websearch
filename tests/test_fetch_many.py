"""Runtime.fetch_many 批量抓取测试（假客户端，不联网不落盘）。"""

from __future__ import annotations

import asyncio
import time
import unittest

from tests import framework_stub
from tests.helpers import NOW, Clock, FakeStore, build_runtime

runtime_mod = framework_stub.import_component("core.runtime")
models = framework_stub.import_component("core.models")
errors = framework_stub.import_component("core.errors")


class BatchClient:
    """批量抓取用的假客户端：可延时，记录调用与同时在飞的峰值。"""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail_urls: dict[str, BaseException] | None = None,
        title: str = "",
    ) -> None:
        self.delay = delay
        self.fail_urls = dict(fail_urls or {})
        self.title = title
        self.calls: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self.closed = False

    async def fetch(self, url):
        """记录并发峰值，按预设延时后返回或抛异常。"""

        self.calls.append(url)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            failure = self.fail_urls.get(url)
            if failure is not None:
                raise failure
            return self.title, f"正文：{url}"
        finally:
            self.in_flight -= 1

    async def aclose(self):
        """标记关闭。"""

        self.closed = True


def build_batch_runtime(client, **fetch_overrides):
    """构造一个注入了批量客户端的 Runtime。"""

    config = framework_stub.build_config()
    for key, value in fetch_overrides.items():
        # 新配置字段先于 config.py 落地，测试里直接挂到配置节上
        setattr(config.fetch, key, value)
    return runtime_mod.Runtime(config, client=client, store=FakeStore(), clock=Clock(), now=lambda: NOW)


class FetchManyTests(unittest.IsolatedAsyncioTestCase):
    """批量抓取的语义。"""

    async def test_dedup_and_order_kept(self) -> None:
        client = BatchClient()
        runtime = build_batch_runtime(client)
        urls = [
            "https://a.example.com/1",
            "  https://b.example.com/2  ",
            "https://a.example.com/1",
            "",
            "   ",
            "https://c.example.com/3",
        ]
        items = await runtime.fetch_many(urls, concurrency=1)
        self.assertEqual(
            [item.url for item in items],
            [
                "https://a.example.com/1",
                "https://b.example.com/2",
                "https://c.example.com/3",
            ],
        )
        self.assertEqual(client.calls, [item.url for item in items])
        self.assertTrue(all(item.ok for item in items))
        self.assertTrue(all(item.error is None for item in items))
        self.assertTrue(all(isinstance(item.outcome, models.FetchOutcome) for item in items))

    async def test_truncated_by_max_batch(self) -> None:
        client = BatchClient()
        runtime = build_batch_runtime(client, max_batch=2)
        urls = [f"https://example.com/{index}" for index in range(5)]
        items = await runtime.fetch_many(urls)  # 超出部分截断，不报错
        self.assertEqual(len(items), 2)
        self.assertEqual(client.calls, urls[:2])
        self.assertTrue(all(item.ok for item in items))

    async def test_single_failure_does_not_affect_others(self) -> None:
        bad = "https://bad.example.com/404"
        boom = "https://boom.example.com/error"
        client = BatchClient(
            fail_urls={
                bad: errors.NotFoundError("HTTP 404"),
                boom: RuntimeError("boom"),  # 非 DeepSeekError 也要被兜住
            }
        )
        runtime = build_batch_runtime(client)
        good_a = "https://good.example.com/a"
        good_b = "https://good.example.com/b"
        items = await runtime.fetch_many(
            [good_a, bad, boom, good_b], concurrency=4
        )
        self.assertEqual(len(items), 4)

        self.assertTrue(items[0].ok)
        self.assertIsNone(items[0].error)
        self.assertTrue(items[3].ok)

        # 失败只落在对应条目上，且整体没有抛出去
        self.assertFalse(items[1].ok)
        self.assertIsInstance(items[1].error, errors.NotFoundError)
        self.assertIsNone(items[1].outcome)
        self.assertEqual(items[1].title, bad)  # 没有 outcome 时标题退回链接

        self.assertFalse(items[2].ok)
        self.assertIsInstance(items[2].error, errors.DeepSeekError)
        self.assertIn("boom", str(items[2].error))
        self.assertEqual(client.in_flight, 0)

    async def test_all_failures_still_return_items(self) -> None:
        client = BatchClient(
            fail_urls={
                "https://x.example.com/1": errors.AuthError("HTTP 401"),
                "https://x.example.com/2": errors.AuthError("HTTP 401"),
            }
        )
        runtime = build_batch_runtime(client)
        items = await runtime.fetch_many(
            ["https://x.example.com/1", "https://x.example.com/2"]
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(all(not item.ok for item in items))
        self.assertTrue(all(isinstance(item.error, errors.AuthError) for item in items))

    async def test_concurrency_really_happens(self) -> None:
        client = BatchClient(delay=0.05)
        runtime = build_batch_runtime(client)
        urls = [f"https://slow.example.com/{index}" for index in range(3)]
        started = time.perf_counter()
        items = await runtime.fetch_many(urls, concurrency=3)
        elapsed = time.perf_counter() - started
        self.assertTrue(all(item.ok for item in items))
        self.assertEqual([item.url for item in items], urls)
        self.assertGreater(client.peak, 1)  # 确实有同时在飞的请求
        self.assertLess(elapsed, 0.12)  # 串行需要 0.15s

    async def test_empty_input(self) -> None:
        client = BatchClient()
        runtime = build_batch_runtime(client)
        self.assertEqual(await runtime.fetch_many([]), [])
        self.assertEqual(await runtime.fetch_many(["", "   ", "\t"]), [])
        self.assertEqual(client.calls, [])

    async def test_batch_item_properties(self) -> None:
        outcome = models.FetchOutcome(url="https://ok.example.com", title="网页标题")
        ok_item = models.BatchFetchItem(url="https://ok.example.com", outcome=outcome)
        bad_item = models.BatchFetchItem(
            url="https://bad.example.com", error=errors.NotFoundError("HTTP 404")
        )
        self.assertTrue(ok_item.ok)
        self.assertEqual(ok_item.title, "网页标题")
        self.assertFalse(bad_item.ok)
        self.assertEqual(bad_item.title, "https://bad.example.com")


class FetchManyCacheTests(unittest.IsolatedAsyncioTestCase):
    """批量抓取沿用单条缓存语义。"""

    async def test_use_cache_respected(self) -> None:
        client = BatchClient()
        runtime = build_batch_runtime(client)
        first = await runtime.fetch_many(["https://cache.example.com/1"])
        second = await runtime.fetch_many(["https://cache.example.com/1"])
        self.assertTrue(first[0].ok)
        self.assertTrue(second[0].ok)
        self.assertTrue(second[0].outcome.cached)
        self.assertEqual(client.calls, ["https://cache.example.com/1"])

        await runtime.fetch_many(["https://cache.example.com/1"], use_cache=False)
        self.assertEqual(
            client.calls,
            ["https://cache.example.com/1", "https://cache.example.com/1"],
        )

    async def test_disabled_plugin_reported_per_item(self) -> None:
        runtime, _, _, _ = build_runtime(plugin={"enabled": False})
        items = await runtime.fetch_many(["https://example.com/1", "https://example.com/2"])
        self.assertEqual(len(items), 2)
        self.assertTrue(
            all(isinstance(item.error, errors.PluginDisabledError) for item in items)
        )


if __name__ == "__main__":
    unittest.main()
