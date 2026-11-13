"""共享 TLS 上下文测试（完全不联网）。"""

from __future__ import annotations

import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import certifi

from tests import framework_stub

tls = framework_stub.import_component("core.tls")


class SharedContextTests(unittest.TestCase):
    """单例复用、失败记忆与重置。"""

    def setUp(self):
        tls.reset_ssl_context_cache()

    def tearDown(self):
        tls.reset_ssl_context_cache()

    def test_returns_same_context(self):
        first = tls.get_shared_ssl_context()
        self.assertIsNotNone(first)
        self.assertIs(tls.get_shared_ssl_context(), first)

    def test_enforces_certificate_verification(self):
        context = tls.get_shared_ssl_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(context.check_hostname, True)

    def test_missing_cacert_returns_none(self):
        missing = str(Path(tempfile.gettempdir()) / "no-such-certifi-cacert.pem")
        with mock.patch.object(certifi, "where", return_value=missing):
            self.assertIsNone(tls.get_shared_ssl_context())

    def test_failure_is_remembered(self):
        missing = str(Path(tempfile.gettempdir()) / "no-such-certifi-cacert.pem")
        with mock.patch.object(certifi, "where", return_value=missing) as where:
            tls.get_shared_ssl_context()
            tls.get_shared_ssl_context()
        self.assertEqual(where.call_count, 1)

    def test_reset_rebuilds_context(self):
        first = tls.get_shared_ssl_context()
        tls.reset_ssl_context_cache()
        second = tls.get_shared_ssl_context()
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
