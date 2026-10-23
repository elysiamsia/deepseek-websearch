"""Runtime 编排层测试（假客户端 + 假存储，不联网不落盘）。"""

from __future__ import annotations

import unittest
from unittest import mock

from tests import framework_stub
from tests.helpers import NOW, Clock, FakeClient, FakeStore, build_runtime, sample_result

runtime_mod = framework_stub.import_component("core.runtime")
errors = framework_stub.import_component("core.errors")


class SearchTests(unittest.IsolatedAsyncioTestCase):
    """搜索编排。"""

    async def test_success_records_usage_and_persists(self) -> None:
        runtime, client, store, _ = build_runtime()
        outcome = await runtime.search("Neo-MoFox")
        self.assertEqual(outcome.count, 1)
        self.assertFalse(outcome.cached)
        self.assertEqual(runtime.usage.total_requests, 1)
        self.assertEqual(store.saves[-1]["total_search"], 1)
        self.assertEqual(client.last_search["count"], 8)  # 默认条数

    async def test_cache_prevents_second_call(self) -> None:
        runtime, client, _, _ = build_runtime()
        first = await runtime.search("同一个问题")
        second = await runtime.search("同一个问题")
        self.assertEqual(client.search_calls, 1)
        self.assertTrue(second.cached)
        self.assertEqual(runtime.usage.cache_hits, 1)
        self.assertEqual(first.count, second.count)

    async def test_cache_disabled(self) -> None:
        runtime, client, _, _ = build_runtime(cache={"enabled": False})
        await runtime.search("q")
        await runtime.search("q")
        self.assertEqual(client.search_calls, 2)

    async def test_cache_key_includes_filters(self) -> None:
        runtime, client, _, _ = build_runtime()
        await runtime.search("q")
        await runtime.search("q", site="github.com")
        self.assertEqual(client.search_calls, 2)

    async def test_empty_results_not_billed(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.results = []
        outcome = await runtime.search("空")
        self.assertEqual(outcome.count, 0)
        self.assertEqual(runtime.usage.total_requests, 0)
        self.assertEqual(runtime.usage.total_empty, 1)
        self.assertEqual(runtime.cache.size, 0)

    async def test_error_records_failure(self) -> None:
        runtime, client, store, _ = build_runtime()
        client.error = errors.AuthError("HTTP 401: bad key")
        with self.assertRaises(errors.AuthError):
            await runtime.search("q")
        self.assertEqual(runtime.usage.total_failed, 1)
        self.assertTrue(store.saves)  # 失败也会立刻落盘，方便排查

    async def test_budget_limit(self) -> None:
        runtime, client, _, _ = build_runtime(budget={"daily_request_limit": 1})
        await runtime.search("第一次")
        with self.assertRaises(errors.BudgetExceededError):
            await runtime.search("第二次")
        self.assertEqual(client.search_calls, 1)

    async def test_plugin_disabled(self) -> None:
        runtime, client, _, _ = build_runtime(plugin={"enabled": False})
        with self.assertRaises(errors.PluginDisabledError):
            await runtime.search("q")
        self.assertEqual(client.search_calls, 0)

    async def test_count_clamped(self) -> None:
        runtime, client, _, _ = build_runtime(search={"max_count": 5, "default_count": 3})
        await runtime.search("q", count=99)
        self.assertEqual(client.last_search["count"], 5)
        await runtime.search("q2", count=0)
        self.assertEqual(client.last_search["count"], 3)

    async def test_ori_question_truncated(self) -> None:
        runtime, client, _, _ = build_runtime()
        await runtime.search("q", ori_question="字" * 500)
        self.assertEqual(len(client.last_search["ori_question"]), runtime_mod.MAX_ORI_QUESTION)

    async def test_filters_composed(self) -> None:
        runtime, client, _, _ = build_runtime()
        await runtime.search("文档", site="github.com", lang="zh", after="2025")
        query = client.last_search["query"]
        self.assertIn("site:github.com", query)
        self.assertIn("lang:zh", query)
        self.assertIn("after:2025", query)

    async def test_missing_key(self) -> None:
        config = framework_stub.build_config()
        runtime = runtime_mod.Runtime(config, store=FakeStore(), clock=Clock(), now=lambda: NOW)
        with mock.patch.object(runtime_mod, "resolve_api_key", return_value=("", "")):
            with self.assertRaises(errors.MissingAPIKeyError):
                await runtime.search("q")

    async def test_client_uses_model_toml_fallback_settings(self) -> None:
        config = framework_stub.build_config(
            api={
                "model_config_path": "custom/model.toml",
                "key_provider": "DeepSeek",
            }
        )
        runtime = runtime_mod.Runtime(config, store=FakeStore(), clock=Clock(), now=lambda: NOW)
        with mock.patch.object(
            runtime_mod, "resolve_api_key", return_value=("sk-from-model-toml", "model.toml:DeepSeek")
        ) as spy:
            client = runtime._ensure_client()
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs["model_config_path"], "custom/model.toml")
        self.assertEqual(kwargs["provider_name"], "DeepSeek")
        self.assertEqual(client.api_key, "sk-from-model-toml")
        await runtime.aclose()


class FetchTests(unittest.IsolatedAsyncioTestCase):
    """抓取编排。"""

    async def test_success_and_cache(self) -> None:
        runtime, client, _, _ = build_runtime()
        first = await runtime.fetch("https://example.com")
        self.assertEqual(first.title, "标题")
        self.assertEqual(runtime.usage.total_fetch, 1)
        second = await runtime.fetch("https://example.com")
        self.assertTrue(second.cached)
        self.assertEqual(client.fetch_calls, 1)

    async def test_private_host_blocked(self) -> None:
        runtime, client, _, _ = build_runtime()
        with self.assertRaises(errors.InvalidArgumentError):
            await runtime.fetch("http://127.0.0.1:8080/")
        self.assertEqual(client.fetch_calls, 0)

    async def test_private_host_allowed_by_config(self) -> None:
        runtime, client, _, _ = build_runtime(fetch={"block_private_hosts": False})
        await runtime.fetch("http://127.0.0.1:8080/")
        self.assertEqual(client.fetch_calls, 1)

    async def test_fetch_error(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.fetch_error = errors.NotFoundError("HTTP 404")
        with self.assertRaises(errors.NotFoundError):
            await runtime.fetch("https://example.com/x")
        self.assertEqual(runtime.usage.total_failed, 1)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    """启动、持久化与管理接口。"""

    async def test_start_loads_usage(self) -> None:
        config = framework_stub.build_config()
        store = FakeStore(
            data={"total_search": 42, "days": {"2026-10-23": {"search": 7, "fetch": 0}}}
        )
        runtime = runtime_mod.Runtime(
            config, client=FakeClient(), store=store, clock=Clock(), now=lambda: NOW
        )
        await runtime.start()
        self.assertEqual(runtime.usage.total_search, 42)
        self.assertEqual(runtime.usage.today_count(NOW), 7)

    async def test_start_with_broken_store(self) -> None:
        class BrokenStore:
            """读写都会失败的存储。"""

            async def load(self):
                raise OSError("disk on fire")

            async def save(self, data):
                raise OSError("disk on fire")

        config = framework_stub.build_config()
        runtime = runtime_mod.Runtime(
            config, client=FakeClient(results=[sample_result()]), store=BrokenStore(),
            clock=Clock(), now=lambda: NOW,
        )
        await runtime.start()  # 不应抛异常
        outcome = await runtime.search("q")
        self.assertEqual(outcome.count, 1)  # 主流程不受影响

    async def test_persist_debounced(self) -> None:
        runtime, _, store, clock = build_runtime()
        clock.value = 5000.0
        await runtime.search("a")
        saves_after_first = len(store.saves)
        clock.advance(1)
        await runtime.search("b")
        self.assertEqual(len(store.saves), saves_after_first)
        clock.advance(60)
        await runtime.search("c")
        self.assertGreater(len(store.saves), saves_after_first)

    async def test_persist_disabled(self) -> None:
        runtime, _, store, _ = build_runtime(budget={"persist_usage": False})
        await runtime.search("q")
        self.assertEqual(store.saves, [])

    async def test_aclose_flushes_and_closes(self) -> None:
        runtime, client, store, _ = build_runtime()
        await runtime.search("q")
        await runtime.aclose()
        self.assertTrue(client.closed)
        self.assertIsNone(runtime.client)
        self.assertTrue(store.saves)

    async def test_reset_usage(self) -> None:
        runtime, _, _, _ = build_runtime()
        await runtime.search("q")
        await runtime.reset_usage()
        self.assertEqual(runtime.usage.total_requests, 0)

    async def test_clear_cache(self) -> None:
        runtime, _, _, _ = build_runtime()
        await runtime.search("q")
        self.assertEqual(runtime.cache.size, 1)
        removed = await runtime.clear_cache()
        self.assertEqual(removed, 1)
        self.assertEqual(runtime.cache.size, 0)

    async def test_snapshot(self) -> None:
        runtime, _, _, _ = build_runtime(budget={"daily_request_limit": 5})
        await runtime.search("q")
        snapshot = runtime.usage_snapshot()
        self.assertEqual(snapshot["today_requests"], 1)
        self.assertEqual(snapshot["daily_limit"], 5)
        self.assertAlmostEqual(snapshot["cost_cny"], 0.005)
        self.assertIn("cache", snapshot)


if __name__ == "__main__":
    unittest.main()
