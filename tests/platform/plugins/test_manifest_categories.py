import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.platform.plugins.manifest import (
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

    def test_retired_and_unknown_values_are_rejected(self):
        for value in ["generation", "models", "media", "workflow", "system", "not-a-real-category", "Tools", ""]:
            with self.assertRaises(ValidationError, msg=value):
                PluginManifestSchema.model_validate(_manifest(category=value))

    def test_omitted_value_defaults_to_other(self):
        self.assertEqual(PluginManifestSchema.model_validate(_manifest()).category, PluginCategory.OTHER)

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
