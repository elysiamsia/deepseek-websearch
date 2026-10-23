"""DeepSeek 搜索 API 的异常类型。

本模块只定义异常、不做任何 I/O。所有异常都带一个面向用户的 hint，
由上层（Tool / Command / Service）直接拼进回复，避免把 HTTP 细节原样丢给 LLM。
"""

from __future__ import annotations


class DeepSeekError(Exception):
    """本插件所有异常的基类。

    Attributes:
        message: 原始错误信息。
        hint: 面向用户的中文修复建议。
        retryable: 该错误是否值得稍后重试。
        status: 上游 HTTP 状态码，非 HTTP 错误时为 None。
    """

    default_hint = ""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint if hint is not None else self.default_hint
        self.retryable = retryable
        self.status = status

    def describe(self) -> str:
        """返回「错误信息（修复建议）」形式的单行文本。"""

        return f"{self.message}（{self.hint}）" if self.hint else self.message


# ---------------------------------------------------------------------------
# 配置 / 参数类错误：重试没有意义，需要改配置或改调用参数
# ---------------------------------------------------------------------------


class ConfigError(DeepSeekError):
    """插件配置不完整或不可用。"""


class PluginDisabledError(ConfigError):
    """插件或命令在配置里被关闭。"""

    default_hint = "在 config/plugins/deepseek_websearch/config.toml 的 [plugin] 段打开 enabled"


class MissingAPIKeyError(ConfigError):
    """没有找到 DeepSeek API Key。"""

    default_hint = (
        "在 config/plugins/deepseek_websearch/config.toml 的 [api].api_key 填入 Key；"
        "或设置环境变量 DEEPSEEK_API_KEY；"
        "或确保本实例 config/model.toml 里有一个指向 api.deepseek.com 的 api_providers 条目"
    )


class InvalidArgumentError(DeepSeekError):
    """调用参数不合法（query 过长、日期格式错误等）。"""


class EndpointNotAllowedError(DeepSeekError):
    """试图调用 search / fetch 之外的端点。"""

    default_hint = "本插件只允许调用 DeepSeek 官方 /v1/tools/search 与 /v1/tools/fetch，不做对话与识图"


class BudgetExceededError(DeepSeekError):
    """当日请求配额已用尽。"""

    default_hint = (
        "在 config/plugins/deepseek_websearch/config.toml 的 [budget].daily_request_limit "
        "调整配额或等待次日重置"
    )


# ---------------------------------------------------------------------------
# 传输 / 上游错误
# ---------------------------------------------------------------------------


class TransportError(DeepSeekError):
    """网络层错误：连不上、超时、TLS 失败等。"""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        timeout: bool = False,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message,
            hint=hint
            or (
                "请求 DeepSeek API 超时，可调大 [api].timeout_seconds 后重试"
                if timeout
                else "网络异常，请检查服务器出网能力与代理设置（[api].use_proxy）"
            ),
            retryable=retryable,
        )
        self.timeout = timeout


class AuthError(DeepSeekError):
    """401：Key 缺失、失效或没有该接口权限。"""

    default_hint = "检查 [api].api_key 是否正确、是否已过期"


class InsufficientBalanceError(DeepSeekError):
    """402：账号余额不足。"""

    default_hint = "前往 DeepSeek 开放平台充值后再试，重试不会成功"


class ForbiddenError(DeepSeekError):
    """403：Fetch 被目标站点安全策略拒绝。"""

    default_hint = "换一个来源站点，或直接搜索该话题"


class NotFoundError(DeepSeekError):
    """404：Fetch 未能取到可用网页内容。"""

    default_hint = "该页面可能是纯 JS 渲染或已删除，换一个可信来源"


class BadRequestError(DeepSeekError):
    """400 / 413：请求不合法。"""

    default_hint = "检查 query 长度（1~128 字符）与 count 取值范围（1~50）"


class RateLimitedError(DeepSeekError):
    """429：触发 QPS 限制（Search 100/s、Fetch 25/s）。"""

    default_hint = "稍等片刻再试；插件已自动退避重试，仍然失败说明并发过高"


class UpstreamError(DeepSeekError):
    """5xx / 504：DeepSeek 服务端问题。"""

    default_hint = "这是上游服务异常，稍后重试即可"
