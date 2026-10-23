"""/ds 命令测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests import framework_stub
from tests.helpers import FakePlugin, build_runtime

command_mod = framework_stub.import_component("components.command")
errors = framework_stub.import_component("core.errors")
PermissionLevel = framework_stub.PermissionLevel


def owner_message():
    """构造一个所有者消息。"""

    framework_stub.PERMISSION_LEVELS["qq:10001"] = PermissionLevel.OWNER
    return SimpleNamespace(platform="qq", sender_id="10001", processed_plain_text="")


def user_message():
    """构造一个普通用户消息。"""

    framework_stub.PERMISSION_LEVELS["qq:20002"] = PermissionLevel.USER
    return SimpleNamespace(platform="qq", sender_id="20002", processed_plain_text="")


class CommandTests(unittest.IsolatedAsyncioTestCase):
    """命令解析与执行。"""

    def setUp(self) -> None:
        framework_stub.PERMISSION_LEVELS.clear()

    async def make_command(self, overrides=None, message=None):
        """构造命令实例。"""

        runtime, client, store, _ = build_runtime(**(overrides or {}))
        command = command_mod.DeepSeekCommand(
            plugin=FakePlugin(runtime), stream_id="s1", message=message
        )
        return command, client, runtime

    async def test_empty_shows_help(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("")
        self.assertTrue(ok)
        self.assertIn("/ds search", text)

    async def test_help(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("帮助")
        self.assertTrue(ok)
        self.assertIn("/ds fetch", text)

    async def test_bare_query(self) -> None:
        command, client, _ = await self.make_command()
        ok, text = await command.execute("今天 有什么 AI 新闻")
        self.assertTrue(ok)
        self.assertEqual(client.last_search["query"], "今天 有什么 AI 新闻")
        self.assertIn("🔍", text)

    async def test_search_with_options(self) -> None:
        command, client, _ = await self.make_command()
        ok, _ = await command.execute("search DeepSeek V4 -n 3 -c --site github.com --lang zh")
        self.assertTrue(ok)
        call = client.last_search
        self.assertEqual(call["query"].split(" site:")[0], "DeepSeek V4")
        self.assertEqual(call["count"], 3)
        self.assertTrue(call["need_content"])
        self.assertIn("site:github.com", call["query"])
        self.assertIn("lang:zh", call["query"])

    async def test_search_bad_count(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("search abc -n x")
        self.assertFalse(ok)
        self.assertIn("参数错误", text)

    async def test_search_missing_query(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("search -n 3")
        self.assertFalse(ok)
        self.assertIn("关键词", text)

    async def test_search_error(self) -> None:
        command, client, _ = await self.make_command()
        client.error = errors.InsufficientBalanceError("HTTP 402")
        ok, text = await command.execute("搜索 测试")
        self.assertFalse(ok)
        self.assertIn("❌", text)

    async def test_fetch(self) -> None:
        command, client, _ = await self.make_command()
        ok, text = await command.execute("fetch https://example.com/a -m 100")
        self.assertTrue(ok)
        self.assertEqual(client.last_fetch, "https://example.com/a")
        self.assertIn("📄", text)

    async def test_fetch_missing_url(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("fetch")
        self.assertFalse(ok)
        self.assertIn("链接", text)

    async def test_stat(self) -> None:
        command, _, _ = await self.make_command()
        await command.execute("查 测试")
        ok, text = await command.execute("stat")
        self.assertTrue(ok)
        self.assertIn("📊", text)
        self.assertIn("缓存", text)

    async def test_usage_footer(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("q")
        self.assertTrue(ok)
        self.assertIn("今日", text)
        self.assertIn("累计", text)

    async def test_usage_footer_can_be_disabled(self) -> None:
        command, _, _ = await self.make_command(overrides={"plugin": {"show_usage_in_reply": False}})
        ok, text = await command.execute("q")
        self.assertTrue(ok)
        self.assertNotIn("📊 今日", text)

    async def test_command_disabled(self) -> None:
        command, _, _ = await self.make_command(overrides={"plugin": {"command_enabled": False}})
        ok, text = await command.execute("随便搜点")
        self.assertFalse(ok)
        self.assertIn("关闭", text)

    async def test_cache_clear_requires_permission(self) -> None:
        command, _, _ = await self.make_command(message=user_message())
        ok, text = await command.execute("cache clear")
        self.assertFalse(ok)
        self.assertIn("权限", text)

    async def test_cache_clear_by_operator(self) -> None:
        framework_stub.PERMISSION_LEVELS["qq:10001"] = PermissionLevel.OPERATOR
        command, _, runtime = await self.make_command(message=owner_message())
        await command.execute("q")
        ok, text = await command.execute("cache clear")
        self.assertTrue(ok)
        self.assertIn("已清空", text)
        self.assertEqual(runtime.cache.size, 0)

    async def test_cache_status(self) -> None:
        command, _, _ = await self.make_command()
        ok, text = await command.execute("cache")
        self.assertTrue(ok)
        self.assertIn("🧠", text)

    async def test_reset_requires_owner(self) -> None:
        command, _, _ = await self.make_command(message=user_message())
        ok, text = await command.execute("reset")
        self.assertFalse(ok)
        self.assertIn("权限", text)

    async def test_reset_by_owner(self) -> None:
        command, _, runtime = await self.make_command(message=owner_message())
        await command.execute("q")
        ok, text = await command.execute("reset")
        self.assertTrue(ok)
        self.assertIn("清零", text)
        self.assertEqual(runtime.usage.total_requests, 0)

    async def test_test_command_requires_owner(self) -> None:
        command, _, _ = await self.make_command(message=user_message())
        ok, text = await command.execute("test")
        self.assertFalse(ok)
        self.assertIn("权限", text)

    async def test_test_command_by_owner(self) -> None:
        command, client, _ = await self.make_command(message=owner_message())
        ok, text = await command.execute("test")
        self.assertTrue(ok)
        self.assertIn("自检通过", text)
        self.assertEqual(client.last_search["count"], 1)


class ParseTests(unittest.TestCase):
    """参数解析纯函数。"""

    def test_search_options(self) -> None:
        query, options = command_mod._parse_search_args(
            ["a", "b", "-n", "4", "--site=github.com", "--after", "2025", "-c"]
        )
        self.assertEqual(query, "a b")
        self.assertEqual(options["count"], 4)
        self.assertEqual(options["site"], "github.com")
        self.assertEqual(options["after"], "2025")
        self.assertTrue(options["need_content"])

    def test_search_missing_value(self) -> None:
        with self.assertRaises(ValueError):
            command_mod._parse_search_args(["abc", "--site"])

    def test_fetch_options(self) -> None:
        url, max_chars = command_mod._parse_fetch_args(["https://x.com", "-m", "500"])
        self.assertEqual(url, "https://x.com")
        self.assertEqual(max_chars, 500)

    def test_fetch_default(self) -> None:
        self.assertEqual(
            command_mod._parse_fetch_args(["https://x.com"]), ("https://x.com", 0)
        )


if __name__ == "__main__":
    unittest.main()
