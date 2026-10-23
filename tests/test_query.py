"""query 构造与校验测试。"""

from __future__ import annotations

import unittest

from tests import framework_stub

query_mod = framework_stub.import_component("core.query")
errors = framework_stub.import_component("core.errors")


class NormalizeQueryTests(unittest.TestCase):
    """normalize_query 行为。"""

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(query_mod.normalize_query("  DeepSeek   V4 \n 发布 "), "DeepSeek V4 发布")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            query_mod.normalize_query("   ")

    def test_too_long_rejected(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError) as ctx:
            query_mod.normalize_query("啊" * 129)
        self.assertIn("128", str(ctx.exception))

    def test_limit_is_inclusive(self) -> None:
        self.assertEqual(len(query_mod.normalize_query("a" * 128)), 128)


class NormalizeSiteTests(unittest.TestCase):
    """站点过滤值规范化。"""

    def test_strips_scheme_and_path(self) -> None:
        self.assertEqual(query_mod.normalize_site("https://github.com/mofox/bot"), "github.com")

    def test_empty_ok(self) -> None:
        self.assertEqual(query_mod.normalize_site(""), "")

    def test_rejects_pure_scheme(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            query_mod.normalize_site("https://")


class NormalizeDateTests(unittest.TestCase):
    """日期过滤值校验。"""

    def test_year_only(self) -> None:
        self.assertEqual(query_mod.normalize_date("2025"), "2025")

    def test_month(self) -> None:
        self.assertEqual(query_mod.normalize_date("2025-6"), "2025-06")

    def test_full_date(self) -> None:
        self.assertEqual(query_mod.normalize_date("2025-06-01"), "2025-06-01")

    def test_invalid(self) -> None:
        for bad in ("2025/06/01", "25-06", "abcd", "2025-13-01", "2025-06-32"):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidArgumentError):
                query_mod.normalize_date(bad, "after")

    def test_empty(self) -> None:
        self.assertEqual(query_mod.normalize_date(""), "")


class NormalizeLangTests(unittest.TestCase):
    """语言过滤值规范化。"""

    def test_lowercase_and_multi(self) -> None:
        self.assertEqual(query_mod.normalize_lang(" ZH , En "), "zh,en")

    def test_invalid(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            query_mod.normalize_lang("中文")


class BuildQueryTests(unittest.TestCase):
    """build_query 组合行为。"""

    def test_plain(self) -> None:
        self.assertEqual(query_mod.build_query("Neo-MoFox 插件"), "Neo-MoFox 插件")

    def test_with_filters(self) -> None:
        built = query_mod.build_query(
            "MoFox 文档",
            site="github.com docs.mofox.chat",
            lang="zh,en",
            after="2025-01",
            before="2026-12-31",
        )
        self.assertIn("site:github.com", built)
        self.assertIn("site:docs.mofox.chat", built)
        self.assertIn("lang:zh,en", built)
        self.assertIn("after:2025-01", built)
        self.assertIn("before:2026-12-31", built)

    def test_does_not_duplicate_manual_site(self) -> None:
        built = query_mod.build_query("插件 site:github.com", site="gitee.com")
        self.assertEqual(built, "插件 site:github.com")

    def test_exclude_site(self) -> None:
        built = query_mod.build_query("教程", exclude_site="pinterest.com")
        self.assertIn("-site:pinterest.com", built)

    def test_overflow_rejected(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            query_mod.build_query("x" * 120, site="github.com", lang="zh", after="2025")

    def test_bad_date_surfaces(self) -> None:
        with self.assertRaises(errors.InvalidArgumentError):
            query_mod.build_query("abc", after="not-a-date")


if __name__ == "__main__":
    unittest.main()
