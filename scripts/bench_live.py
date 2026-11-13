#!/usr/bin/env python3
"""deepseek_websearch 真实耗时基准。

默认只打印测量项目与用法，不联网、不需要 Key；加 `--live` 才会真的调用
DeepSeek 的 /v1/tools/search 与 /v1/tools/fetch。

Key 读取顺序：环境变量 DEEPSEEK_API_KEY → model.toml 中 base_url 含
api.deepseek.com 的 provider。Key 只会以掩码形式出现在输出里，脚本不写任何文件。

用法：
    python scripts/bench_live.py
    python scripts/bench_live.py --live
    python scripts/bench_live.py --live --model-config config/model.toml
    python scripts/bench_live.py --live --query "Neo-MoFox 插件开发" \
        --urls https://a.example https://b.example https://c.example
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import sys
import time
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "_deepseek_websearch_bench"

DEFAULT_QUERY = "Neo-MoFox 插件开发"
DEFAULT_URLS = (
    "https://api-docs.deepseek.com/",
    "https://docs.mofox.chat/",
    "https://www.python.org/doc/",
)
DEFAULT_BASE_URL = "https://api.deepseek.com"


@dataclass(slots=True)
class Row:
    """一组测量的结果。"""

    name: str
    detail: str
    target_ms: int
    elapsed_ms: float | None = None
    note: str = ""
    error: str = ""


def _read_version() -> str:
    try:
        data = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except Exception:
        pass
    return "unknown"


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _load_client_class() -> type:
    """按包结构加载仓库里的 DeepSeekClient，不依赖插件系统。"""

    if PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE,
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载插件包：{ROOT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = module
        spec.loader.exec_module(module)
    client_module = importlib.import_module(f"{PACKAGE}.core.client")
    return client_module.DeepSeekClient


def _resolve_key(model_config: Path) -> tuple[str, str]:
    """返回 (key, 来源说明)。只读，不缓存、不写回。"""

    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key, "环境变量 DEEPSEEK_API_KEY"

    if not model_config.is_file():
        return "", f"未找到 {model_config}"

    try:
        data = tomllib.loads(model_config.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置解析失败只需要报给人看
        return "", f"{model_config} 解析失败：{exc}"

    providers = data.get("api_providers")
    if not isinstance(providers, list):
        return "", f"{model_config} 里没有 [[api_providers]]"

    for provider in providers:
        if not isinstance(provider, dict):
            continue
        base_url = str(provider.get("base_url") or "")
        if "api.deepseek.com" not in base_url:
            continue
        key = provider.get("api_key")
        if isinstance(key, list):
            key = next(
                (item for item in key if isinstance(item, str) and item.strip()),
                "",
            )
        if isinstance(key, str) and key.strip():
            name = str(provider.get("name") or "DeepSeek")
            return key.strip(), f"{model_config} 里的 {name} 配置"

    return "", f"{model_config} 里没有找到 api.deepseek.com 的 api_key"


def _build_client(client_class: type, key: str, args: argparse.Namespace) -> Any:
    kwargs: dict[str, Any] = {
        "api_key": key,
        "base_url": args.base_url,
        "timeout": args.timeout,
        # 基准要测单次真实耗时，不重试；重试会把时间拉长，也会多计费。
        "max_retries": 0,
    }
    params = inspect.signature(client_class.__init__).parameters
    if "reuse_ssl_context" in params:
        kwargs["reuse_ssl_context"] = True
    if "keepalive_seconds" in params:
        kwargs["keepalive_seconds"] = 120.0
    return client_class(**kwargs)


def _describe_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    hint = str(getattr(exc, "hint", "") or "").strip()
    if hint:
        text = f"{text}（{hint}）"
    return text


def _print_dry_run(version: str) -> None:
    print(f"deepseek_websearch {version} 耗时基准（dry-run）")
    print()
    print("默认不联网、不需要 Key。要真的测量，加 --live。")
    print()
    print("会测这五组：")
    groups = [
        ("1. 首次搜索（冷启动）", "进程内第一次 search，含 TLS 上下文与 HTTP 客户端构建", "≤ 700 ms"),
        ("2. 热搜索", "复用连接再搜一次", "≤ 350 ms"),
        ("3. 静默 8 秒后搜索", "验证 keepalive 仍在，不用重新握手", "≤ 330 ms"),
        ("4. 3 个 URL 批量抓取", "并发 fetch 三个固定页面，测总耗时", "≤ 250 ms"),
        ("5. 搜索 + fetch_top=3", "一次搜索后并行抓前三条正文", "≤ 700 ms"),
    ]
    name_width = max(_width(name) for name, _, _ in groups) + 2
    for name, detail, target in groups:
        print(f"  {_pad(name, name_width)}{_pad(target, 10)}{detail}")
    print()
    print("合计约 10 次请求，费用 ￥0.05 上下（以官方计费为准）。")
    print()
    print("命令：")
    print("  # Linux / macOS")
    print("  DEEPSEEK_API_KEY=sk-... python scripts/bench_live.py --live")
    print("  # Windows PowerShell")
    print('  $env:DEEPSEEK_API_KEY="sk-..."; python scripts/bench_live.py --live')
    print()
    print("  # 从 bot 的 model.toml 取 Key（只读，不写回）")
    print("  python scripts/bench_live.py --live --model-config config/model.toml")
    print()
    print("  # 换查询词与批量抓取的链接")
    print("  python scripts/bench_live.py --live --query \"你的问题\" \\")
    print("      --urls https://a.example https://b.example https://c.example")
    print()
    print("Key 只会以 sk-a****wxyz 这样的掩码形式打印，不会写入任何文件。")


def _print_table(rows: Iterable[Row]) -> None:
    headers = ("项目", "耗时", "目标", "说明")
    body: list[tuple[str, str, str, str]] = []
    for row in rows:
        elapsed = f"{row.elapsed_ms:.0f} ms" if row.elapsed_ms is not None else "—"
        target = f"≤ {row.target_ms} ms"
        if row.error:
            status = f"失败：{row.error}"
        elif row.elapsed_ms is None:
            status = "—"
        else:
            status = "达标" if row.elapsed_ms <= row.target_ms else "超出目标"
            if row.note:
                status = f"{status}，{row.note}"
        body.append((row.name, elapsed, target, status))

    widths = []
    for index, header in enumerate(headers):
        widths.append(max(_width(header), *( _width(line[index]) for line in body)) + 2)

    print()
    print("".join(_pad(headers[i], widths[i]) for i in range(4)).rstrip())
    print("-" * (sum(widths) + 2))
    for line in body:
        print("".join(_pad(line[i], widths[i]) for i in range(4)).rstrip())


async def _timed(coro_factory) -> tuple[float, Any, BaseException | None]:
    start = time.perf_counter()
    try:
        value = await coro_factory()
    except Exception as exc:  # noqa: BLE001 - 基准要把失败也变成一行结果
        return (time.perf_counter() - start) * 1000.0, None, exc
    return (time.perf_counter() - start) * 1000.0, value, None


async def _run_live(args: argparse.Namespace, key: str, key_source: str) -> int:
    try:
        import httpx  # noqa: F401 - 只用来确认运行环境里有它
    except ImportError:
        print("缺少 httpx。请用 bot 的虚拟环境运行：", file=sys.stderr)
        print("  D:/Neo-MoFox-bot/bot-2780184392/neo-mofox/.venv/Scripts/python.exe scripts/bench_live.py --live", file=sys.stderr)
        return 2

    try:
        client_class = _load_client_class()
    except Exception as exc:  # noqa: BLE001
        print(f"无法加载 DeepSeekClient：{_describe_error(exc)}", file=sys.stderr)
        return 2

    version = _read_version()
    urls = [url for url in (args.urls or DEFAULT_URLS) if url.strip()][:3]
    if len(urls) < 3:
        print(f"批量抓取需要 3 个 URL，当前只有 {len(urls)} 个，将按实际数量测。", file=sys.stderr)

    client = _build_client(client_class, key, args)
    masked = getattr(client, "masked_key", "") or _mask(key)
    print(f"deepseek_websearch {version} 耗时基准（live）")
    print(f"  Key       : {masked}（来源：{key_source}）")
    print(f"  基址      : {args.base_url}")
    print(f"  查询词    : {args.query}（每次略作变化，避免撞上服务端缓存）")
    print(f"  批量 URL  : {len(urls)} 个")
    print("  重试      : 关闭（max_retries=0）")
    print()

    query = args.query.strip()
    queries = [
        query,
        f"{query} 最新",
        f"{query} 教程",
        f"{query} 文档",
    ]
    rows: list[Row] = []
    failed = False

    if args.prewarm:
        print("[0/5] 预热 TLS 上下文 …", flush=True)
        try:
            tls_module = importlib.import_module(f"{PACKAGE}.core.tls")
            context = await asyncio.to_thread(tls_module.get_shared_ssl_context)
            print(f"      预热完成：{'拿到共享上下文' if context is not None else '构建失败，将回退 verify=True'}")
        except Exception as exc:  # noqa: BLE001
            print(f"      跳过：当前代码没有可用的 core/tls.py（{_describe_error(exc)}）")

    # 1. 首次搜索（冷启动）
    print("[1/5] 首次搜索（冷启动） …", flush=True)
    elapsed, results, error = await _timed(lambda: client.search(queries[0], count=args.count))
    row = Row("1. 首次搜索（冷启动）", "第一次 search，含 TLS 上下文与客户端构建", 700, elapsed)
    if error is not None:
        row.elapsed_ms = None
        row.error = _describe_error(error)
        failed = True
        print(f"      失败：{row.error}", flush=True)
    else:
        row.note = f"{len(results)} 条结果"
        print(f"      {elapsed:.0f} ms", flush=True)
    rows.append(row)

    if not failed:
        # 2. 热搜索
        print("[2/5] 热搜索 …", flush=True)
        elapsed, results, error = await _timed(lambda: client.search(queries[1], count=args.count))
        row = Row("2. 热搜索", "复用连接再搜一次", 350, elapsed)
        if error is not None:
            row.elapsed_ms = None
            row.error = _describe_error(error)
            failed = True
            print(f"      失败：{row.error}", flush=True)
        else:
            row.note = f"{len(results)} 条结果"
            print(f"      {elapsed:.0f} ms", flush=True)
        rows.append(row)

    if not failed:
        # 3. 静默 8 秒后搜索
        print("[3/5] 静默 8 秒后搜索 …", flush=True)
        await asyncio.sleep(8)
        elapsed, results, error = await _timed(lambda: client.search(queries[2], count=args.count))
        row = Row("3. 静默 8 秒后搜索", "验证 keepalive 仍在，不用重新握手", 330, elapsed)
        if error is not None:
            row.elapsed_ms = None
            row.error = _describe_error(error)
            failed = True
            print(f"      失败：{row.error}", flush=True)
        else:
            row.note = f"{len(results)} 条结果"
            print(f"      {elapsed:.0f} ms", flush=True)
        rows.append(row)

    if not failed and urls:
        # 4. 3 个 URL 批量抓取
        print("[4/5] 3 个 URL 批量抓取 …", flush=True)
        start = time.perf_counter()
        outcomes = await asyncio.gather(
            *(client.fetch(url) for url in urls),
            return_exceptions=True,
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        ok = sum(1 for item in outcomes if not isinstance(item, BaseException))
        row = Row("4. 3 个 URL 批量抓取", "并发 fetch，测总耗时", 250, elapsed)
        row.note = f"{ok}/{len(urls)} 成功"
        if ok == 0:
            first_error = next(
                (item for item in outcomes if isinstance(item, BaseException)),
                None,
            )
            row.note = ""
            row.error = _describe_error(first_error) if first_error else "全部失败"
            failed = True
            print(f"      失败：{row.error}", flush=True)
        else:
            if ok < len(urls):
                row.note += "（部分失败不影响总耗时）"
            print(f"      {elapsed:.0f} ms", flush=True)
        rows.append(row)

    if not failed and urls:
        # 5. 搜索 + fetch_top=3
        print("[5/5] 搜索 + fetch_top=3 …", flush=True)
        start = time.perf_counter()
        try:
            found = await client.search(queries[3], count=args.count)
            picked = [item.url for item in found if item.url][:3]
            if picked:
                fetched = await asyncio.gather(
                    *(client.fetch(url) for url in picked),
                    return_exceptions=True,
                )
                ok = sum(1 for item in fetched if not isinstance(item, BaseException))
            else:
                ok = 0
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000.0
            row = Row("5. 搜索 + fetch_top=3", "搜索后并行抓前三条正文", 700, None)
            row.error = _describe_error(exc)
            failed = True
            print(f"      失败：{row.error}", flush=True)
            rows.append(row)
        else:
            elapsed = (time.perf_counter() - start) * 1000.0
            row = Row("5. 搜索 + fetch_top=3", "搜索后并行抓前三条正文", 700, elapsed)
            if not picked:
                row.note = "搜索没有返回可抓取的链接"
            else:
                row.note = f"{ok}/{len(picked)} 条正文"
            print(f"      {elapsed:.0f} ms", flush=True)
            rows.append(row)

    await client.aclose()
    _print_table(rows)

    measured = [row for row in rows if row.elapsed_ms is not None]
    passed = [row for row in measured if row.elapsed_ms is not None and row.elapsed_ms <= row.target_ms]
    print()
    if failed:
        print("有项目失败，先按上面的报错排查；其余项目的结果仅供参考。")
        return 1
    print(f"{len(measured)} 组完成，{len(passed)} 组达标；本次共发出 {client.request_count} 次 HTTP 请求。")
    print("首搜若明显超过目标，确认插件已开启 prewarm 与 reuse_ssl_context；--prewarm 只提前建 TLS 上下文，客户端仍会在首次请求时创建。")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="deepseek_websearch 真实耗时基准（默认不联网，加 --live 才调用 API）",
    )
    parser.add_argument("--live", action="store_true", help="真实调用 DeepSeek API")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="基准用的查询词")
    parser.add_argument(
        "--urls",
        nargs="*",
        default=None,
        help="批量抓取用的 3 个 URL（默认内置三个公开文档页）",
    )
    parser.add_argument(
        "--model-config",
        default="config/model.toml",
        help="没有环境变量时从这里读 Key（默认 config/model.toml，相对当前目录）",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API 基址")
    parser.add_argument("--timeout", type=float, default=60.0, help="单次请求超时秒数")
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="每次搜索返回条数（默认 5）",
    )
    parser.add_argument(
        "--prewarm",
        action="store_true",
        help="先构建共享 TLS 上下文，模拟插件加载期的证书预热",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    version = _read_version()

    if not args.live:
        _print_dry_run(version)
        return 0

    model_config = Path(args.model_config).expanduser()
    key, source = _resolve_key(model_config)
    if not key:
        print("没有找到 DeepSeek API Key。", file=sys.stderr)
        print(f"  {source}", file=sys.stderr)
        print("  也可以设置环境变量 DEEPSEEK_API_KEY 后再试。", file=sys.stderr)
        return 2
    if not key.startswith("sk-") and "api.deepseek.com" in args.base_url:
        print("提示：这个 Key 不是 sk- 开头，若请求被拒请换一把官方 Key。", file=sys.stderr)

    try:
        return asyncio.run(_run_live(args, key, source))
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
