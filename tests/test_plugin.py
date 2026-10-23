"""插件根组件测试。"""

from __future__ import annotations

import unittest
from unittest import mock

from tests import framework_stub
from tests.helpers import FakeClient

plugin_mod = framework_stub.import_component("plugin")
components_tools = framework_stub.import_component("components.tools")
components_command = framework_stub.import_component("components.command")
components_service = framework_stub.import_component("components.service")
components_trigger = framework_stub.import_component("components.trigger")


class PluginClassTests(unittest.TestCase):
    """类结构与注册。"""

    def test_registered(self) -> None:
        framework_stub.load_package()
        self.assertIn(plugin_mod.DeepSeekWebSearchPlugin, framework_stub.REGISTERED_PLUGINS)

    def test_components(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        components = plugin.get_components()
        self.assertEqual(
            components,
            [
                components_tools.DeepSeekSearchTool,
                components_tools.DeepSeekFetchTool,
                components_command.DeepSeekCommand,
                components_service.DeepSeekWebSearchService,
                components_trigger.DeepSeekNaturalTrigger,
            ],
        )
        for component in components:
            with self.subTest(component=component.__name__):
                self.assertTrue(component.name)

    def test_configs_declared(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        self.assertEqual(len(plugin.configs), 1)
        self.assertTrue(issubclass(plugin.configs[0], framework_stub.BaseConfig))


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """运行时创建与卸载。"""

    def setUp(self) -> None:
        framework_stub.reset_state()

    async def test_get_runtime_is_cached(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        first = await plugin.get_runtime()
        second = await plugin.get_runtime()
        self.assertIs(first, second)

    async def test_get_runtime_without_injected_config(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(None)
        runtime = await plugin.get_runtime()
        self.assertIsNotNone(runtime.config)
        self.assertTrue(hasattr(runtime.config, "search"))

    async def test_on_plugin_loaded_logs_configuration(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(
            framework_stub.build_config(
                budget={"daily_request_limit": 100},
                cache={"ttl_seconds": 120},
            )
        )
        with mock.patch.object(plugin_mod, "resolve_api_key", return_value=("sk-test-1234567890", "config")):
            await plugin.on_plugin_loaded()
        messages = framework_stub.LOGGERS["deepseek_websearch"].messages()
        joined = "\n".join(messages)
        self.assertIn("已加载", joined)
        self.assertIn("120s", joined)
        self.assertIn("每日 100 次", joined)
        self.assertIn("sk-t****7890", joined)

    async def test_on_plugin_loaded_without_key_warns(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        with mock.patch.object(plugin_mod, "resolve_api_key", return_value=("", "")):
            await plugin.on_plugin_loaded()
        warnings = framework_stub.LOGGERS["deepseek_websearch"].messages("warning")
        self.assertTrue(any("API Key" in item for item in warnings))

    async def test_on_plugin_unloaded_closes_runtime(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        runtime = await plugin.get_runtime()
        fake = FakeClient()
        runtime.client = fake
        await plugin.on_plugin_unloaded()
        self.assertTrue(fake.closed)
        self.assertIsNone(plugin._runtime)

    async def test_unload_without_runtime_is_safe(self) -> None:
        plugin = plugin_mod.DeepSeekWebSearchPlugin(framework_stub.build_config())
        await plugin.on_plugin_unloaded()  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
