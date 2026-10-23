#!/usr/bin/env python3
"""把插件安装到 Neo-MoFox 的 plugins 目录。

插件目录名必须是 deepseek_websearch（下划线），否则框架按目录名构建包名后
相对导入会失败。本脚本负责按正确名字复制，并跳过测试与脚本。

用法：
    python scripts/install_local.py --plugins-dir "<bot>/plugins"
    python scripts/install_local.py --plugins-dir "<bot>/plugins" --force
    MOFOX_PLUGINS_DIR="<bot>/plugins" python scripts/install_local.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_DIR_NAME = "deepseek_websearch"
SOURCE_FILES = ("manifest.json", "plugin.py", "config.py", "__init__.py", "README.md", "LICENSE")
SOURCE_DIRS = ("core", "components")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def copy_plugin(target: Path) -> int:
    """把插件文件复制到 target，返回复制文件数。"""

    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for name in SOURCE_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, target / name)
            count += 1
    for name in SOURCE_DIRS:
        source_dir = ROOT / name
        if not source_dir.is_dir():
            continue
        for path in source_dir.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            relative = path.relative_to(ROOT)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            count += 1
    return count


def looks_like_plugins_dir(path: Path) -> bool:
    """粗略判断这是不是 Neo-MoFox 的 plugins 目录。"""

    if not path.is_dir():
        return False
    if (path / "parser").exists() or (path / "utility_commands").exists():
        return True
    for child in path.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            return True
        if child.suffix in (".mfp", ".zip"):
            return True
    return False


def main() -> int:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="安装 deepseek_websearch 到 Neo-MoFox")
    parser.add_argument(
        "--plugins-dir",
        default=os.environ.get("MOFOX_PLUGINS_DIR", ""),
        help="Neo-MoFox 的 plugins 目录（也可用环境变量 MOFOX_PLUGINS_DIR）",
    )
    parser.add_argument("--force", action="store_true", help="目标已存在时先删除再安装")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作")
    args = parser.parse_args()

    if not args.plugins_dir:
        print("❌ 需要 --plugins-dir 或环境变量 MOFOX_PLUGINS_DIR", file=sys.stderr)
        return 2

    plugins_dir = Path(args.plugins_dir).expanduser().resolve()
    if not plugins_dir.is_dir():
        print(f"❌ 目录不存在：{plugins_dir}", file=sys.stderr)
        return 2
    if not looks_like_plugins_dir(plugins_dir):
        print(f"⚠️  {plugins_dir} 看起来不像 Neo-MoFox 的 plugins 目录，继续执行前请确认")

    target = plugins_dir / PLUGIN_DIR_NAME
    if target.exists():
        if not args.force:
            print(f"❌ 已存在：{target}\n   如需覆盖请加 --force", file=sys.stderr)
            return 1
        print(f"🗑  删除旧版本：{target}")
        if not args.dry_run:
            shutil.rmtree(target)

    print(f"📁 安装到：{target}")
    if args.dry_run:
        print("（dry-run：未写入任何文件）")
        return 0

    count = copy_plugin(target)
    print(f"✅ 已复制 {count} 个文件")
    print()
    print("接下来：")
    print("  1. 完全重启 Neo-MoFox（插件与配置都不热加载）")
    print("  2. 首次启动会生成 config/plugins/deepseek_websearch/config.toml")
    print("  3. 填好 [api].api_key（或设置 DEEPSEEK_API_KEY）后再次重启")
    print("  4. 私聊 bot 发送 /ds test 自检（消耗 1 次请求）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
