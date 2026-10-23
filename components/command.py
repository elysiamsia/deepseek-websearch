"""聊天命令组件：/ds。

用命令直接联网搜索，不依赖 LLM 决策，适合群聊里快速查东西。
子命令通过自己解析 message_text 实现（需要保留整段自由文本作为 query，
框架的 @cmd_route 会把参数按空白切分，无法承载带空格的检索词）。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseCommand
from src.core.components.types import ChatType, PermissionLevel

from ..core.errors import DeepSeekError
from ..core.formatting import format_error, format_usage_text
from .common import get_runtime
from .render import render_fetch_text, render_search_text

logger = get_logger("deepseek_websearch")

HELP_TEXT = """📖 /ds 用法（DeepSeek 官方联网搜索）

/ds 关键词                      直接搜索
/ds search 关键词 [选项]        显式搜索
     -n 条数          返回条数（1~20）
     -c               同时抓取正文
     --site 域名      只搜该站点，可空格分隔多个
     --exclude-site 域名  排除站点
     --lang zh,en     限定语言
     --after 2025-01-01   起始日期
     --before 2026-12-31  结束日期
/ds fetch 链接 [-m 字符数]      抓取网页正文
/ds stat                        查看用量与估算费用
/ds cache                        查看缓存命中情况
/ds cache clear                  清空缓存（管理员）
/ds test                         用 1 次真实请求自检（所有者）
/ds reset                        清零统计（所有者）

计费：每 1000 次成功请求 5 元，失败与无结果不计费；同一 query 命中缓存不重复计费。"""

_SEARCH_FLAGS = ("-n", "--count")
_SEARCH_SWITCHES = ("-c", "--content")
_SEARCH_VALUES = {
    "--site": "site",
    "-s": "site",
    "--exclude-site": "exclude_site",
    "--exclude": "exclude_site",
    "--lang": "lang",
    "-l": "lang",
    "--after": "after",
    "--before": "before",
}


class DeepSeekCommand(BaseCommand):
    """DeepSeek 联网搜索命令。"""

    name = "ds"
    description = "DeepSeek 官方联网搜索（/ds 关键词、/ds fetch 链接、/ds stat）"
    command_prefix = "/"
    permission_level = PermissionLevel.USER
    chat_type = ChatType.ALL

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """解析并执行 /ds 子命令。

        Args:
            message_text: 已去掉前缀与命令名的子路由文本。

        Returns:
            (是否成功, 回复文本)
        """

        try:
            runtime = await get_runtime(self)
        except Exception as exc:
            logger.error(f"/ds 初始化失败：{exc!r}")
            return False, f"❌ 无法初始化 DeepSeek 搜索插件：{exc}"

        if not bool(getattr(runtime.config.plugin, "command_enabled", True)):
            return False, "⚠️ /ds 命令已在配置中关闭（[plugin].command_enabled）"

        raw = (message_text or "").strip()
        if not raw:
            return True, HELP_TEXT

        tokens = raw.split()
        sub = tokens[0].lower()

        if sub in {"help", "?", "？", "帮助", "用法"}:
            return True, HELP_TEXT
        if sub in {"stat", "status", "状态", "统计", "用量"}:
            return await self._handle_stat(runtime)
        if sub in {"fetch", "抓取", "阅读", "读"}:
            return await self._handle_fetch(runtime, tokens[1:])
        if sub in {"search", "s", "搜", "搜索", "查"}:
            return await self._handle_search(runtime, tokens[1:])
        if sub in {"test", "测试", "自检"}:
            return await self._handle_test(runtime)
        if sub in {"cache", "缓存"}:
            return await self._handle_cache(runtime, tokens[1:])
        if sub in {"reset", "重置", "清零"}:
            return await self._handle_reset(runtime)
        return await self._handle_search(runtime, tokens)

    # -- 子命令 -------------------------------------------------------------

    async def _handle_search(self, runtime: Any, tokens: list[str]) -> tuple[bool, str]:
        """执行搜索。"""

        try:
            query, options = _parse_search_args(tokens)
        except ValueError as exc:
            return False, f"❌ 参数错误：{exc}"

        if not query:
            return False, "❌ 请给出要搜索的关键词，例如：/ds 今天的 AI 新闻"

        try:
            outcome = await runtime.search(
                query,
                count=options["count"],
                need_content=options["need_content"],
                site=options["site"],
                exclude_site=options["exclude_site"],
                lang=options["lang"],
                after=options["after"],
                before=options["before"],
            )
        except DeepSeekError as exc:
            logger.warning(f"/ds 搜索失败：{exc.describe()}")
            return False, format_error(exc)

        text = render_search_text(runtime, outcome)
        return True, self._with_usage(text, runtime, outcome.cost_cny)

    async def _handle_fetch(self, runtime: Any, tokens: list[str]) -> tuple[bool, str]:
        """抓取单个链接。"""

        try:
            url, max_chars = _parse_fetch_args(tokens)
        except ValueError as exc:
            return False, f"❌ 参数错误：{exc}"

        if not url:
            return False, "❌ 请给出要抓取的链接，例如：/ds fetch https://example.com"

        # 压缩打开时展示完整压缩结果；关闭时按命令展示上限截断
        display_cap = max_chars or int(
            getattr(runtime.config.fetch, "command_max_chars", 800) or 800
        )
        try:
            outcome = await runtime.fetch(url)
        except DeepSeekError as exc:
            logger.warning(f"/ds 抓取失败：{exc.describe()}")
            return False, format_error(exc)

        text = await render_fetch_text(runtime, outcome, fallback_chars=display_cap)
        return True, self._with_usage(text, runtime, outcome.cost_cny)

    async def _handle_stat(self, runtime: Any) -> tuple[bool, str]:
        """展示用量。"""

        snapshot = runtime.usage_snapshot()
        text = format_usage_text(snapshot)
        cache = snapshot.get("cache") or {}
        if isinstance(cache, dict) and cache:
            text += (
                f"\n缓存：{cache.get('size', 0)}/{cache.get('max_entries', 0)} 条 · "
                f"命中 {cache.get('hits', 0)} 次 · 命中率 {cache.get('hit_rate', 0)}%"
            )
        return True, text

    async def _handle_cache(self, runtime: Any, args: list[str]) -> tuple[bool, str]:
        """查看或清空缓存。"""

        action = args[0].lower() if args else ""
        if action in {"clear", "clean", "清空", "清理"}:
            if not await self._has_permission(PermissionLevel.OPERATOR):
                return False, "❌ 清空缓存需要操作员及以上权限"
            removed = await runtime.clear_cache()
            return True, f"✅ 已清空缓存（{removed} 条）"

        stats = runtime.cache_stats()
        return True, (
            "🧠 缓存状态\n"
            f"条目：{stats.get('size', 0)}/{stats.get('max_entries', 0)}\n"
            f"TTL：{stats.get('ttl_seconds', 0)} 秒\n"
            f"命中：{stats.get('hits', 0)} 次 · 命中率 {stats.get('hit_rate', 0)}%"
        )

    async def _handle_test(self, runtime: Any) -> tuple[bool, str]:
        """用一次真实请求自检。"""

        if not await self._has_permission(PermissionLevel.OWNER):
            return False, "❌ 自检会消耗一次真实请求，需要所有者权限"

        try:
            outcome = await runtime.search("Neo-MoFox 插件", count=1, use_cache=False)
        except DeepSeekError as exc:
            logger.warning(f"/ds 自检失败：{exc.describe()}")
            return False, format_error(exc)

        snapshot = runtime.usage_snapshot()
        return True, (
            "✅ 自检通过\n"
            f"结果：{outcome.count} 条，耗时 {outcome.elapsed_ms}ms\n"
            f"Key 来源：{snapshot.get('api_key_source', '未知')}\n"
            f"今日已用：{snapshot.get('today_requests', 0)} 次"
        )

    async def _handle_reset(self, runtime: Any) -> tuple[bool, str]:
        """清零用量统计。"""

        if not await self._has_permission(PermissionLevel.OWNER):
            return False, "❌ 清零统计需要所有者权限"
        await runtime.reset_usage()
        return True, "✅ 用量统计已清零"

    # -- 内部工具 -----------------------------------------------------------

    def _with_usage(self, text: str, runtime: Any, cost: float) -> str:
        """按配置在回复末尾追加一行用量。"""

        if not bool(getattr(runtime.config.plugin, "show_usage_in_reply", True)):
            return text
        try:
            snapshot = runtime.usage_snapshot()
        except Exception:  # pragma: no cover - 统计失败不影响回复
            return text
        limit = int(snapshot.get("daily_limit") or 0)
        today = snapshot.get("today_requests", 0)
        today_text = f"{today}/{limit}" if limit > 0 else str(today)
        return (
            f"{text}\n\n📊 今日 {today_text} 次 · 本次 ¥{cost:.3f} · "
            f"累计 ¥{float(snapshot.get('cost_cny') or 0):.3f}"
        )

    async def _has_permission(self, level: PermissionLevel) -> bool:
        """检查触发者是否达到指定权限级别。"""

        message = getattr(self, "_message", None)
        if message is None:
            return False
        platform = str(getattr(message, "platform", "") or "")
        sender_id = str(getattr(message, "sender_id", "") or "")
        if not sender_id:
            return False
        try:
            from src.app.plugin_system.api import permission_api

            person_id = permission_api.generate_person_id(platform, sender_id)
            current = await permission_api.get_user_permission_level(person_id)
        except Exception as exc:
            logger.warning(f"权限检查失败：{exc!r}")
            return False
        try:
            return int(current) >= int(level)
        except (TypeError, ValueError):
            return False


def _parse_search_args(tokens: list[str]) -> tuple[str, dict[str, Any]]:
    """解析搜索参数。"""

    options: dict[str, Any] = {
        "count": 0,
        "need_content": False,
        "site": "",
        "exclude_site": "",
        "lang": "",
        "after": "",
        "before": "",
    }
    query_parts: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()

        if lowered in _SEARCH_FLAGS:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} 后面需要条数，例如 {token} 5")
            try:
                options["count"] = int(tokens[index + 1])
            except ValueError as exc:
                raise ValueError(f"{token} 需要整数，收到 {tokens[index + 1]!r}") from exc
            index += 2
            continue

        if lowered in _SEARCH_SWITCHES:
            options["need_content"] = True
            index += 1
            continue

        if lowered in _SEARCH_VALUES:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} 后面需要一个值")
            options[_SEARCH_VALUES[lowered]] = tokens[index + 1]
            index += 2
            continue

        if token.startswith("--") and "=" in token:
            key, _, value = token.partition("=")
            if key.lower() in _SEARCH_VALUES:
                options[_SEARCH_VALUES[key.lower()]] = value
                index += 1
                continue

        if token in {"-h", "--help"}:
            raise ValueError("使用 /ds --help 查看完整用法")

        query_parts.append(token)
        index += 1

    return " ".join(query_parts).strip(), options


def _parse_fetch_args(tokens: list[str]) -> tuple[str, int]:
    """解析抓取参数，返回 (url, max_chars)。"""

    url = ""
    max_chars = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if lowered in ("-m", "--max-chars"):
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} 后面需要字符数")
            try:
                max_chars = int(tokens[index + 1])
            except ValueError as exc:
                raise ValueError(f"{token} 需要整数") from exc
            index += 2
            continue
        if not url:
            url = token
        index += 1
    return url.strip(), max_chars
