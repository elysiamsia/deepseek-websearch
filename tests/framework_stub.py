"""Neo-MoFox 框架的最小替身，用于在脱离主程序的情况下跑单元测试。

为什么需要它：插件代码依赖 src.app.plugin_system.* 与 src.core.components.*，
直接 import 会把测试绑到「本机恰好装了哪个版本的 bot」上。本文件只实现插件真正
用到的那部分契约（配置字段、组件基类、日志、存储、权限），行为与主程序一致但零依赖。

安装方式：调用 install()，它会在 sys.modules 里注册 src / plugins 相关模块。
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import types
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "deepseek_websearch"

#: 测试里可以改写的权限级别表（person_id -> PermissionLevel）。
PERMISSION_LEVELS: dict[str, "PermissionLevel"] = {}
#: 测试里可以改写的存储内容（(store, name) -> dict）。
STORAGE: dict[tuple[str, str], dict[str, Any]] = {}
#: 测试里收集到的组件类。
REGISTERED_PLUGINS: list[type] = []


# ---------------------------------------------------------------------------
# 配置字段
# ---------------------------------------------------------------------------


class FieldInfo:
    """替身版 Field 描述符。"""

    def __init__(self, default: Any = ..., *, default_factory: Any = None, **kwargs: Any) -> None:
        self.default = default
        self.default_factory = default_factory
        self.kwargs = kwargs

    def materialize(self) -> Any:
        if self.default_factory is not None:
            return self.default_factory()
        if self.default is ...:
            return None
        return self.default


def Field(default: Any = ..., *, default_factory: Any = None, **kwargs: Any) -> Any:  # noqa: N802
    """替身版 Field。"""

    return FieldInfo(default, default_factory=default_factory, **kwargs)


def config_section(name: str, **kwargs: Any) -> Any:
    """替身版 config_section：只记录节名。"""

    def decorator(cls: type) -> type:
        cls.__config_section_name__ = name
        cls.__config_section_kwargs__ = kwargs
        return cls

    return decorator


def _collect_defaults(cls: type) -> dict[str, Any]:
    """按 MRO 收集类属性里的默认值。"""

    defaults: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        # 必须先取出注解：直接访问 klass.__annotations__ 会在部分类上惰性创建该属性，
        # 从而在遍历 vars(klass) 时触发 "dictionary changed size during iteration"。
        annotations = getattr(klass, "__annotations__", None) or {}
        for key, value in list(vars(klass).items()):
            if key.startswith("__"):
                continue
            if isinstance(value, FieldInfo):
                defaults[key] = value.materialize()
            elif key in annotations:
                defaults[key] = value
    return defaults


class SectionBase:
    """替身版配置节基类。"""

    def __init__(self, **overrides: Any) -> None:
        defaults = _collect_defaults(type(self))
        for key, value in defaults.items():
            if key in overrides:
                continue
            setattr(self, key, value)
        for key, value in overrides.items():
            if key not in defaults:
                raise TypeError(f"{type(self).__name__} 没有字段 {key!r}")
            setattr(self, key, value)


class BaseConfig(SectionBase):
    """替身版插件配置基类。"""

    name = "config"
    description = ""


# ---------------------------------------------------------------------------
# 组件基类
# ---------------------------------------------------------------------------


class BasePlugin:
    """替身版插件基类。"""

    plugin_name = "unknown_plugin"
    plugin_version = ""
    plugin_description = ""
    configs: list[type] = []
    dependent_components: list[str] = []
    dependencies: list[str] = []

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def get_components(self) -> list[type]:  # pragma: no cover - 子类实现
        raise NotImplementedError

    async def on_plugin_loaded(self) -> None:
        """加载钩子。"""

    async def on_plugin_unloaded(self) -> None:
        """卸载钩子。"""


class BaseTool:
    """替身版 Tool 基类，带一个极简的 schema 生成器。"""

    name = ""
    description = ""
    chatter_allow: list[str] = []
    dependencies: list[str] = []

    def __init__(self, plugin: Any = None) -> None:
        self.plugin = plugin
        self.stream_id = ""
        self.trigger_message: Any = None

    def _bind_runtime_context(self, *, stream_id: str | None = None, message: Any = None) -> None:
        self.stream_id = str(stream_id or "")
        self.trigger_message = message

    def get_current_stream_id(self) -> str:
        return str(getattr(self.trigger_message, "stream_id", "") or self.stream_id or "")

    @classmethod
    def _schema(cls) -> dict[str, Any]:
        """按 inspect + Annotated 生成简化 schema，用于校验参数文档是否齐全。"""

        from typing import get_type_hints

        signature = inspect.signature(cls.execute)
        try:
            hints = get_type_hints(cls.execute, include_extras=True)
        except Exception:  # pragma: no cover - 注解解析失败时退化为原始注解
            hints = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            annotation = hints.get(name, param.annotation)
            description = ""
            metadata = getattr(annotation, "__metadata__", None)
            if metadata:
                for item in metadata:
                    if isinstance(item, str):
                        description = item
                        break
                annotation = getattr(annotation, "__origin__", annotation)
            type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
            properties[name] = {
                "type": type_map.get(annotation, "string"),
                "description": description,
            }
            if param.default is inspect.Parameter.empty:
                required.append(name)
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }


class BaseCommand:
    """替身版 Command 基类。"""

    name = ""
    description = ""
    command_prefix = "/"
    permission_level = 2
    chat_type = "all"
    dependencies: list[str] = []

    def __init__(self, plugin: Any = None, stream_id: str = "", message_id: str = "", message: Any = None) -> None:
        self.plugin = plugin
        self.stream_id = stream_id
        self.message_id = message_id
        self._message = message

    async def execute(self, message_text: str) -> tuple[bool, str]:  # pragma: no cover
        raise NotImplementedError


class BaseService:
    """替身版 Service 基类。"""

    name = ""
    description = ""
    version = "1.0.0"
    dependencies: list[str] = []

    def __init__(self, plugin: Any = None) -> None:
        self.plugin = plugin


def register_plugin(cls: type) -> type:
    """替身版注册装饰器。"""

    REGISTERED_PLUGINS.append(cls)
    return cls


def cmd_route(*path: str) -> Any:  # pragma: no cover - 本插件未使用
    """替身版路由装饰器。"""

    def decorator(func: Any) -> Any:
        func._cmd_route = list(path)
        return func

    return decorator


# ---------------------------------------------------------------------------
# 类型 / 日志 / 存储 / 权限 / 配置 API
# ---------------------------------------------------------------------------


class PermissionLevel(IntEnum):
    """权限级别。"""

    GUEST = 1
    USER = 2
    OPERATOR = 3
    OWNER = 4

    @classmethod
    def from_string(cls, value: str) -> "PermissionLevel":
        mapping = {
            "guest": cls.GUEST,
            "user": cls.USER,
            "operator": cls.OPERATOR,
            "owner": cls.OWNER,
        }
        return mapping[str(value).strip().lower()]


class ChatType(str, Enum):
    """聊天类型。"""

    PRIVATE = "private"
    GROUP = "group"
    DISCUSS = "discuss"
    ALL = "all"


class FakeLogger:
    """记录日志的替身日志器。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.records: list[tuple[str, str]] = []

    def _add(self, level: str, message: str) -> None:
        self.records.append((level, message))

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._add("debug", str(message))

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._add("info", str(message))

    def success(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._add("success", str(message))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._add("warning", str(message))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._add("error", str(message))

    def messages(self, level: str | None = None) -> list[str]:
        return [msg for lvl, msg in self.records if level is None or lvl == level]


LOGGERS: dict[str, FakeLogger] = {}


def get_logger(name: str, display: str | None = None, color: Any = None, enable_event_broadcast: bool = True) -> FakeLogger:
    """替身版 get_logger。"""

    if name not in LOGGERS:
        LOGGERS[name] = FakeLogger(name)
    return LOGGERS[name]


class COLOR(str, Enum):
    """占位颜色枚举。"""

    BLUE = "blue"
    GREEN = "green"
    RED = "red"
    ORANGE = "orange"


async def save_json(store_name: str, name: str, data: dict[str, Any]) -> None:
    """替身版存储写入。"""

    STORAGE[(store_name, name)] = dict(data)


async def load_json(store_name: str, name: str) -> dict[str, Any] | None:
    """替身版存储读取。"""

    value = STORAGE.get((store_name, name))
    return dict(value) if value is not None else None


async def delete_json(store_name: str, name: str) -> bool:
    """替身版存储删除。"""

    return STORAGE.pop((store_name, name), None) is not None


def generate_person_id(platform: str, user_id: str) -> str:
    """替身版 person_id 生成。"""

    if not platform or not user_id:
        raise ValueError("platform 与 user_id 都不能为空")
    return f"{platform}:{user_id}"


async def get_user_permission_level(person_id: str) -> PermissionLevel:
    """替身版权限查询。"""

    return PERMISSION_LEVELS.get(person_id, PermissionLevel.USER)


def load_config(plugin_name: str, config_class: type, **kwargs: Any) -> Any:
    """替身版配置加载。"""

    return config_class()


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def _ensure_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def install() -> None:
    """把所有替身注册进 sys.modules（幂等）。"""

    if getattr(install, "_done", False):
        return

    src = _ensure_module("src")
    src.__path__ = []  # type: ignore[attr-defined]
    app = _ensure_module("src.app")
    app.__path__ = []  # type: ignore[attr-defined]
    plugin_system = _ensure_module("src.app.plugin_system")
    plugin_system.__path__ = []  # type: ignore[attr-defined]
    api = _ensure_module("src.app.plugin_system.api")
    api.__path__ = []  # type: ignore[attr-defined]
    core = _ensure_module("src.core")
    core.__path__ = []  # type: ignore[attr-defined]
    components = _ensure_module("src.core.components")
    components.__path__ = []  # type: ignore[attr-defined]

    base_module = _ensure_module("src.app.plugin_system.base")
    base_module.BaseConfig = BaseConfig
    base_module.BaseTool = BaseTool
    base_module.BaseCommand = BaseCommand
    base_module.BaseService = BaseService
    base_module.BasePlugin = BasePlugin
    base_module.Field = Field
    base_module.SectionBase = SectionBase
    base_module.config_section = config_section
    base_module.register_plugin = register_plugin
    base_module.cmd_route = cmd_route

    log_api = _ensure_module("src.app.plugin_system.api.log_api")
    log_api.get_logger = get_logger
    log_api.COLOR = COLOR

    storage_api = _ensure_module("src.app.plugin_system.api.storage_api")
    storage_api.save_json = save_json
    storage_api.load_json = load_json
    storage_api.delete_json = delete_json

    permission_api = _ensure_module("src.app.plugin_system.api.permission_api")
    permission_api.generate_person_id = generate_person_id
    permission_api.get_user_permission_level = get_user_permission_level
    permission_api.PermissionLevel = PermissionLevel

    config_api = _ensure_module("src.app.plugin_system.api.config_api")
    config_api.load_config = load_config

    types_module = _ensure_module("src.core.components.types")
    types_module.PermissionLevel = PermissionLevel
    types_module.ChatType = ChatType

    install._done = True  # type: ignore[attr-defined]


def load_package() -> types.ModuleType:
    """把插件目录以 deepseek_websearch 包名装载进 sys.modules。"""

    install()
    existing = sys.modules.get(PACKAGE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        str(ROOT / "__init__.py"),
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("无法为插件目录创建模块规范")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


def import_component(module_name: str) -> types.ModuleType:
    """装载包内子模块，例如 core.query / components.tools。"""

    load_package()
    return importlib.import_module(f"{PACKAGE_NAME}.{module_name}")


def reset_state() -> None:
    """清空测试间共享的状态。"""

    PERMISSION_LEVELS.clear()
    STORAGE.clear()
    REGISTERED_PLUGINS.clear()
    for logger in LOGGERS.values():
        logger.records.clear()


def build_config(**overrides: Any) -> Any:
    """构造一份插件配置，可按节覆盖字段。"""

    config_module = import_component("config")
    config = config_module.DeepSeekWebSearchConfig()
    for section_name, values in overrides.items():
        section = getattr(config, section_name)
        for key, value in values.items():
            if not hasattr(section, key):
                raise AttributeError(f"{section_name}.{key} 不存在")
            setattr(section, key, value)
    return config
