"""自然语言触发器测试。"""

from __future__ import annotations

import asyncio
import unittest

from tests import framework_stub
from tests.helpers import FakePlugin, build_runtime

trigger_mod = framework_stub.import_component("components.trigger")
errors = framework_stub.import_component("core.errors")
EventDecision = framework_stub.EventDecision
EventType = framework_stub.EventType


def make_envelope(
    text: str,
    *,
    platform: str = "qq",
    group_id: str | None = None,
    user_id: str = "10001",
    segment_list: bool = False,
):
    """构造一个入站 envelope。"""

    info = {
        "platform": platform,
        "message_id": "msg-1",
        "user_info": {"user_id": user_id, "user_nickname": "测试用户"},
    }
    if group_id:
        info["group_info"] = {"group_id": group_id, "group_name": "测试群"}
    segment = {"type": "text", "data": text}
    return {
        "direction": "incoming",
        "message_info": info,
        "message_segment": [dict(segment)] if segment_list else segment,
    }


def envelope_text(envelope) -> str:
    """取出 envelope 的文本。"""

    seg = envelope.get("message_segment")
    if isinstance(seg, list):
        seg = seg[0]
    return seg.get("data")


class CleanQueryTests(unittest.TestCase):
    """查询词提取。"""

    def test_strips_polite_prefix(self) -> None:
        self.assertEqual(trigger_mod.clean_query("帮我搜一下 Neo-MoFox 插件开发"), "Neo-MoFox 插件开发")

    def test_strips_colon(self) -> None:
        self.assertEqual(trigger_mod.clean_query("查一下：Rust 异步运行时"), "Rust 异步运行时")

    def test_keeps_plain_question(self) -> None:
        self.assertEqual(trigger_mod.clean_query("今天有什么 AI 新闻"), "今天有什么 AI 新闻")

    def test_strips_suffix_particles(self) -> None:
        self.assertEqual(trigger_mod.clean_query("搜一下 Neo-MoFox 吧"), "Neo-MoFox")

    def test_empty_after_cleaning(self) -> None:
        self.assertEqual(trigger_mod.clean_query("搜一下"), "")
        self.assertEqual(trigger_mod.clean_query("   "), "")

    def test_truncates(self) -> None:
        self.assertEqual(len(trigger_mod.clean_query("词" * 300, 120)), 120)


class TriggerBehaviourTests(unittest.IsolatedAsyncioTestCase):
    """触发与注入。"""

    def setUp(self) -> None:
        framework_stub.reset_state()

    async def make_handler(self, overrides=None):
        """构造 handler 与依赖。"""

        runtime, client, store, clock = build_runtime(**(overrides or {}))
        handler = trigger_mod.DeepSeekNaturalTrigger(FakePlugin(runtime))
        return handler, runtime, client, clock

    async def test_declares_subscription(self) -> None:
        handler, _, _, _ = await self.make_handler()
        self.assertEqual(handler.init_subscribe, [EventType.BEFORE_MESSAGE_RECEIVED])
        self.assertEqual(handler.timeout, 0)
        self.assertFalse(handler.intercept_message)

    async def test_match_injects_results(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = make_envelope("帮我搜一下 Neo-MoFox 插件开发")
        params = {"envelope": envelope, "adapter_signature": "test:adapter:qq"}

        decision, out = await handler.execute("before_message_received", params)

        self.assertEqual(decision, EventDecision.SUCCESS)
        self.assertIs(out["envelope"], envelope)
        text = envelope_text(envelope)
        self.assertIn(trigger_mod.INJECT_HEADER.strip(), text)
        self.assertIn(trigger_mod.INJECT_FOOTER.strip(), text)
        self.assertIn("标题", text)
        self.assertTrue(text.startswith("帮我搜一下 Neo-MoFox 插件开发"))
        self.assertEqual(client.search_calls, 1)
        self.assertEqual(client.last_search["query"], "Neo-MoFox 插件开发")
        self.assertEqual(client.last_search["count"], 5)

    async def test_segment_list_form(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = make_envelope("帮我查一下 DeepSeek V4", segment_list=True)
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.SUCCESS)
        self.assertEqual(client.search_calls, 1)
        self.assertIn("【系统注入", envelope["message_segment"][0]["data"])

    async def test_no_intent_passes(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = make_envelope("今天天气真不错，适合睡觉")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)
        self.assertEqual(envelope_text(envelope), "今天天气真不错，适合睡觉")

    async def test_disabled_passes(self) -> None:
        handler, _, client, _ = await self.make_handler(overrides={"trigger": {"enabled": False}})
        envelope = make_envelope("帮我搜一下 Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_command_prefix_skipped(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = make_envelope("/ds search Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_group_switch(self) -> None:
        handler, _, client, _ = await self.make_handler(overrides={"trigger": {"allow_group": False}})
        envelope = make_envelope("帮我搜一下 Neo-MoFox", group_id="123456")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_private_switch(self) -> None:
        handler, _, client, _ = await self.make_handler(overrides={"trigger": {"allow_private": False}})
        envelope = make_envelope("帮我搜一下 Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_cooldown_blocks_second(self) -> None:
        handler, _, client, clock = await self.make_handler()
        first = make_envelope("帮我搜一下 Neo-MoFox")
        await handler.execute("x", {"envelope": first})
        second = make_envelope("帮我搜一下 Neo-MoFox", user_id="10001")
        clock.advance(10)
        decision, _ = await handler.execute("x", {"envelope": second})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 1)

    async def test_cooldown_expires(self) -> None:
        # 关掉缓存，才能真正断言「又打了一次上游」
        handler, _, client, clock = await self.make_handler(overrides={"cache": {"enabled": False}})
        await handler.execute("x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox")})
        clock.advance(61)
        decision, _ = await handler.execute(
            "x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox")}
        )
        self.assertEqual(decision, EventDecision.SUCCESS)
        self.assertEqual(client.search_calls, 2)

    async def test_second_trigger_hits_cache(self) -> None:
        handler, _, client, clock = await self.make_handler()
        await handler.execute("x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox")})
        clock.advance(61)
        decision, _ = await handler.execute(
            "x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox")}
        )
        self.assertEqual(decision, EventDecision.SUCCESS)
        self.assertEqual(client.search_calls, 1)  # 第二次走缓存，不重复计费

    async def test_daily_limit(self) -> None:
        handler, _, client, clock = await self.make_handler(
            overrides={"trigger": {"daily_limit": 1, "cooldown_seconds": 0}}
        )
        await handler.execute("x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox")})
        clock.advance(1)
        decision, _ = await handler.execute(
            "x", {"envelope": make_envelope("帮我搜一下 Neo-MoFox", user_id="20002")}
        )
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 1)
        self.assertEqual(handler._count, 1)

    async def test_search_error_passes(self) -> None:
        handler, _, client, _ = await self.make_handler()
        client.error = errors.AuthError("HTTP 401: bad key")
        envelope = make_envelope("帮我搜一下 Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(envelope_text(envelope), "帮我搜一下 Neo-MoFox")

    async def test_timeout_passes(self) -> None:
        handler, _, client, _ = await self.make_handler()
        client.error = asyncio.TimeoutError()
        envelope = make_envelope("帮我搜一下 Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(envelope_text(envelope), "帮我搜一下 Neo-MoFox")

    async def test_empty_results_no_inject(self) -> None:
        handler, _, client, _ = await self.make_handler()
        client.results = []
        envelope = make_envelope("帮我搜一下 一个不存在的东西")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(envelope_text(envelope), "帮我搜一下 一个不存在的东西")
        self.assertEqual(handler._count, 0)

    async def test_inject_truncated(self) -> None:
        handler, _, client, _ = await self.make_handler(
            overrides={"trigger": {"max_inject_chars": 260}}
        )
        client.results = client.results * 20
        envelope = make_envelope("帮我搜一下 Neo-MoFox")
        await handler.execute("x", {"envelope": envelope})
        injected = envelope_text(envelope)
        block = injected.split(trigger_mod.INJECT_HEADER.strip())[1].split(
            trigger_mod.INJECT_FOOTER.strip()
        )[0]
        self.assertLessEqual(len(block.strip()), 260)

    async def test_already_injected_skipped(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = make_envelope("帮我搜一下 Neo-MoFox" + trigger_mod.INJECT_HEADER + "旧结果")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_non_text_message_skipped(self) -> None:
        handler, _, client, _ = await self.make_handler()
        envelope = {
            "direction": "incoming",
            "message_info": {"platform": "qq", "user_info": {"user_id": "1"}},
            "message_segment": {"type": "image", "data": "https://example.com/a.png"},
        }
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.PASS)
        self.assertEqual(client.search_calls, 0)

    async def test_bad_regex_ignored(self) -> None:
        handler, _, client, _ = await self.make_handler(
            overrides={"trigger": {"patterns": ["([bad", "帮我查一下"]}}
        )
        envelope = make_envelope("帮我查一下 Neo-MoFox")
        decision, _ = await handler.execute("x", {"envelope": envelope})
        self.assertEqual(decision, EventDecision.SUCCESS)
        self.assertEqual(client.search_calls, 1)

    async def test_missing_envelope_passes(self) -> None:
        handler, _, _, _ = await self.make_handler()
        decision, _ = await handler.execute("x", {})
        self.assertEqual(decision, EventDecision.PASS)


if __name__ == "__main__":
    unittest.main()
