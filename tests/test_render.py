"""渲染层测试：压缩 / 截断如何落到最终文本。"""

from __future__ import annotations

import unittest

from tests import framework_stub
from tests.helpers import FakeLLM, FakePlugin, build_runtime

render = framework_stub.import_component("components.render")
tools_mod = framework_stub.import_component("components.tools")
models = framework_stub.import_component("core.models")
fmt = framework_stub.import_component("core.formatting")


def make_search_outcome(content: str = "正" * 5000, count: int = 1):
    """构造一条带正文的搜索结果。"""

    items = tuple(
        models.SearchResult(
            title=f"标题{i}",
            url=f"https://example.com/{i}",
            summary="摘要",
            content=content,
            authority=3,
            rerank_score=2.0,
        )
        for i in range(count)
    )
    return models.SearchOutcome(query="q", results=items, elapsed_ms=10)


class SearchRenderTests(unittest.IsolatedAsyncioTestCase):
    """搜索结果渲染。"""

    async def test_truncate_when_compression_off(self) -> None:
        runtime, _, _, _ = build_runtime(search={"max_content_chars": 100})
        text = await render.render_search_markdown(runtime, make_search_outcome())
        self.assertIn("正文摘录：", text)
        self.assertNotIn("已由小模型压缩", text)
        # 正文被截到 100 字符（带省略号）
        line = next(item for item in text.splitlines() if "正文摘录：" in item)
        self.assertLessEqual(len(line), 100 + 20)

    async def test_compress_when_enabled(self) -> None:
        llm = FakeLLM(output="压缩后的正文")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300}, llm_call=llm
        )
        text = await render.render_search_markdown(runtime, make_search_outcome())
        self.assertIn("压缩后的正文", text)
        self.assertIn("已由小模型压缩", text)
        self.assertEqual(len(llm.calls), 1)

    async def test_item_limit_respected(self) -> None:
        llm = FakeLLM(output="压缩后的正文")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300, "search_item_limit": 1},
            search={"max_content_chars": 100},
            llm_call=llm,
        )
        text = await render.render_search_markdown(runtime, make_search_outcome(count=3))
        self.assertEqual(len(llm.calls), 1)  # 只压第一条
        self.assertIn("已由小模型压缩：1 条", text)

    async def test_apply_to_search_content_off(self) -> None:
        llm = FakeLLM(output="压缩后的正文")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "apply_to_search_content": False},
            search={"max_content_chars": 100},
            llm_call=llm,
        )
        await render.render_search_markdown(runtime, make_search_outcome())
        self.assertEqual(llm.calls, [])

    def test_command_text_has_no_content(self) -> None:
        runtime, _, _, _ = build_runtime()
        text = render.render_search_text(runtime, make_search_outcome())
        self.assertNotIn("正文摘录", text)


class FetchRenderTests(unittest.IsolatedAsyncioTestCase):
    """抓取结果渲染。"""

    def make_fetch(self, content: str = "正" * 5000):
        return models.FetchOutcome(url="https://e.com", title="标题", content=content)

    async def test_truncate_when_off(self) -> None:
        runtime, _, _, _ = build_runtime()
        text = await render.render_fetch_markdown(
            runtime, self.make_fetch(), fallback_chars=100
        )
        self.assertIn("已截断到 100 字符", text)
        self.assertNotIn("已由小模型压缩", text)

    async def test_compress_when_on(self) -> None:
        llm = FakeLLM(output="压缩后的网页正文")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300}, llm_call=llm
        )
        text = await render.render_fetch_markdown(
            runtime, self.make_fetch(), fallback_chars=100
        )
        self.assertIn("压缩后的网页正文", text)
        self.assertIn("已由小模型压缩到", text)

    async def test_fallback_note_when_model_fails(self) -> None:
        llm = FakeLLM(error=RuntimeError("模型炸了"))
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300}, llm_call=llm
        )
        text = await render.render_fetch_markdown(
            runtime, self.make_fetch(), fallback_chars=100
        )
        self.assertIn("压缩未生效", text)
        self.assertIn("已截断到 100 字符", text)

    async def test_apply_to_fetch_off(self) -> None:
        llm = FakeLLM(output="压缩后的网页正文")
        runtime, _, _, _ = build_runtime(
            compress={"enabled": True, "apply_to_fetch": False}, llm_call=llm
        )
        text = await render.render_fetch_markdown(
            runtime, self.make_fetch(), fallback_chars=100
        )
        self.assertNotIn("压缩后的网页正文", text)
        self.assertEqual(llm.calls, [])

    async def test_command_text_uses_cap(self) -> None:
        runtime, _, _, _ = build_runtime()
        text = await render.render_fetch_text(
            runtime, self.make_fetch(), fallback_chars=800
        )
        self.assertIn("已截断到 800 字符", text)


class ToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Tool 走渲染层后仍能正常返回。"""

    async def test_fetch_tool_compresses(self) -> None:
        llm = FakeLLM(output="工具压缩结果")
        runtime, client, _, _ = build_runtime(
            compress={"enabled": True, "target_chars": 300}, llm_call=llm
        )
        client.content = "正" * 5000
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com", max_chars=100)
        self.assertTrue(ok)
        self.assertIn("工具压缩结果", text)
        self.assertIn("已由小模型压缩", text)

    async def test_fetch_tool_truncates_when_off(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.content = "正" * 5000
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com", max_chars=120)
        self.assertTrue(ok)
        self.assertIn("已截断到 120 字符", text)


if __name__ == "__main__":
    unittest.main()
