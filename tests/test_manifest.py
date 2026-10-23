"""manifest.json 与代码一致性测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests import framework_stub

ROOT = Path(__file__).resolve().parents[1]

#: 官方文档列出的 20 个插件 API 模块。
KNOWN_API_MODULES = {
    "action_api", "adapter_api", "agent_api", "chat_api", "command_api",
    "config_api", "database_api", "event_api", "llm_api", "log_api",
    "media_api", "message_api", "permission_api", "plugin_api", "prompt_api",
    "router_api", "send_api", "service_api", "storage_api", "stream_api",
}
ALLOWED_CATEGORIES = {"tool", "chat", "fun", "information", "moderation"}
ALLOWED_COMPONENT_TYPES = {
    "action", "tool", "adapter", "chatter", "command",
    "event_handler", "service", "router", "config", "agent",
}


class ManifestTests(unittest.TestCase):
    """校验插件清单。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        cls.plugin_module = framework_stub.import_component("plugin")
        cls.plugin_cls = cls.plugin_module.DeepSeekWebSearchPlugin

    def test_required_fields(self) -> None:
        for field in ("name", "version", "description", "author", "dependencies", "entry_point"):
            with self.subTest(field=field):
                self.assertIn(field, self.manifest)
                self.assertTrue(self.manifest[field] != "" or field == "dependencies")

    def test_name_matches_plugin_class(self) -> None:
        self.assertEqual(self.manifest["name"], self.plugin_cls.plugin_name)

    def test_version_is_semver(self) -> None:
        self.assertRegex(self.manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_package_version_matches_manifest(self) -> None:
        package = framework_stub.load_package()
        self.assertEqual(package.__version__, self.manifest["version"])

    def test_entry_point_exists(self) -> None:
        self.assertTrue((ROOT / self.manifest["entry_point"]).is_file())

    def test_dependencies_shape(self) -> None:
        deps = self.manifest["dependencies"]
        self.assertEqual(set(deps), {"plugins", "components"})
        self.assertEqual(deps["plugins"], [])

    def test_include_matches_components(self) -> None:
        components = {cls.name: cls for cls in self.plugin_cls([]).get_components()}
        include = self.manifest["include"]
        self.assertTrue(include)
        declared = set()
        for item in include:
            with self.subTest(component=item["component_name"]):
                self.assertIn(item["component_type"], ALLOWED_COMPONENT_TYPES)
                self.assertIn(item["component_name"], components)
                self.assertTrue(item["enabled"])
                declared.add(item["component_name"])
        self.assertEqual(declared, set(components))

    def test_api_version_declares_known_modules(self) -> None:
        api_version = self.manifest["api_version"]
        self.assertIsInstance(api_version, dict)
        self.assertTrue(api_version)
        for module, version in api_version.items():
            with self.subTest(module=module):
                self.assertIn(module, KNOWN_API_MODULES)
                self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_categories_and_tags(self) -> None:
        categories = self.manifest.get("categories")
        self.assertIsInstance(categories, list)
        self.assertEqual(len(categories), 1)
        self.assertIn(categories[0], ALLOWED_CATEGORIES)
        self.assertIsInstance(self.manifest.get("tags"), list)
        self.assertTrue(self.manifest["tags"])

    def test_no_deprecated_class_metadata(self) -> None:
        for attr in ("plugin_version", "plugin_description", "plugin_author"):
            with self.subTest(attr=attr):
                self.assertNotIn(attr, vars(self.plugin_cls))

    def test_python_dependencies_is_empty(self) -> None:
        self.assertEqual(self.manifest.get("python_dependencies", []), [])

    def test_min_core_version_present(self) -> None:
        self.assertRegex(self.manifest["min_core_version"], r"^\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
