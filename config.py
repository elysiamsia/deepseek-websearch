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
                "DeepSeek API Key。留空时依次回退到：环境变量 DEEPSEEK_API_KEY → "
                "本实例的 model_config_path（默认 config/model.toml）里 api_providers 中"
                "指向 api.deepseek.com 的 api_key。"
                "不读取任何与开发机绑定的文件，换台机器部署也能开箱复用已有的官方 Key"
            ),
            label="API Key",
            input_type="password",
            tag="security",
        )
        model_config_path: str = Field(
            default="config/model.toml",
            description=(
                "第三级回退要读的模型配置文件（相对 bot 根目录，也可写绝对路径）。"
                "随手填 Key 时这一项可以不管"
            ),
            label="模型配置文件",
            placeholder="config/model.toml",
            tag="network",
        )
        key_provider: str = Field(
            default="",
            description=(
                "强制使用 model.toml 里某个 api_providers 的 Key（按 provider 名，大小写不敏感）。"
                "留空时自动挑 base_url 指向 api.deepseek.com 的那个；"
                "只有在中转站也兼容官方工具接口时才需要手动指定"
            ),
            label="指定 provider",
            placeholder="DeepSeek",
            tag="advanced",
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

    @config_section("trigger", title="自然语言触发", tag="ai")
    class TriggerSection(SectionBase):
        """让「帮我搜一下 xxx」这类自然语言直接触发一次检索。

        命中方式：用正则匹配消息文本；命中后由插件直接调用搜索，
        再把结果作为「系统注入」追加到本轮消息里，LLM 回答时就能看到。
        """

        enabled: bool = Field(
            default=True,
            description=(
                "是否启用自然语言触发。关闭后只有 LLM 主动调用 deepseek_search "
                "或 /ds 命令才会检索，不会因为聊天里的关键词自动花钱"
            ),
            label="启用自然语言触发",
            tag="ai",
        )
        allow_group: bool = Field(
            default=True,
            description="是否在群聊里触发（群聊上下文人多，可按需关闭）",
            label="群聊启用",
            tag="ai",
        )
        allow_private: bool = Field(
            default=True,
            description="是否在私聊里触发",
            label="私聊启用",
            tag="ai",
        )
        patterns: list[str] = Field(
            default_factory=lambda: [
                r"(搜一下|搜搜|搜索一下|查一下|查查|查询一下|找一下|找找)",
                r"(帮我|帮忙|麻烦|请|替我|给我)\s*(搜|查|找)",
                r"(联网(搜|查)|上网(搜|查))",
                r"(最新|最近|近期).{0,10}(新闻|消息|动态|进展|版本|发布|价格)",
                r"(是什么情况|怎么回事|发生了什么)",
                r"有没有.{0,8}(资料|文档|教程)",
            ],
            description=(
                "触发意图的正则列表（大小写不敏感）。命中任意一条即触发；"
                "写得太宽会频繁花钱，建议先只保留明确的「搜/查/找」动词"
            ),
            label="触发正则",
            input_type="list",
            tag="ai",
        )
        count: int = Field(
            default=5,
            ge=1,
            le=20,
            description="自动检索时取几条结果",
            label="自动检索条数",
            input_type="slider",
            step=1,
            tag="ai",
        )
        need_content: bool = Field(
            default=False,
            description="自动检索时是否同时抓取正文（更慢、注入内容更长）",
            label="自动抓取正文",
            tag="ai",
        )
        max_inject_chars: int = Field(
            default=1800,
            ge=200,
            le=20000,
            description="注入到消息里的检索结果最大字符数，超出会截断",
            label="注入内容上限",
            input_type="number",
            tag="text",
        )
        max_query_chars: int = Field(
            default=120,
            ge=2,
            le=128,
            description="从消息里提取的查询词最大长度（官方 query 上限 128）",
            label="查询词长度上限",
            input_type="number",
            tag="text",
        )
        min_query_chars: int = Field(
            default=2,
            ge=1,
            le=20,
            description="清理后的查询词短于该长度就不触发（避免「搜」这类空查询）",
            label="最短查询词",
            input_type="number",
            tag="text",
        )
        cooldown_seconds: int = Field(
            default=60,
            ge=0,
            le=86400,
            description="同一聊天流两次自动触发的间隔秒数，0 表示不限制",
            label="冷却时间（秒）",
            input_type="number",
            tag="performance",
        )
        daily_limit: int = Field(
            default=200,
            ge=0,
            le=100000,
            description="自然语言触发每天最多花多少次请求，0 表示不额外限制（仍受 [budget] 约束）",
            label="每日自动触发上限",
            input_type="number",
            tag="performance",
        )
        timeout_seconds: int = Field(
            default=20,
            ge=1,
            le=120,
            description="自动检索的超时秒数，超时则放行原消息、不注入任何内容",
            label="检索超时（秒）",
            input_type="number",
            tag="network",
        )
        skip_prefixes: list[str] = Field(
            default_factory=lambda: ["/", "!", "！"],
            description="以这些前缀开头的消息视为命令，不再触发自动检索",
            label="跳过命令前缀",
            input_type="list",
            tag="text",
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
    trigger: TriggerSection = Field(default_factory=TriggerSection)
    budget: BudgetSection = Field(default_factory=BudgetSection)
    cache: CacheSection = Field(default_factory=CacheSection)
