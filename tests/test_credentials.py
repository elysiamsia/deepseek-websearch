"""API Key 解析测试（全部在临时目录里跑，不读真实文件）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import framework_stub

cred = framework_stub.import_component("core.credentials")


def write_providers(directory: Path, providers: list[dict[str, str]], *, table_form: bool = False) -> Path:
    """把 providers 写成 model.toml。"""

    lines: list[str] = []
    for provider in providers:
        if table_form:
            lines.append(f"[api_providers.{provider['name']}]")
            for key, value in provider.items():
                if key == "name":
                    continue
                lines.append(f'{key} = "{value}"')
        else:
            lines.append("[[api_providers]]")
            for key, value in provider.items():
                lines.append(f'{key} = "{value}"')
        lines.append("")
    path = directory / "model.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


OFFICIAL = {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-official"}
RELAY = {"name": "opencode", "base_url": "https://opencode.ai/zen/go/v1", "api_key": "sk-relay-0001"}


class ResolveApiKeyTests(unittest.TestCase):
    """三级回退链。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env_patch = mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def resolve(self, **kwargs):
        """以临时目录为基目录调用解析。"""

        kwargs.setdefault("model_config_path", "model.toml")
        return cred.resolve_api_key(base_dir=self.dir, **kwargs)

    def test_config_wins(self) -> None:
        write_providers(self.dir, [OFFICIAL])
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env-0001"}):
            key, source = self.resolve(configured="sk-config")
        self.assertEqual((key, source), ("sk-config", "config"))

    def test_env_fallback(self) -> None:
        write_providers(self.dir, [OFFICIAL])
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env-0001"}):
            key, source = self.resolve()
        self.assertEqual((key, source), ("sk-env-0001", "env"))

    def test_model_toml_official(self) -> None:
        write_providers(self.dir, [RELAY, OFFICIAL])
        key, source = self.resolve()
        self.assertEqual(key, "sk-official")
        self.assertEqual(source, "model.toml:DeepSeek")

    def test_ignores_non_official(self) -> None:
        write_providers(self.dir, [RELAY])
        self.assertEqual(self.resolve(), ("", ""))

    def test_skips_empty_key(self) -> None:
        write_providers(
            self.dir,
            [
                {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": ""},
                RELAY,
            ],
        )
        self.assertEqual(self.resolve(), ("", ""))

    def test_provider_override(self) -> None:
        write_providers(self.dir, [RELAY, OFFICIAL])
        key, source = self.resolve(provider_name="opencode")
        self.assertEqual((key, source), ("sk-relay-0001", "model.toml:opencode"))

    def test_provider_override_missing(self) -> None:
        write_providers(self.dir, [OFFICIAL])
        self.assertEqual(self.resolve(provider_name="没有这个"), ("", ""))

    def test_table_form(self) -> None:
        write_providers(self.dir, [OFFICIAL], table_form=True)
        self.assertEqual(self.resolve()[0], "sk-official")

    def test_list_typed_api_key(self) -> None:
        """model.toml 里 api_key 可能是数组（多 Key 轮换），取第一个。"""

        (self.dir / "model.toml").write_text(
            "[[api_providers]]\n"
            'name = "DeepSeek"\n'
            'base_url = "https://api.deepseek.com/v1"\n'
            "api_key = [\"sk-first-0001\", \"sk-second-0001\"]\n",
            encoding="utf-8",
        )
        key, source = self.resolve()
        self.assertEqual(key, "sk-first-0001")
        self.assertEqual(source, "model.toml:DeepSeek")

    def test_list_typed_key_skips_empty_entries(self) -> None:
        (self.dir / "model.toml").write_text(
            "[[api_providers]]\n"
            'name = "DeepSeek"\n'
            'base_url = "https://api.deepseek.com/v1"\n'
            "api_key = [\"  \", \"sk-real\"]\n",
            encoding="utf-8",
        )
        self.assertEqual(self.resolve()[0], "sk-real")

    def test_missing_file(self) -> None:
        self.assertEqual(self.resolve(), ("", ""))

    def test_broken_toml(self) -> None:
        (self.dir / "model.toml").write_text("this is not toml [[[", encoding="utf-8")
        self.assertEqual(self.resolve(), ("", ""))

    def test_absolute_path(self) -> None:
        path = write_providers(self.dir, [OFFICIAL])
        key, _ = cred.resolve_api_key(model_config_path=str(path))
        self.assertEqual(key, "sk-official")

    def test_never_touches_home(self) -> None:
        """回归守卫：解析过程绝不能读用户主目录。"""

        write_providers(self.dir, [OFFICIAL])
        with mock.patch.object(
            Path, "home", side_effect=AssertionError("不允许访问用户主目录")
        ):
            key, source = self.resolve()
        self.assertEqual(key, "sk-official")


class ProviderSelectionTests(unittest.TestCase):
    """provider 挑选细节。"""

    def test_official_endpoint_detection(self) -> None:
        self.assertTrue(cred.is_official_endpoint("https://api.deepseek.com/v1"))
        self.assertTrue(cred.is_official_endpoint("https://api.deepseek.com"))
        self.assertFalse(cred.is_official_endpoint("https://api.deepseek.com.evil.com/v1"))
        self.assertFalse(cred.is_official_endpoint("https://opencode.ai/zen/v1"))
        self.assertFalse(cred.is_official_endpoint(""))

    def test_prefers_deepseek_named_provider(self) -> None:
        providers = [
            {"name": "relay-with-official-domain", "base_url": "https://api.deepseek.com", "api_key": "sk-a"},
            {"name": "MyDeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-b"},
        ]
        key, source = cred.pick_provider_key(providers)
        self.assertEqual(key, "sk-b")
        self.assertEqual(source, "model.toml:MyDeepSeek")

    def test_candidate_paths_respect_base_dir(self) -> None:
        paths = cred._candidate_paths("model.toml", Path("D:/bot"))
        self.assertEqual([str(item) for item in paths], [str(Path("D:/bot") / "model.toml")])


class OfficialFormatTests(unittest.TestCase):
    """官方端点的 Key 格式校验：明显不是 sk- 的 Key 直接跳过。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env_patch = mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def resolve(self, **kwargs):
        kwargs.setdefault("model_config_path", "model.toml")
        return cred.resolve_api_key(base_dir=self.dir, **kwargs)

    def test_bad_config_key_falls_back_to_model_toml(self) -> None:
        """配置里填了非 sk- 的 Key → 跳过并回退 model.toml（本次 401 的根因）。"""

        write_providers(self.dir, [OFFICIAL])
        notes: list[str] = []
        key, source = self.resolve(
            configured="fsf3" + "x" * 24 + "DmqD",
            notes=notes,
        )
        self.assertEqual((key, source), ("sk-official", "model.toml:DeepSeek"))
        self.assertTrue(any("config" in item for item in notes))
        self.assertTrue(any("不是 sk-" in item for item in notes))

    def test_bad_env_key_falls_back_to_model_toml(self) -> None:
        write_providers(self.dir, [OFFICIAL])
        notes: list[str] = []
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "not-a-key"}):
            key, source = self.resolve(notes=notes)
        self.assertEqual((key, source), ("sk-official", "model.toml:DeepSeek"))
        self.assertTrue(any("env" in item for item in notes))

    def test_all_candidates_invalid(self) -> None:
        write_providers(self.dir, [])
        notes: list[str] = []
        key, source = self.resolve(configured="fsf3badkey", notes=notes)
        self.assertEqual((key, source), ("", ""))
        self.assertTrue(notes)

    def test_skips_invalid_official_provider_then_uses_valid_one(self) -> None:
        bad = {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "fsf3badkey"}
        good = {"name": "DeepSeek2", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-goodkey123"}
        write_providers(self.dir, [bad, good])
        notes: list[str] = []
        key, source = self.resolve(notes=notes)
        self.assertEqual((key, source), ("sk-goodkey123", "model.toml:DeepSeek2"))
        self.assertTrue(any("model.toml:DeepSeek " in item for item in notes))

    def test_relay_endpoint_accepts_any_key(self) -> None:
        """非官方端点不做格式校验：中转站 Key 可能是任意格式。"""

        write_providers(self.dir, [OFFICIAL])
        key, source = self.resolve(
            configured="relay-anything-123",
            base_url="https://relay.example.com/v1",
        )
        self.assertEqual((key, source), ("relay-anything-123", "config"))

    def test_official_endpoint_rejects_bad_config_key_when_base_url_given(self) -> None:
        write_providers(self.dir, [OFFICIAL])
        key, source = self.resolve(
            configured="relay-anything-123",
            base_url="https://api.deepseek.com",
        )
        self.assertEqual((key, source), ("sk-official", "model.toml:DeepSeek"))

    def test_mask_key(self) -> None:
        self.assertEqual(cred.mask_key("sk-1234567890abcd"), "sk-1****abcd")
        self.assertEqual(cred.mask_key("short"), "****")

    def test_official_format_predicate(self) -> None:
        self.assertTrue(cred.looks_like_official_key("sk-abcdef123456"))
        self.assertFalse(cred.looks_like_official_key("fsf3abcdef123456"))
        self.assertFalse(cred.looks_like_official_key(""))


if __name__ == "__main__":
    unittest.main()