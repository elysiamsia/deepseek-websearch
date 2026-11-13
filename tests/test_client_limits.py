"""传输层参数测试：SSL 上下文复用与 keepalive（完全不联网）。"""

from __future__ import annotations

import ssl
import unittest
from unittest import mock

import httpx

from tests import framework_stub

client_mod = framework_stub.import_component("core.client")
tls = framework_stub.import_component("core.tls")


class FakeResponse:
    """最小的 httpx 响应替身。"""

    status_code = 200
    headers: dict[str, str] = {}
    text = "{}"


class FakeAsyncClient:
    """捕获构造参数的 httpx.AsyncClient 替身。"""

    instances: list["FakeAsyncClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeAsyncClient.instances.append(self)

    async def post(self, url, json=None, headers=None):
        return FakeResponse()


def make_client(**kwargs):
    """构造一个走真实 _httpx_post 的客户端。"""

    return client_mod.DeepSeekClient(
        api_key="sk-test-12345678",
        base_url="https://api.deepseek.com",
        **kwargs,
    )


class TransportOptionsTests(unittest.IsolatedAsyncioTestCase):
    """验证建客户端时传给 httpx 的参数。"""

    def setUp(self):
        FakeAsyncClient.instances.clear()
        tls.reset_ssl_context_cache()
        self.patch = mock.patch.object(httpx, "AsyncClient", FakeAsyncClient)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    async def open_client(self, **kwargs):
        """驱动一次请求并返回捕获到的构造参数。"""

        client = make_client(**kwargs)
        await client._httpx_post("https://api.deepseek.com/v1/tools/search", {}, {}, 30.0)
        return FakeAsyncClient.instances[-1].kwargs

    async def test_reuses_shared_ssl_context(self):
        kwargs = await self.open_client()
        self.assertIsInstance(kwargs["verify"], ssl.SSLContext)
        self.assertIs(kwargs["verify"], tls.get_shared_ssl_context())

    async def test_plain_verify_when_reuse_disabled(self):
        kwargs = await self.open_client(reuse_ssl_context=False)
        self.assertIs(kwargs["verify"], True)

    async def test_default_keepalive(self):
        kwargs = await self.open_client()
        limits = kwargs["limits"]
        self.assertEqual(limits.keepalive_expiry, 120.0)
        self.assertEqual(limits.max_connections, 8)
        self.assertEqual(limits.max_keepalive_connections, 4)

    async def test_custom_keepalive(self):
        kwargs = await self.open_client(keepalive_seconds=60)
        self.assertEqual(kwargs["limits"].keepalive_expiry, 60.0)

    async def test_zero_keepalive_falls_back(self):
        kwargs = await self.open_client(keepalive_seconds=0)
        self.assertEqual(kwargs["limits"].keepalive_expiry, 5.0)
