"""进程内复用的 TLS 上下文。

为什么要复用：httpx 默认走 ssl.create_default_context(cafile=certifi.where())，
在本机上建一次要 850~890ms。同一份 PEM 改用 cadata 只要约 20ms，再做成进程内
单例，整个进程就只付一次这个成本。
"""

from __future__ import annotations

import ssl
import threading
from pathlib import Path

_lock = threading.Lock()
_context: ssl.SSLContext | None = None
_failed = False


def _build_context() -> ssl.SSLContext:
    """用 certifi 的根证书构建一个只信任它们的客户端上下文。"""

    import certifi  # 延迟导入：证书包缺失时也落到同一个失败分支

    pem = Path(certifi.where()).read_bytes().decode("utf-8")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=pem)
    return context


def get_shared_ssl_context() -> ssl.SSLContext | None:
    """返回进程内共享的 TLS 上下文，构建失败时返回 None。"""

    global _context, _failed

    if _context is not None or _failed:
        return _context
    with _lock:
        if _context is not None or _failed:
            return _context
        try:
            _context = _build_context()
        except Exception:
            # 记住失败：慢路径重试一次就够，不必每次请求都重来
            _failed = True
    return _context


def reset_ssl_context_cache() -> None:
    """清空缓存与失败标记（仅供测试）。"""

    global _context, _failed

    with _lock:
        _context = None
        _failed = False
