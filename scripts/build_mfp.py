#!/usr/bin/env python3
"""把插件目录打包为 .mfp（MoFox Plugin，本质是 ZIP）。

只打包运行需要的文件，跳过测试、脚本、文档与缓存目录，产物可复现
（固定成员时间戳与顺序，同样的源码得到同样的 SHA256）。

用法：
    python scripts/build_mfp.py                 # 输出到 dist/
    python scripts/build_mfp.py -o release      # 自定义输出目录
    python scripts/build_mfp.py --format zip    # 打普通 zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 必须打进包里的单文件。
INCLUDE_FILES = (
    "manifest.json",
    "plugin.py",
    "config.py",
    "__init__.py",
    "README.md",
    "LICENSE",
)

#: 必须打进包里的目录。
INCLUDE_DIRS = ("core", "components")

#: 永远跳过的目录 / 后缀。
EXCLUDE_DIRS = {"__pycache__", ".git", ".github", "tests", "scripts", "docs", "dist", "build", ".venv"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".mfp", ".zip", ".log"}

#: 固定时间戳，保证产物可复现。
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def collect_files() -> list[Path]:
    """收集需要打包的文件（相对插件的路径）。"""

    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif name in ("manifest.json", "plugin.py"):
            raise SystemExit(f"缺少必需文件：{name}")

    for dir_name in INCLUDE_DIRS:
        directory = ROOT / dir_name
        if not directory.is_dir():
            raise SystemExit(f"缺少必需目录：{dir_name}")
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            files.append(path)

    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix())


def read_manifest() -> dict:
    """读取并校验 manifest。"""

    manifest_path = ROOT / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("manifest.json 不存在")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"manifest.json 不是合法 JSON：{exc}") from exc

    for field in ("name", "version", "entry_point"):
        if not data.get(field):
            raise SystemExit(f"manifest.json 缺少字段：{field}")
    if not (ROOT / data["entry_point"]).is_file():
        raise SystemExit(f"入口文件不存在：{data['entry_point']}")
    return data


def build(output_dir: Path, fmt: str) -> Path:
    """执行打包并返回产物路径。"""

    manifest = read_manifest()
    files = collect_files()

    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "mfp" if fmt == "mfp" else "zip"
    archive_path = output_dir / f"{manifest['name']}-{manifest['version']}.{suffix}"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    size = archive_path.stat().st_size

    print("📦 构建完成")
    print(f"  插件      {manifest['name']} v{manifest['version']}")
    print(f"  文件数    {len(files)}")
    print(f"  包大小    {size / 1024:.1f} KiB")
    print(f"  SHA256    {digest}")
    print(f"  输出      {archive_path}")
    print()
    print("安装：把该文件放进 Neo-MoFox 的 plugins/ 目录后重启 bot")
    return archive_path


def main() -> int:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="打包 Neo-MoFox 插件为 .mfp")
    parser.add_argument("-o", "--output", default="dist", help="输出目录（默认 dist/）")
    parser.add_argument("--format", choices=("mfp", "zip"), default="mfp", help="包格式（默认 mfp）")
    args = parser.parse_args()

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    build(output_dir, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
