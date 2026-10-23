"""deepseek_websearch 插件入口。

只用 DeepSeek 官方「搜索 / 抓取」两个接口：
- POST /v1/tools/search
- POST /v1/tools/fetch

不调用对话、识图、embedding 等任何其他能力。核心实现见 core/，组件见 components/。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .components.command import DeepSeekCommand
from .components.service import DeepSeekWebSearchService
from .components.tools import DeepSeekFetchTool, DeepSeekSearchTool
from .config import DeepSeekWebSearchConfig
from .core.credentials import resolve_api_key
from .core.runtime import Runtime

logger = get_logger("deepseek_websearch")


@register_plugin
class DeepSeekWebSearchPlugin(BasePlugin):
    """DeepSeek 官方联网搜索插件。"""

    plugin_name = "deepseek_websearch"

    configs: list[type] = [DeepSeekWebSearchConfig]
    dependent_components: list[str] = []

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._runtime: Runtime | None = None
        self._runtime_lock = asyncio.Lock()

    def get_components(self) -> list[type]:
        """返回插件提供的组件类。"""

        return [
            DeepSeekSearchTool,
            DeepSeekFetchTool,
            DeepSeekCommand,
            DeepSeekWebSearchService,
        ]

    # -- 运行时 -------------------------------------------------------------

    async def get_runtime(self) -> Runtime:
        """获取（必要时创建）运行时实例。"""

        if self._runtime is not None:
            return self._runtime

        async with self._runtime_lock:
            if self._runtime is None:
                config = self._resolve_config()
                runtime = Runtime(config, logger=logger)
                await runtime.start()
                self._runtime = runtime
                logger.info("DeepSeek 搜索运行时已初始化")
        return self._runtime

    def _resolve_config(self) -> Any:
        """拿到配置：优先用框架注入的，其次按需加载，最后退回默认值。"""

        if self.config is not None:
            return self.config

        try:
            from src.app.plugin_system.api import config_api

            self.config = config_api.load_config(self.plugin_name, DeepSeekWebSearchConfig)
        except Exception as exc:
            logger.warning(f"加载配置文件失败，使用默认配置：{exc!r}")

        if self.config is None:
            self.config = DeepSeekWebSearchConfig()
        return self.config

    # -- 生命周期 -----------------------------------------------------------

    async def on_plugin_loaded(self) -> None:
        """插件加载完成：预热运行时并输出配置摘要。"""

        try:
            runtime = await self.get_runtime()
        except Exception as exc:
            logger.error(f"DeepSeek 搜索插件初始化失败：{exc!r}")
            return

        config = runtime.config
        enabled = bool(getattr(config.plugin, "enabled", True))
        cache_ttl = int(getattr(config.cache, "ttl_seconds", 600))
        cache_enabled = bool(getattr(config.cache, "enabled", True))
        limit = int(getattr(config.budget, "daily_request_limit", 0) or 0)
        limit_text = f"每日 {limit} 次" if limit > 0 else "不限"

        key, source = resolve_api_key(str(getattr(config.api, "api_key", "") or ""))
        if not key:
            logger.warning(
                "未找到 DeepSeek API Key，联网搜索会在首次调用时报错。"
                "请在 config/plugins/deepseek_websearch/config.toml 填写 [api].api_key，"
                "或设置环境变量 DEEPSEEK_API_KEY"
            )
        else:
            masked = f"{key[:4]}****{key[-4:]}" if len(key) > 8 else "****"
            logger.info(f"DeepSeek API Key 已就绪（来源：{source}，{masked}）")

        logger.info(
            f"deepseek_websearch 已加载：启用={enabled}，"
            f"缓存={'开' if cache_enabled else '关'}（TTL {cache_ttl}s），配额={limit_text}"
        )

    async def on_plugin_unloaded(self) -> None:
        """插件卸载：落盘用量并释放连接。"""

        runtime = self._runtime
        self._runtime = None
        if runtime is None:
            return
        try:
            await runtime.aclose()
        except Exception as exc:
            logger.warning(f"关闭 DeepSeek 搜索运行时失败：{exc!r}")
