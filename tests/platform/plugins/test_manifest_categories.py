import logging
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.manifest import (
    LEGACY_PLUGIN_CATEGORIES,
    PluginCategory,
    PluginManifestSchema,
)

MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace"


def _manifest(**overrides):
    data = {
        "id": "cat-plugin",
        "name": "Cat Plugin",
        "version": "1.0.0",
        "description": "d",
        "author": "a",
        "type": "backend-only",
    }
    data.update(overrides)
    return data


class TestManifestCategories(unittest.TestCase):
    def test_every_new_value_validates(self):
        for category in PluginCategory:
            schema = PluginManifestSchema.model_validate(_manifest(category=category.value))
            self.assertEqual(schema.category, category)

    def test_new_values_are_the_agreed_set(self):
        self.assertEqual(
            [c.value for c in PluginCategory],
            ["backends", "sources", "steps", "tools", "security", "monitoring", "developer", "other"],
        )

    def test_legacy_values_map_with_a_warning_naming_the_plugin(self):
        for legacy, expected in LEGACY_PLUGIN_CATEGORIES.items():
            with self.assertLogs("src.platform.plugins.manifest", level=logging.WARNING) as logs:
                schema = PluginManifestSchema.model_validate(_manifest(category=legacy))
            self.assertEqual(schema.category, expected)
            self.assertEqual(len(logs.records), 1)
            self.assertIn("cat-plugin", logs.output[0])
            self.assertIn(legacy, logs.output[0])

    def test_legacy_mapping_choices(self):
        self.assertEqual(LEGACY_PLUGIN_CATEGORIES["generation"], PluginCategory.BACKENDS)
        self.assertEqual(LEGACY_PLUGIN_CATEGORIES["models"], PluginCategory.SOURCES)
        self.assertEqual(LEGACY_PLUGIN_CATEGORIES["media"], PluginCategory.TOOLS)
        self.assertEqual(LEGACY_PLUGIN_CATEGORIES["workflow"], PluginCategory.TOOLS)
        self.assertEqual(LEGACY_PLUGIN_CATEGORIES["system"], PluginCategory.OTHER)

    def test_unknown_value_loads_as_other_with_a_warning(self):
        with self.assertLogs("src.platform.plugins.manifest", level=logging.WARNING) as logs:
            schema = PluginManifestSchema.model_validate(_manifest(category="not-a-real-category"))
        self.assertEqual(schema.category, PluginCategory.OTHER)
        self.assertIn("cat-plugin", logs.output[0])

    def test_valid_and_omitted_values_do_not_warn(self):
        with self.assertNoLogs("src.platform.plugins.manifest", level=logging.WARNING):
            PluginManifestSchema.model_validate(_manifest(category="tools"))
            PluginManifestSchema.model_validate(_manifest())

    def test_value_is_case_insensitive(self):
        schema = PluginManifestSchema.model_validate(_manifest(category="Tools"))
        self.assertEqual(schema.category, PluginCategory.TOOLS)

    def test_every_marketplace_manifest_uses_a_current_value(self):
        manifests = sorted(MARKETPLACE.glob("*/manifest.yml"))
        self.assertTrue(manifests)
        current = {c.value for c in PluginCategory}
        for path in manifests:
            declared = yaml.safe_load(path.read_text(encoding="utf-8")).get("category")
            self.assertIn(declared, current, path.parent.name)

    def test_marketplace_plugins_land_in_the_agreed_categories(self):
        expected = {
            "comfyui-backend": "backends",
            "openrouter-provider": "backends",
            "runpod-provider": "backends",
            "civitai-provider": "sources",
            "huggingface-provider": "sources",
            "nvidia-rtx-upscale": "steps",
            "a1111-metadata-export": "tools",
            "image-modal": "tools",
            "oidc-auth": "security",
            "system-monitor": "monitoring",
            "ollama": "monitoring",
            "example-extensions": "developer",
            "example-field": "developer",
        }
        for plugin_id, category in expected.items():
            data = yaml.safe_load((MARKETPLACE / plugin_id / "manifest.yml").read_text(encoding="utf-8"))
            self.assertEqual(data["category"], category, plugin_id)


if __name__ == "__main__":
    unittest.main()
