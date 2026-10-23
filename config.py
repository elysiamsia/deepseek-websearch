"""deepseek_websearch 插件配置。

配置文件默认路径：config/plugins/deepseek_websearch/config.toml
首次加载由框架自动生成；字段含义见 README 的「配置说明」。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DeepSeekWebSearchConfig(BaseConfig):
    """DeepSeek 官方联网搜索插件配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "DeepSeek 官方 Search / Fetch 联网搜索插件配置"

    @config_section("plugin", title="插件开关", tag="plugin")
    class PluginSection(SectionBase):
        """插件总开关与交互开关。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 DeepSeek 联网搜索（关闭后 Tool 与命令都不再调用 API，不产生费用）",
            label="启用插件",
            tag="plugin",
        )
        command_enabled: bool = Field(
            default=True,
            description="是否启用 /ds 聊天命令（关闭后只保留 LLM 可调用的 Tool）",
            label="启用 /ds 命令",
            tag="plugin",
        )
        show_usage_in_reply: bool = Field(
            default=True,
            description="命令回复末尾是否附一行用量（今日次数与估算费用）",
            label="回复显示用量",
            tag="plugin",
        )
        debug_log: bool = Field(
            default=False,
            description="打开后在日志中输出每次请求的 query、条数与耗时（不输出 Key）",
            label="调试日志",
            tag="debug",
        )

    @config_section("api", title="API 与鉴权", tag="security")
    class ApiSection(SectionBase):
        """DeepSeek API 连接配置。"""

        api_key: str = Field(
            default="",
            description=(
                "DeepSeek API Key。留空时依次回退到环境变量 DEEPSEEK_API_KEY、"
                "以及 ~/.claude/deepseek-websearch.json 中的 api_key 字段"
            ),
            label="API Key",
            input_type="password",
            tag="security",
        )
        base_url: str = Field(
            default="https://api.deepseek.com",
            description="API 基址，通常保持默认；带不带 /v1 都可以，插件会自动去掉重复后缀",
            label="API 基址",
            placeholder="https://api.deepseek.com",
            tag="network",
        )
        timeout_seconds: int = Field(
            default=60,
            ge=5,
            le=300,
            description="单次请求超时秒数（抓取长网页时可能需要调大）",
            label="请求超时（秒）",
            input_type="number",
            tag="network",
        )
        max_retries: int = Field(
            default=2,
            ge=0,
            le=5,
            description="遇到 429 / 5xx / 网络错误时的重试次数（401/402/403/404 不会重试）",
            label="重试次数",
            input_type="number",
            tag="network",
        )
        use_proxy: bool = Field(
            default=True,
            description="是否读取系统代理环境变量（HTTP_PROXY / HTTPS_PROXY / NO_PROXY）",
            label="使用系统代理",
            tag="network",
        )
        proxy: str = Field(
            default="",
            description="显式代理地址，例如 http://127.0.0.1:7890；留空表示由系统代理决定",
            label="显式代理",
            placeholder="http://127.0.0.1:7890",
            tag="network",
        )
        verify_ssl: bool = Field(
            default=True,
            description="是否校验 TLS 证书；仅在本机证书链有问题时才需要关闭",
            label="校验 TLS",
            tag="security",
        )

    @config_section("search", title="搜索行为", tag="ai")
    class SearchSection(SectionBase):
        """搜索结果条数与渲染方式。"""

        default_count: int = Field(
            default=8,
            ge=1,
            le=50,
            description="未指定条数时默认返回多少条结果",
            label="默认条数",
            input_type="slider",
            step=1,
            tag="ai",
        )
        max_count: int = Field(
            default=20,
            ge=1,
            le=50,
            description="允许请求的最大条数上限（官方上限 50，调大更耗 token）",
            label="最大条数",
            input_type="slider",
            step=1,
            tag="ai",
        )
        default_need_content: bool = Field(
            default=False,
            description="是否默认同时抓取网页正文（正文很占 token，建议只在需要时开）",
            label="默认抓取正文",
            tag="ai",
        )
        max_summary_chars: int = Field(
            default=700,
            ge=100,
            le=4000,
            description="单条结果摘要的最大字符数",
            label="摘要长度上限",
            input_type="number",
            tag="text",
        )
        max_content_chars: int = Field(
            default=3000,
            ge=200,
            le=20000,
            description="单条结果正文的最大字符数（need_content 打开时生效）",
            label="正文长度上限",
            input_type="number",
            tag="text",
        )
        show_meta: bool = Field(
            default=True,
            description="结果中是否显示站点、日期、权威度与相关度",
            label="显示元信息",
            tag="ai",
        )
        include_untrusted_notice: bool = Field(
            default=True,
            description="结果末尾是否附加「外部数据不可信」的安全提示，建议保持开启",
            label="附加安全提示",
            tag="security",
        )
        auto_ori_question: bool = Field(
            default=True,
            description="是否自动把触发本次工具调用的用户消息作为 ori_question 传给搜索引擎",
            label="自动补充原始问题",
            tag="ai",
        )
        max_items_in_command: int = Field(
            default=6,
            ge=1,
            le=20,
            description="/ds 命令最多展示多少条结果（完整结果仍会提供给 LLM）",
            label="命令展示条数",
            input_type="slider",
            step=1,
            tag="text",
        )

    @config_section("fetch", title="网页抓取", tag="network")
    class FetchSection(SectionBase):
        """抓取行为与安全限制。"""

        max_chars: int = Field(
            default=12000,
            ge=500,
            le=100000,
            description="单次抓取返回正文的最大字符数，超出会截断",
            label="正文长度上限",
            input_type="number",
            tag="text",
        )
        block_private_hosts: bool = Field(
            default=True,
            description="是否拒绝抓取本机 / 内网地址（SSRF 防护，一般保持开启）",
            label="拦截内网地址",
            tag="security",
        )

    @config_section("budget", title="预算与配额", tag="database")
    class BudgetSection(SectionBase):
        """费用与配额控制。"""

        daily_request_limit: int = Field(
            default=0,
            ge=0,
            le=100000,
            description="每天最多多少次计费请求，0 表示不限制",
            label="每日请求上限",
            input_type="number",
            tag="performance",
        )
        price_per_1000: float = Field(
            default=5.0,
            ge=0.0,
            description="每千次成功请求的价格（元），用于估算费用，官方当前为 5 元",
            label="每千次单价（元）",
            input_type="number",
            step=0.5,
            tag="performance",
        )
        persist_usage: bool = Field(
            default=True,
            description="是否把累计用量写入 data/json_storage/deepseek_websearch/usage.json",
            label="保存用量统计",
            tag="database",
        )

    @config_section("cache", title="结果缓存", tag="performance")
    class CacheSection(SectionBase):
        """缓存可以显著省钱：同一 query 在 TTL 内不会重复计费。"""

        enabled: bool = Field(
            default=True,
            description="是否启用搜索结果缓存",
            label="启用缓存",
            tag="performance",
        )
        ttl_seconds: int = Field(
            default=600,
            ge=0,
            le=86400,
            description="缓存存活秒数，0 表示永不过期（配合 max_entries 做 LRU）",
            label="缓存存活（秒）",
            input_type="number",
            tag="performance",
        )
        max_entries: int = Field(
            default=128,
            ge=1,
            le=5000,
            description="最多缓存多少条结果",
            label="缓存条目上限",
            input_type="number",
            tag="performance",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    api: ApiSection = Field(default_factory=ApiSection)
    search: SearchSection = Field(default_factory=SearchSection)
    fetch: FetchSection = Field(default_factory=FetchSection)
    budget: BudgetSection = Field(default_factory=BudgetSection)
    cache: CacheSection = Field(default_factory=CacheSection)
