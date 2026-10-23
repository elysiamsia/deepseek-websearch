"""DeepSeek API 客户端测试（完全不联网）。"""

from __future__ import annotations

import json
import unittest

from tests import framework_stub

client_mod = framework_stub.import_component("core.client")
errors = framework_stub.import_component("core.errors")


def json_response(payload, status=200, headers=None):
    """构造一个上游响应。"""

    return client_mod.HttpResponse(
        status=status,
        headers=headers or {},
        body=json.dumps(payload, ensure_ascii=False),
    )


class Recorder:
    """记录调用并按序返回预设响应的假传输层。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.sleeps = []

    async def post(self, url, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": dict(payload), "timeout": timeout}
        )
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, BaseException):
            raise item
        return item

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


def make_client(recorder, **kwargs):
    """用假传输层构造客户端。"""

    kwargs.setdefault("max_retries", 2)
    return client_mod.DeepSeekClient(
        api_key="sk-test-12345678",
        base_url="https://api.deepseek.com",
        http_post=recorder.post,
        sleep=recorder.sleep,
        **kwargs,
    )


class SearchTests(unittest.IsolatedAsyncioTestCase):
    """search 行为。"""

    async def test_success(self) -> None:
        recorder = Recorder(
            [
                json_response(
                    {
                        "results": [
                            {
                                "title": "标题",
                                "url": "https://example.com/a",
                                "site_name": "示例",
                                "date": "2026-08-27T16:00:00+00:00",
                                "summary": "摘要",
                                "snippet": "短摘要",
                                "content": "",
                                "authority": 3,
                                "rerank_score": 2.13,
                                "site_icon": "https://cdn/x.png",
                            }
                        ]
                    }
                )
            ]
        )
        client = make_client(recorder)
        results = await client.search("测试", count=3, need_content=False)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "标题")
        self.assertEqual(results[0].authority, 3)
        self.assertAlmostEqual(results[0].rerank_score, 2.13)

        call = recorder.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/v1/tools/search")
        self.assertEqual(call["payload"]["query"], "测试")
        self.assertEqual(call["payload"]["count"], 3)
        self.assertFalse(call["payload"]["need_content"])
        self.assertNotIn("ori_question", call["payload"])
        self.assertTrue(call["headers"]["Authorization"].startswith("Bearer sk-test"))

    async def test_ori_question_forwarded(self) -> None:
        recorder = Recorder([json_response({"results": []})])
        client = make_client(recorder)
        await client.search("q", count=1, ori_question="用户原问题")
        self.assertEqual(recorder.calls[0]["payload"]["ori_question"], "用户原问题")

    async def test_empty_and_missing_results(self) -> None:
        for payload in ({"results": []}, {}):
            with self.subTest(payload=payload):
                recorder = Recorder([json_response(payload)])
                client = make_client(recorder, max_retries=0)
                self.assertEqual(await client.search("q", count=1), [])

    async def test_malformed_results_type(self) -> None:
        recorder = Recorder([json_response({"results": "oops"})])
        client = make_client(recorder, max_retries=0)
        with self.assertRaises(errors.UpstreamError):
            await client.search("q", count=1)

    async def test_auth_error_not_retried(self) -> None:
        recorder = Recorder([json_response({"error": {"message": "invalid key"}}, status=401)])
        client = make_client(recorder)
        with self.assertRaises(errors.AuthError):
            await client.search("q", count=1)
        self.assertEqual(len(recorder.calls), 1)

    async def test_insufficient_balance(self) -> None:
        recorder = Recorder([json_response({"error": {"message": "no money"}}, status=402)])
        client = make_client(recorder)
        with self.assertRaises(errors.InsufficientBalanceError) as ctx:
            await client.search("q", count=1)
        self.assertIn("充值", ctx.exception.hint)
        self.assertEqual(len(recorder.calls), 1)

    async def test_rate_limit_retries_with_retry_after(self) -> None:
        recorder = Recorder(
            [
                json_response({"error": {"message": "slow down"}}, status=429, headers={"retry-after": "1"}),
                json_response({"results": []}),
            ]
        )
        client = make_client(recorder)
        self.assertEqual(await client.search("q", count=1), [])
        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(recorder.sleeps, [1.0])

    async def test_server_error_exhausts_retries(self) -> None:
        recorder = Recorder([json_response({"error": {"message": "boom"}}, status=500)])
        client = make_client(recorder, max_retries=1)
        with self.assertRaises(errors.UpstreamError):
            await client.search("q", count=1)
        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(len(recorder.sleeps), 1)

    async def test_transport_error_then_success(self) -> None:
        recorder = Recorder(
            [errors.TransportError("connection reset"), json_response({"results": []})]
        )
        client = make_client(recorder)
        self.assertEqual(await client.search("q", count=1), [])
        self.assertEqual(len(recorder.calls), 2)

    async def test_non_retryable_transport_error(self) -> None:
        recorder = Recorder([errors.TransportError("bad cert", retryable=False)])
        client = make_client(recorder)
        with self.assertRaises(errors.TransportError):
            await client.search("q", count=1)
        self.assertEqual(len(recorder.calls), 1)

    async def test_invalid_json(self) -> None:
        recorder = Recorder([client_mod.HttpResponse(status=200, headers={}, body="<html>oops</html>")])
        client = make_client(recorder, max_retries=0)
        with self.assertRaises(errors.UpstreamError):
            await client.search("q", count=1)

    async def test_request_count_increments(self) -> None:
        recorder = Recorder([json_response({"results": []})])
        client = make_client(recorder, max_retries=0)
        await client.search("q", count=1)
        self.assertEqual(client.request_count, 1)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    """fetch 行为。"""

    async def test_success(self) -> None:
        recorder = Recorder([json_response({"title": "标题", "content": "正文"})])
        client = make_client(recorder)
        title, content = await client.fetch("https://example.com")
        self.assertEqual((title, content), ("标题", "正文"))
        self.assertEqual(
            recorder.calls[0]["url"], "https://api.deepseek.com/v1/tools/fetch"
        )
        self.assertEqual(recorder.calls[0]["payload"], {"url": "https://example.com"})

    async def test_empty_content(self) -> None:
        recorder = Recorder([json_response({"title": "标题", "content": "   "})])
        client = make_client(recorder, max_retries=0)
        with self.assertRaises(errors.NotFoundError):
            await client.fetch("https://example.com")

    async def test_not_found(self) -> None:
        recorder = Recorder(
            [json_response({"error": {"message": "failed to fetch URL"}}, status=404)]
        )
        client = make_client(recorder)
        with self.assertRaises(errors.NotFoundError):
            await client.fetch("https://example.com/gone")


class EndpointGuardTests(unittest.IsolatedAsyncioTestCase):
    """端点白名单是本插件的硬约束。"""

    async def test_allowed_paths(self) -> None:
        self.assertEqual(
            client_mod.ALLOWED_PATHS, frozenset({"/v1/tools/search", "/v1/tools/fetch"})
        )

    async def test_chat_completions_rejected(self) -> None:
        recorder = Recorder([json_response({})])
        client = make_client(recorder)
        with self.assertRaises(errors.EndpointNotAllowedError):
            await client._post("/v1/chat/completions", {"messages": []})
        self.assertEqual(recorder.calls, [])

    async def test_build_url(self) -> None:
        recorder = Recorder([json_response({})])
        client = make_client(recorder)
        self.assertEqual(
            client._build_url("/v1/tools/search"), "https://api.deepseek.com/v1/tools/search"
        )
        with self.assertRaises(errors.EndpointNotAllowedError):
            client._build_url("/v1/embeddings")


class ConstructionTests(unittest.TestCase):
    """构造与配置校验。"""

    def test_missing_key(self) -> None:
        with self.assertRaises(errors.ConfigError):
            client_mod.DeepSeekClient(api_key="  ")

    def test_masked_key(self) -> None:
        client = client_mod.DeepSeekClient(api_key="sk-abcdefghijklmn")
        self.assertEqual(client.masked_key, "sk-a****klmn")
        short = client_mod.DeepSeekClient(api_key="short")
        self.assertEqual(short.masked_key, "****")

    def test_base_url_normalization(self) -> None:
        self.assertEqual(
            client_mod.normalize_base_url("https://api.deepseek.com/v1/"),
            "https://api.deepseek.com",
        )
        self.assertEqual(
            client_mod.normalize_base_url("https://api.deepseek.com/"), "https://api.deepseek.com"
        )
        self.assertEqual(client_mod.normalize_base_url(""), client_mod.DEFAULT_BASE_URL)

    def test_base_url_invalid(self) -> None:
        for bad in ("ftp://x.com", "api.deepseek.com", "https://x.com/?a=1"):
            with self.subTest(bad=bad), self.assertRaises(errors.ConfigError):
                client_mod.normalize_base_url(bad)


class FetchUrlValidationTests(unittest.TestCase):
    """抓取 URL 校验（SSRF 防护）。"""

    def test_accepts_normal(self) -> None:
        url = "https://example.com/a?b=1"
        self.assertEqual(client_mod.validate_fetch_url(url), url)

    def test_rejects_bad_scheme(self) -> None:
        for bad in ("file:///etc/passwd", "ftp://x.com/a", "javascript:alert(1)"):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidArgumentError):
                client_mod.validate_fetch_url(bad)

    def test_rejects_private_hosts(self) -> None:
        for bad in (
            "http://127.0.0.1/admin",
            "http://localhost:8080/",
            "http://192.168.1.1/",
            "http://172.20.0.1/",
            "http://10.0.0.1/",
            "http://router.local/",
        ):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidArgumentError):
                client_mod.validate_fetch_url(bad)

    def test_private_hosts_can_be_allowed(self) -> None:
        url = "http://127.0.0.1:8080/"
        self.assertEqual(
            client_mod.validate_fetch_url(url, block_private_hosts=False), url
        )

    def test_public_172_is_allowed(self) -> None:
        url = "http://172.32.0.1/"
        self.assertEqual(client_mod.validate_fetch_url(url), url)

    def test_rejects_credentials(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            client_mod.validate_fetch_url("https://user:pass@example.com/")

    def test_rejects_whitespace_and_empty(self) -> None:
        for bad in ("", "   ", "https://exa mple.com"):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidArgumentError):
                client_mod.validate_fetch_url(bad)


if __name__ == "__main__":
    unittest.main()
