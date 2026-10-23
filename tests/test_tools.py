"""Tool 组件测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests import framework_stub
from tests.helpers import FakePlugin, build_runtime

tools_mod = framework_stub.import_component("components.tools")
errors = framework_stub.import_component("core.errors")
fmt = framework_stub.import_component("core.formatting")


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    """deepseek_search。"""

    async def test_success_renders_markdown(self) -> None:
        runtime, client, _, _ = build_runtime()
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))
        ok, text = await tool.execute("Neo-MoFox 插件")
        self.assertTrue(ok)
        self.assertIn("标题", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn(fmt.UNTRUSTED_NOTICE, text)
        self.assertEqual(client.last_search["count"], 8)

    async def test_parameters_forwarded(self) -> None:
        runtime, client, _, _ = build_runtime()
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))
        await tool.execute(
            "文档", count=3, need_content=True, site="github.com",
            exclude_site="gitee.com", lang="zh", after="2025", before="2026-12-31",
        )
        call = client.last_search
        self.assertEqual(call["count"], 3)
        self.assertTrue(call["need_content"])
        self.assertIn("site:github.com", call["query"])
        self.assertIn("-site:gitee.com", call["query"])
        self.assertIn("lang:zh", call["query"])
        self.assertIn("after:2025", call["query"])
        self.assertIn("before:2026-12-31", call["query"])

    async def test_ori_question_from_trigger_message(self) -> None:
        runtime, client, _, _ = build_runtime()
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))
        tool.trigger_message = SimpleNamespace(
            processed_plain_text="帮我查一下 Neo-MoFox 最新的文档\n谢谢"
        )
        await tool.execute("Neo-MoFox")
        self.assertEqual(
            client.last_search["ori_question"], "帮我查一下 Neo-MoFox 最新的文档 谢谢"
        )

    async def test_ori_question_can_be_disabled(self) -> None:
        runtime, client, _, _ = build_runtime(search={"auto_ori_question": False})
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))
        tool.trigger_message = SimpleNamespace(processed_plain_text="帮我查")
        await tool.execute("q")
        self.assertEqual(client.last_search["ori_question"], "")

    async def test_error_becomes_friendly_text(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.error = errors.AuthError("HTTP 401: Authentication Fails")
        tool = tools_mod.DeepSeekSearchTool(FakePlugin(runtime))
        ok, text = await tool.execute("q")
        self.assertFalse(ok)
        self.assertTrue(text.startswith("❌"))
        self.assertIn("api_key", text)

    async def test_unexpected_exception_is_contained(self) -> None:
        class BrokenRuntime:
            config = framework_stub.build_config()

            async def search(self, *args, **kwargs):
                raise RuntimeError("kaboom")

        tool = tools_mod.DeepSeekSearchTool(FakePlugin(BrokenRuntime()))
        ok, text = await tool.execute("q")
        self.assertFalse(ok)
        self.assertIn("kaboom", text)

    def test_schema_has_descriptions(self) -> None:
        schema = tools_mod.DeepSeekSearchTool._schema()
        params = schema["function"]["parameters"]
        self.assertEqual(params["required"], ["query"])
        for name, prop in params["properties"].items():
            with self.subTest(name=name):
                self.assertTrue(prop["description"], f"{name} 缺少参数描述")
        self.assertEqual(params["properties"]["query"]["type"], "string")
        self.assertEqual(params["properties"]["count"]["type"], "integer")
        self.assertEqual(params["properties"]["need_content"]["type"], "boolean")


class FetchToolTests(unittest.IsolatedAsyncioTestCase):
    """deepseek_fetch。"""

    async def test_success(self) -> None:
        runtime, client, _, _ = build_runtime()
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com/post")
        self.assertTrue(ok)
        self.assertIn("正文", text)
        self.assertIn("https://example.com/post", text)
        self.assertEqual(client.last_fetch, "https://example.com/post")

    async def test_truncation(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.content = "正" * 500
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com", max_chars=50)
        self.assertTrue(ok)
        self.assertIn("已截断", text)
        self.assertLess(len(text), 400)

    async def test_default_max_chars_from_config(self) -> None:
        runtime, client, _, _ = build_runtime(fetch={"max_chars": 30})
        client.content = "字" * 200
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com", max_chars=0)
        self.assertTrue(ok)
        self.assertIn("已截断", text)

    async def test_error(self) -> None:
        runtime, client, _, _ = build_runtime()
        client.fetch_error = errors.ForbiddenError("HTTP 403")
        tool = tools_mod.DeepSeekFetchTool(FakePlugin(runtime))
        ok, text = await tool.execute("https://example.com")
        self.assertFalse(ok)
        self.assertIn("❌", text)

    def test_schema(self) -> None:
        schema = tools_mod.DeepSeekFetchTool._schema()
        params = schema["function"]["parameters"]
        self.assertEqual(params["required"], ["url"])
        self.assertTrue(params["properties"]["url"]["description"])


if __name__ == "__main__":
    unittest.main()
