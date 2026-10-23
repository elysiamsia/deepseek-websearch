"""组件共用的小工具。"""

from __future__ import annotations

from typing import Any


def extract_trigger_text(message: Any, limit: int = 200) -> str:
    """从触发消息里提取用户文本，用作搜索的 ori_question。

    Args:
        message: 框架注入的 Message 对象，可能为 None。
        limit: 最长保留字符数。

    Returns:
        清洗后的单行文本；取不到时返回空串。
    """

    if message is None:
        return ""
    for attr in ("processed_plain_text", "content"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            collapsed = " ".join(value.split())
            return collapsed[:limit]
    return ""


async def get_runtime(component: Any) -> Any:
    """从组件拿到所属插件的 Runtime。

    Args:
        component: Tool / Command / Service 实例。

    Returns:
        Runtime 实例。

    Raises:
        RuntimeError: 插件实例不提供 get_runtime 时。
    """

    plugin = getattr(component, "plugin", None)
    getter = getattr(plugin, "get_runtime", None)
    if getter is None:
        raise RuntimeError("插件未正确初始化：缺少 get_runtime()")
    return await getter()
