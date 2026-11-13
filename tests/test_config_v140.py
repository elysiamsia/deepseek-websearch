"""v1.4.0 性能配置的默认值（防止默认值被悄悄改回旧值）。

边界（ge/le）由框架的 pydantic 模型校验，离线桩不跑校验，故这里只锁默认值；
真机边界在 mpdt check 与配置加载时验证。
"""

from __future__ import annotations

import unittest

from tests import framework_stub

config_mod = framework_stub.import_component("config")


class PerformanceDefaultsTests(unittest.TestCase):
    """1.4.0 新增/调整的默认值。"""

    def setUp(self) -> None:
        self.config = config_mod.DeepSeekWebSearchConfig()

    def test_transport_defaults(self) -> None:
        self.assertTrue(self.config.api.reuse_ssl_context)
        self.assertEqual(self.config.api.keepalive_seconds, 120)

    def test_plugin_prewarm_default(self) -> None:
        self.assertTrue(self.config.plugin.prewarm)

    def test_cache_key_excludes_ori_question(self) -> None:
        self.assertFalse(self.config.cache.key_includes_ori_question)

    def test_fetch_batch_defaults(self) -> None:
        self.assertEqual(self.config.fetch.max_batch, 5)
        self.assertEqual(self.config.fetch.max_batch_concurrency, 5)

    def test_search_fetch_top_default(self) -> None:
        self.assertEqual(self.config.search.fetch_top, 0)

    def test_trigger_defaults_are_tighter(self) -> None:
        # 1.4.0 有意收紧：注入更短，模型首字更快
        self.assertEqual(self.config.trigger.count, 3)
        self.assertEqual(self.config.trigger.max_inject_chars, 800)


if __name__ == "__main__":
    unittest.main()
