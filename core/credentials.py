"""API Key 解析（零本机依赖）。

解析顺序：

1. 插件配置 `[api].api_key`
2. 环境变量 `DEEPSEEK_API_KEY`
3. **本实例的 `config/model.toml`**：从 `[[api_providers]]` 里挑一个指向
   `api.deepseek.com` 的条目，复用这个 bot 已经配好的官方 Key

设计约束：不读取用户主目录、不读取任何与「开发机」绑定的文件。
别人部署 bot 时通常只有 `model.toml` 里的 Key 可用，所以第三级回退必须落在
实例自己的配置文件上，而不是某个 agent 环境的私有路径。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: 环境变量名。
ENV_VAR = "DEEPSEEK_API_KEY"

#: 默认的模型配置文件（相对 bot 根目录）。
DEFAULT_MODEL_CONFIG = "config/model.toml"

#: 视为「官方 DeepSeek 端点」的主机。
OFFICIAL_HOSTS = ("api.deepseek.com",)


def _candidate_paths(configured: str, base_dir: Path | None = None) -> list[Path]:
    """算出 model.toml 的候选路径（去重、保持顺序）。

    Args:
        configured: 配置里的路径，可能是相对路径。
        base_dir: 显式指定的基目录（测试用）；为空时按 CWD 与插件所在 bot 根目录推断。

    Returns:
        候选路径列表。
    """

    path = Path(configured or DEFAULT_MODEL_CONFIG)
    if path.is_absolute():
        return [path]

    if base_dir is not None:
        return [Path(base_dir) / path]

    candidates = [Path.cwd() / path]
    # 插件被放在 <bot>/plugins/deepseek_websearch/core/credentials.py 时，
    # parents[3] 就是 bot 根目录；.mfp 解包到临时目录时这里只会多一个无效候选。
    try:
        bot_root = Path(__file__).resolve().parents[3]
        candidates.append(bot_root / path)
    except IndexError:  # pragma: no cover - 目录层级异常时忽略
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _load_providers(path: Path) -> list[dict[str, Any]]:
    """读取 TOML 里的 api_providers（列表或表两种写法都兼容）。"""

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return []

    raw = data.get("api_providers")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        providers: list[dict[str, Any]] = []
        for name, value in raw.items():
            if isinstance(value, dict):
                providers.append({"name": name, **value})
        return providers
    return []


def _extract_key(value: Any) -> str:
    """从 api_key 字段取值。

    model.toml 里 api_key 既可能写成字符串，也可能写成数组（多 Key 轮换）：

        api_key = "sk-xxx"
        api_key = ["sk-xxx", "sk-yyy"]

    两种都取第一个非空字符串，绝不把列表的 repr 当成 Key 发出去。
    """

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _host(url: str) -> str:
    """取出 URL 的主机名（非法 URL 返回空串）。"""

    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_official_endpoint(url: str) -> bool:
    """判断是否指向 DeepSeek 官方端点。"""

    host = _host(url)
    if not host:
        return False
    return any(host == item or host.endswith(f".{item}") for item in OFFICIAL_HOSTS)


def pick_provider_key(
    providers: list[dict[str, Any]], provider_name: str = ""
) -> tuple[str, str]:
    """从 api_providers 里挑一个可用的 Key。

    Args:
        providers: api_providers 条目列表。
        provider_name: 显式指定 provider 名（大小写不敏感）；为空则自动挑官方端点。

    Returns:
        (Key, 来源标识)；找不到时返回 ("", "")。
    """

    wanted = (provider_name or "").strip().lower()

    # 1) 用户显式指定 provider
    if wanted:
        for provider in providers:
            name = str(provider.get("name") or "").strip()
            if name.lower() != wanted:
                continue
            key = _extract_key(provider.get("api_key"))
            if key:
                return key, f"model.toml:{name}"
        return "", ""

    # 2) 自动：只认官方端点，避免把中转站 Key 发到 api.deepseek.com
    official = [
        provider
        for provider in providers
        if _extract_key(provider.get("api_key"))
        and is_official_endpoint(str(provider.get("base_url") or ""))
    ]
    if not official:
        return "", ""

    # 同名的情况下优先名字里带 deepseek 的，其次保持文件里的顺序
    official.sort(key=lambda item: 0 if "deepseek" in str(item.get("name") or "").lower() else 1)
    chosen = official[0]
    name = str(chosen.get("name") or "?").strip()
    return _extract_key(chosen.get("api_key")), f"model.toml:{name}"


def resolve_api_key(
    configured: str = "",
    *,
    env_var: str = ENV_VAR,
    model_config_path: str = DEFAULT_MODEL_CONFIG,
    provider_name: str = "",
    base_dir: Path | None = None,
) -> tuple[str, str]:
    """按「配置 → 环境变量 → model.toml」解析 API Key。

    Args:
        configured: 插件配置里的 Key。
        env_var: 环境变量名。
        model_config_path: 模型配置文件路径（相对 bot 根目录或绝对路径）。
        provider_name: 强制使用 model.toml 里某个 provider 的 Key。
        base_dir: 显式基目录（测试用）。

    Returns:
        (Key, 来源标识)，来源取值 config / env / model.toml:<provider> / ""。
    """

    value = (configured or "").strip()
    if value:
        return value, "config"

    env_value = (os.environ.get(env_var) or "").strip()
    if env_value:
        return env_value, "env"

    for path in _candidate_paths(model_config_path, base_dir):
        if not path.is_file():
            continue
        key, source = pick_provider_key(_load_providers(path), provider_name)
        if key:
            return key, source

    return "", ""
