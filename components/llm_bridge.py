"""小模型桥：把框架的 llm_api 包成「一次文本补全」。

插件核心（core/）不依赖框架，压缩只要求一个
call_llm(system, user, max_tokens) -> str 协程，本模块就是它的真实实现：

- 先按【任务名】解析（model.toml 的 model_tasks.*，默认 utils_small），
  失败再按【模型名】解析；
- 每次调用把 temperature / max_tokens / tool_call_compat 收到压缩场景需要的值，
  避免沿用任务的通用额度；
- 只发 SYSTEM + USER 两段纯文本，不挂工具、不读聊天上下文。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("deepseek_websearch")


class SmallModelBridge:
    """把「指定小模型跑一次补全」封装成协程。

    Args:
        config: 插件配置对象（提供 [compress] 节）。
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._model_set_cache: dict[tuple[str, int], Any] = {}

    async def complete(self, system: str, user: str, max_tokens: int) -> str:
        """执行一次补全，返回纯文本。"""

        from src.app.plugin_system.api import llm_api
        from src.kernel.llm import LLMPayload, ROLE, Text

        model_set = self._model_set(llm_api, int(max_tokens))
        request = llm_api.create_llm_request(model_set, "deepseek_websearch_compress")
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user)))

        response = await request.send(stream=False)
        return str(await response or "")

    # -- 模型解析 -----------------------------------------------------------

    def _model_set(self, llm_api: Any, max_tokens: int) -> Any:
        """解析并缓存 ModelSet。"""

        compress = getattr(self._config, "compress", None)
        spec = str(getattr(compress, "model", "") or "utils_small").strip() or "utils_small"
        cache_key = (spec, int(max_tokens))
        cached = self._model_set_cache.get(cache_key)
        if cached is not None:
            return cached

        temperature = float(getattr(compress, "temperature", 0.3) or 0.3)
        model_set = self._resolve(llm_api, spec, temperature, int(max_tokens))
        tightened = [
            {
                **entry,
                "temperature": temperature,
                "max_tokens": int(max_tokens),
                "tool_call_compat": False,
            }
            for entry in model_set
        ]
        self._model_set_cache[cache_key] = tightened
        return tightened

    @staticmethod
    def _resolve(llm_api: Any, spec: str, temperature: float, max_tokens: int) -> Any:
        """先按任务名、再按模型名解析。"""

        task_error: Exception | None = None
        try:
            model_set = llm_api.get_model_set_by_task(spec)
            logger.info(f"压缩模型 {spec!r} 按【任务名】解析")
            return model_set
        except Exception as exc:  # 不是任务名，继续按模型名尝试
            task_error = exc

        try:
            model_set = llm_api.get_model_set_by_name(
                spec, temperature=temperature, max_tokens=max_tokens
            )
            logger.info(f"压缩模型 {spec!r} 按【模型名】解析")
            return model_set
        except Exception as exc:
            raise RuntimeError(
                f"压缩模型 {spec!r} 无法解析：既不是 model_tasks 里的任务名，"
                f"也不是 models 里的模型名（任务名错误：{task_error}；模型名错误：{exc}）"
            ) from exc
