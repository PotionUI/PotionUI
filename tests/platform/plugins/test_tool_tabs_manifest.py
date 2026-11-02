import unittest
from pathlib import Path

from pydantic import ValidationError

from src.platform.plugins.loader import PluginLoader
from src.platform.plugins.manifest import PluginManifestSchema

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "plugins"


def _manifest(**overrides):
    data = {
        "id": "tool-plugin",
        "name": "Tool Plugin",
        "version": "1.0.0",
        "description": "Has a tool tab",
        "author": "Author",
        "type": "frontend-only",
    }
    data.update(overrides)
    return data


def _tool_tab(**overrides):
    entry = {"id": "stamp", "label": "Stamp tool", "component": "StampToolTab.js"}
    entry.update(overrides)
    return entry


class TestToolTabsSchema(unittest.TestCase):
    def test_tool_tabs_default_to_empty(self):
        self.assertEqual(PluginManifestSchema.model_validate(_manifest()).tool_tabs, [])

    def test_tool_tab_accepts_media_kinds_and_handles(self):
        schema = PluginManifestSchema.model_validate(
            _manifest(tool_tabs=[_tool_tab(icon="brush", accepts=["image", "video"], handles=["image.crop"])])
        )
        tab = schema.tool_tabs[0]
        self.assertEqual((tab.id, tab.label, tab.icon), ("stamp", "Stamp tool", "brush"))
        self.assertEqual(tab.accepts, ["image", "video"])
        self.assertEqual(tab.handles, ["image.crop"])

    def test_unknown_media_kind_is_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(_manifest(tool_tabs=[_tool_tab(accepts=["mesh"])]))

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(_manifest(tool_tabs=[_tool_tab(route="/x")]))

    def test_component_is_required(self):
        entry = _tool_tab()
        del entry["component"]
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(_manifest(tool_tabs=[entry]))

    def test_id_must_be_a_slug(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(_manifest(tool_tabs=[_tool_tab(id="Stamp.Tool")]))

    def test_component_outside_dist_is_rejected(self):
        for component in ["../other/Secret.js", "frontend/../../Secret.js", "/abs/Tool.js", "dist\\Tool.js", "C:/Tool.js", ""]:
            with self.subTest(component=component), self.assertRaises(ValidationError):
                PluginManifestSchema.model_validate(_manifest(tool_tabs=[_tool_tab(component=component)]))

    def test_component_under_dist_is_accepted(self):
        schema = PluginManifestSchema.model_validate(
            _manifest(tool_tabs=[_tool_tab(component="frontend/dist/tools/Stamp.js")])
        )
        self.assertEqual(schema.tool_tabs[0].component, "frontend/dist/tools/Stamp.js")

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(_manifest(tool_tabs=[_tool_tab(), _tool_tab(label="Again")]))


class TestToolTabsFixturePlugin(unittest.TestCase):
    def test_loader_reads_the_fixture_tool_tab(self):
        loader = PluginLoader(str(FIXTURE_ROOT), str(FIXTURE_ROOT / "missing-local"))
        plugin_dir = FIXTURE_ROOT / "tool-tab-fixture"
        manifest = loader._load_manifest(plugin_dir / "manifest.yml", plugin_dir, "marketplace")

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(
            manifest.tool_tabs,
            [
                {
                    "id": "stamp",
                    "label": "Stamp tool",
                    "component": "frontend/dist/StampToolTab.js",
                    "icon": "brush",
                    "accepts": ["image"],
                    "handles": ["image.crop"],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
