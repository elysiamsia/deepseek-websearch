"""小模型压缩器测试（不联网、不碰真实模型）。"""

from __future__ import annotations

import asyncio
import unittest

from tests import framework_stub
from tests.helpers import FakeLLM, build_runtime

compressor_mod = framework_stub.import_component("core.compressor")


def make_compressor(**overrides):
    """构造压缩器与其配置。"""

    config = framework_stub.build_config(compress=overrides) if overrides else framework_stub.build_config()
    llm = FakeLLM()
    return compressor_mod.ContentCompressor(config.compress, call_llm=llm), llm, config


class DisabledTests(unittest.IsolatedAsyncioTestCase):
    """默认关闭时只截断。"""

    async def test_disabled_truncates(self) -> None:
        compressor, llm, _ = make_compressor(enabled=False)
        result = await compressor.shrink("正" * 1000, fallback_chars=100)
        self.assertEqual(result.method, "truncate")
        self.assertEqual(len(result.text), 100)
        self.assertEqual(result.original_chars, 1000)
        self.assertEqual(llm.calls, [])

    async def test_disabled_without_cap_keeps_text(self) -> None:
        compressor, _, _ = make_compressor(enabled=False)
        result = await compressor.shrink("正" * 1000, fallback_chars=0)
        self.assertEqual(result.method, "passthrough")
        self.assertEqual(len(result.text), 1000)

    async def test_disabled_when_no_llm_injected(self) -> None:
        config = framework_stub.build_config(compress={"enabled": True})
        compressor = compressor_mod.ContentCompressor(config.compress, call_llm=None)
        self.assertFalse(compressor.enabled)
        result = await compressor.shrink("正" * 500, fallback_chars=50)
        self.assertEqual(result.method, "truncate")


class EnabledTests(unittest.IsolatedAsyncioTestCase):
    """开启后用模型压缩。"""

    async def test_short_text_passthrough(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=2000)
        result = await compressor.shrink("短文本", fallback_chars=100)
        self.assertEqual(result.method, "passthrough")
        self.assertEqual(result.text, "短文本")
        self.assertEqual(llm.calls, [])

    async def test_compress_called(self) -> None:
        compressor, llm, _ = make_compressor(
            enabled=True, target_chars=2000, temperature=0.2
        )
        llm.output = "压缩结果"
        result = await compressor.shrink("正" * 3000, fallback_chars=500)
        self.assertEqual(result.method, "llm")
        self.assertEqual(result.text, "压缩结果")
        self.assertEqual(result.original_chars, 3000)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("2000", llm.calls[0]["user"])
        self.assertNotIn("<<<正文结束>>>", llm.calls[0]["system"])

    async def test_custom_system_prompt(self) -> None:
        compressor, llm, _ = make_compressor(
            enabled=True, system_prompt="自定义提示词"
        )
        await compressor.shrink("正" * 3000, fallback_chars=100)
        self.assertEqual(llm.calls[0]["system"], "自定义提示词")

    async def test_long_input_sampled(self) -> None:
        compressor, llm, _ = make_compressor(
            enabled=True, max_input_chars=1000, target_chars=300
        )
        await compressor.shrink("字" * 50000, fallback_chars=100)
        prompt = llm.calls[0]["user"]
        self.assertIn("中间省略", prompt)
        self.assertLess(len(prompt), 1400)

    async def test_output_truncated_if_model_ignores_target(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=300)
        llm.output = "满" * 5000
        result = await compressor.shrink("正" * 5000, fallback_chars=100)
        self.assertLessEqual(len(result.text), 300)

    async def test_empty_output_falls_back(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=300)
        llm.output = "   "
        result = await compressor.shrink("正" * 5000, fallback_chars=200)
        self.assertEqual(result.method, "truncate")
        self.assertEqual(len(result.text), 200)
        self.assertIn("空内容", result.reason)

    async def test_timeout_falls_back(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=300)
        llm.error = asyncio.TimeoutError()
        result = await compressor.shrink("正" * 5000, fallback_chars=200)
        self.assertEqual(result.method, "truncate")
        self.assertEqual(len(result.text), 200)

    async def test_fence_stripped(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=5000)
        fence = chr(96) * 3
        llm.output = fence + "\n压缩结果\n" + fence
        result = await compressor.shrink("正" * 6000, fallback_chars=100)
        self.assertEqual(result.text, "压缩结果")

    async def test_max_tokens_from_config(self) -> None:
        compressor, llm, _ = make_compressor(
            enabled=True, target_chars=300, max_tokens=1234
        )
        await compressor.shrink("正" * 5000, fallback_chars=100)
        self.assertEqual(llm.calls[0]["max_tokens"], 1234)

    async def test_max_tokens_estimated(self) -> None:
        compressor, llm, _ = make_compressor(enabled=True, target_chars=1000, max_tokens=0)
        await compressor.shrink("正" * 5000, fallback_chars=100)
        self.assertEqual(llm.calls[0]["max_tokens"], 1600)


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Runtime 层：压缩结果缓存与开关。"""

    async def test_disabled_by_default(self) -> None:
        runtime, _, _, _ = build_runtime()
        self.assertFalse(runtime.compressor_enabled)
        result = await runtime.shrink_text("正" * 500, fallback_chars=100)
        self.assertEqual(result.method, "truncate")

    async def test_compress_cached(self) -> None:
        llm = FakeLLM(output="压缩结果")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300}, llm_call=llm
        )
        self.assertTrue(runtime.compressor_enabled)
        first = await runtime.shrink_text("正" * 5000, fallback_chars=100)
        second = await runtime.shrink_text("正" * 5000, fallback_chars=100)
        self.assertEqual(first.method, "llm")
        self.assertEqual(second.text, "压缩结果")
        self.assertEqual(len(llm.calls), 1)  # 第二次走压缩缓存


if __name__ == "__main__":
    unittest.main()
