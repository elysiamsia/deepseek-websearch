"""API Key 解析。

解析顺序（先到先用）：

1. 插件配置 [api].api_key
2. 环境变量 DEEPSEEK_API_KEY
3. 兼容已有 skill 的 ~/.claude/deepseek-websearch.json（{"api_key": "sk-..."}）

任何一步失败都只是「没找到」，不会抛出异常；由调用方决定如何提示用户。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_VAR = "DEEPSEEK_API_KEY"
LEGACY_KEY_FILE = Path.home() / ".claude" / "deepseek-websearch.json"


def read_key_file(path: Path) -> str:
    """从 JSON 文件里读出 api_key 字段，任何异常都返回空串。"""

    try:
        if not path.exists():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("api_key")
    return value.strip() if isinstance(value, str) else ""


def resolve_api_key(
    configured: str = "",
    *,
    env_var: str = ENV_VAR,
    key_file: Path | None = None,
) -> tuple[str, str]:
    """解析 API Key。

    Args:
        configured: 配置里填写的 Key。
        env_var: 环境变量名。
        key_file: 兼容 Key 文件路径，None 表示使用默认路径。

    Returns:
        (Key, 来源标识)。来源取值 config / env / key_file / ""。
    """

    value = (configured or "").strip()
    if value:
        return value, "config"

    env_value = (os.environ.get(env_var) or "").strip()
    if env_value:
        return env_value, "env"

    file_value = read_key_file(key_file or LEGACY_KEY_FILE)
    if file_value:
        return file_value, "key_file"

    return "", ""
