"""加载期预热测试：runtime.prewarm 与插件的后台预热任务（不联网）。"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests import framework_stub
from tests.helpers import NOW, Clock, FakeStore, build_runtime

plugin_mod = framework_stub.import_component("plugin")
runtime_mod = framework_stub.import_component("core.runtime")
errors = framework_stub.import_component("core.errors")


class PrewarmTests(unittest.TestCase):
    """Runtime.prewarm：只建客户端，失败只返回 False。"""

    def test_prewarm_creates_client_without_requests(self) -> None:
        config = framework_stub.build_config()
        runtime = runtime_mod.Runtime(
            config, store=FakeStore(), clock=Clock(), now=lambda: NOW
        )
        with mock.patch.object(
            runtime_mod, "resolve_api_key", return_value=("sk-test-1234567890", "config")
        ) as spy:
            self.assertTrue(runtime.prewarm())

        self.assertEqual(spy.call_count, 1)
        self.assertIsNotNone(runtime.client)
        self.assertEqual(runtime.client.api_key, "sk-test-1234567890")
        # 预热只装配客户端：一个请求都没有发出去
        self.assertEqual(runtime.client.request_count, 0)

    def test_prewarm_calls_ensure_client(self) -> None:
        runtime, client, _, _ = build_runtime()
        runtime.client = None
        with mock.patch.object(runtime, "_ensure_client", return_value=client) as spy:
            self.assertTrue(runtime.prewarm())
        spy.assert_called_once_with()

    def test_prewarm_without_key_returns_false(self) -> None:
        logger = framework_stub.get_logger("deepseek_websearch")
        logger.records.clear()
        config = framework_stub.build_config()
        runtime = runtime_mod.Runtime(
            config, store=FakeStore(), clock=Clock(), now=lambda: NOW, logger=logger
        )
        with mock.patch.object(runtime_mod, "resolve_api_key", return_value=("", "")):
            self.assertFalse(runtime.prewarm())  # 不抛异常
        self.assertIsNone(runtime.client)
        self.assertTrue(any("预热失败" in item for item in logger.messages("warning")))

    def test_prewarm_swallows_unexpected_error(self) -> None:
        runtime, _, _, _ = build_runtime()
        with mock.patch.object(runtime, "_ensure_client", side_effect=RuntimeError("炸了")):
            self.assertFalse(runtime.prewarm())


class PluginPrewarmTests(unittest.IsolatedAsyncioTestCase):
    """插件在 on_plugin_loaded 里起后台预热任务。"""

    def setUp(self) -> None:
        framework_stub.reset_state()

    async def _build_plugin(self, prewarm=None):
        """构造插件；prewarm 为 None 表示不写这个配置项（走默认值）。"""

        config = framework_stub.build_config()
        if prewarm is not None:
            setattr(config.plugin, "prewarm", prewarm)
        plugin = plugin_mod.DeepSeekWebSearchPlugin(config)
        runtime = await plugin.get_runtime()
        return plugin, runtime

    async def test_task_created_by_default_and_runs_prewarm(self) -> None:
        plugin, runtime = await self._build_plugin()  # 默认 prewarm=True
        calls: list[int] = []

        def fake_prewarm() -> bool:
            calls.append(1)
            return True

        runtime.prewarm = fake_prewarm
        with mock.patch.object(
            plugin_mod, "resolve_api_key", return_value=("sk-test-1234567890", "config")
        ):
            await plugin.on_plugin_loaded()

        task = plugin._prewarm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, 1)  # 后台任务会自己跑完
        self.assertEqual(calls, [1])
        await plugin.on_plugin_unloaded()

    async def test_task_skipped_when_prewarm_disabled(self) -> None:
        plugin, runtime = await self._build_plugin(prewarm=False)
        with mock.patch.object(runtime, "prewarm", return_value=True) as spy:
            with mock.patch.object(
                plugin_mod, "resolve_api_key",
                return_value=("sk-test-1234567890", "config"),
            ):
                await plugin.on_plugin_loaded()
            self.assertIsNone(plugin._prewarm_task)
            spy.assert_not_called()
        await plugin.on_plugin_unloaded()

    async def test_unload_cancels_unfinished_task(self) -> None:
        plugin, _ = await self._build_plugin()
        started = asyncio.Event()

        async def slow_prewarm() -> None:
            started.set()
            await asyncio.sleep(30)

        plugin._prewarm = slow_prewarm
        with mock.patch.object(
            plugin_mod, "resolve_api_key", return_value=("sk-test-1234567890", "config")
        ):
            await plugin.on_plugin_loaded()

        task = plugin._prewarm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(started.wait(), 1)
        self.assertFalse(task.done())

        await plugin.on_plugin_unloaded()
        self.assertTrue(task.cancelled())
        self.assertIsNone(plugin._prewarm_task)

    async def test_prewarm_failure_does_not_break_plugin(self) -> None:
        plugin, runtime = await self._build_plugin()
        runtime.prewarm = mock.Mock(side_effect=RuntimeError("炸了"))
        with mock.patch.object(
            plugin_mod, "resolve_api_key", return_value=("sk-test-1234567890", "config")
        ):
            await plugin.on_plugin_loaded()
        await asyncio.wait_for(plugin._prewarm_task, 1)  # 异常被吞掉，任务正常结束
        warnings = framework_stub.LOGGERS["deepseek_websearch"].messages("warning")
        self.assertTrue(any("预热失败" in item for item in warnings))
        await plugin.on_plugin_unloaded()


if __name__ == "__main__":
    unittest.main()
