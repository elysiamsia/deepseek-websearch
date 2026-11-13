"""搜索缓存键测试：ori_question 默认不参与缓存键。"""

from __future__ import annotations

import unittest

from tests import framework_stub
from tests.helpers import NOW, Clock, FakeClient, FakeStore, build_runtime, sample_result

runtime_mod = framework_stub.import_component("core.runtime")


def build_key_runtime(**cache_overrides):
    """构造一个可以改缓存配置的 Runtime（新字段直接挂在配置节上）。"""

    config = framework_stub.build_config()
    for key, value in cache_overrides.items():
        setattr(config.cache, key, value)
    client = FakeClient(results=[sample_result()])
    runtime = runtime_mod.Runtime(
        config, client=client, store=FakeStore(), clock=Clock(), now=lambda: NOW
    )
    return runtime, client


class CacheKeyTests(unittest.IsolatedAsyncioTestCase):
    """ori_question 与缓存命中的关系。"""

    async def test_different_ori_question_hits_same_cache(self) -> None:
        runtime, client = build_key_runtime()
        first = await runtime.search("天气", ori_question="今天北京天气怎么样")
        second = await runtime.search("天气", ori_question="上海明天会下雨吗")

        # 默认 ori_question 不进缓存键：第二次直接命中，不再请求也不需要再付一次钱
        self.assertEqual(client.search_calls, 1)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(runtime.usage.cache_hits, 1)
        self.assertEqual(first.query, second.query)

        # 但发给 API 的 ori_question 仍然是用户原始问题，不能被缓存键改造影响
        self.assertEqual(client.last_search["ori_question"], "今天北京天气怎么样")

    async def test_empty_and_filled_ori_question_share_cache(self) -> None:
        runtime, client = build_key_runtime()
        await runtime.search("同一个查询", ori_question="")
        outcome = await runtime.search("同一个查询", ori_question="另一个问法")
        self.assertEqual(client.search_calls, 1)
        self.assertTrue(outcome.cached)

    async def test_ori_question_part_of_key_when_enabled(self) -> None:
        runtime, client = build_key_runtime(key_includes_ori_question=True)
        first = await runtime.search("天气", ori_question="今天北京天气怎么样")
        second = await runtime.search("天气", ori_question="上海明天会下雨吗")

        self.assertEqual(client.search_calls, 2)
        self.assertFalse(first.cached)
        self.assertFalse(second.cached)

        # 同一个 ori_question 仍然能命中
        third = await runtime.search("天气", ori_question="上海明天会下雨吗")
        self.assertEqual(client.search_calls, 2)
        self.assertTrue(third.cached)

    async def test_query_still_part_of_key(self) -> None:
        runtime, client = build_key_runtime()
        await runtime.search("查询一")
        await runtime.search("查询二")
        self.assertEqual(client.search_calls, 2)


if __name__ == "__main__":
    unittest.main()
