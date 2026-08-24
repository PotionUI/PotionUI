"""Tests for plugin-contributed automation template lifecycle."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.automation_templates import AutomationTemplateRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState


def _document() -> dict:
    return {
        "schema": "potionui.automation",
        "schema_version": 1,
        "kind": "automation",
        "automation": {
            "name": "Plugin starter",
            "graph": {
                "nodes": [{"id": "start", "type": "trigger.manual", "config": {}}],
                "edges": [],
            },
        },
        "node_types": ["trigger.manual"],
    }


class TestPluginAutomationTemplateRegistration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.template_registry = AutomationTemplateRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            automation_template_registry=self.template_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, templates: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        manifest = {
            "id": plugin_id,
            "name": "Template Plugin",
            "version": "1.0.0",
            "description": "Contributes templates",
            "author": "Test Author",
            "type": "full-stack",
            "automation_templates": templates,
        }
        (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest), encoding="utf-8")
        return plugin_dir

    @staticmethod
    def _spec(
        template_id: str = "starter", path: str = "automations/starter.json"
    ) -> dict:
        return {
            "id": template_id,
            "title": "Plugin starter",
            "description": "A plugin workflow",
            "category": "examples",
            "icon": "plug",
            "tags": ["plugin", "starter"],
            "path": path,
        }

    def test_enable_registers_namespaced_template_metadata(self):
        plugin_dir = self._create_plugin("template-plugin", [self._spec()])
        template_dir = plugin_dir / "automations"
        template_dir.mkdir()
        (template_dir / "starter.json").write_text(
            json.dumps(_document()), encoding="utf-8"
        )

        self.assertTrue(self.registry.enable_plugin("template-plugin"))

        template = self.template_registry.get("plugin:template-plugin:starter")
        self.assertIsNotNone(template)
        self.assertEqual(template.source, "plugin:template-plugin")
        self.assertEqual(template.source_name, "Template Plugin")
        self.assertEqual(template.tags, ("plugin", "starter"))

    def test_disable_unregisters_plugin_templates(self):
        plugin_dir = self._create_plugin("template-plugin-disable", [self._spec()])
        template_dir = plugin_dir / "automations"
        template_dir.mkdir()
        (template_dir / "starter.json").write_text(
            json.dumps(_document()), encoding="utf-8"
        )

        self.assertTrue(self.registry.enable_plugin("template-plugin-disable"))
        self.assertTrue(self.registry.disable_plugin("template-plugin-disable"))

        self.assertIsNone(
            self.template_registry.get("plugin:template-plugin-disable:starter")
        )

    def test_enable_fails_without_template_registry(self):
        registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))
        plugin_dir = self._create_plugin("no-template-registry", [self._spec()])
        template_dir = plugin_dir / "automations"
        template_dir.mkdir()
        (template_dir / "starter.json").write_text(
            json.dumps(_document()), encoding="utf-8"
        )

        self.assertFalse(registry.enable_plugin("no-template-registry"))
        self.assertIn(
            "automation template registry",
            registry.get_plugin_error("no-template-registry"),
        )

    def test_escaping_path_fails_enable(self):
        self._create_plugin("escaping-template", [self._spec(path="../outside.json")])
        (self.marketplace_dir / "outside.json").write_text(
            json.dumps(_document()), encoding="utf-8"
        )

        self.assertFalse(self.registry.enable_plugin("escaping-template"))

        self.assertEqual(
            self.registry.get_plugin_state("escaping-template"),
            PluginState.ERROR,
        )
        self.assertIn(
            "escapes its source directory",
            self.registry.get_plugin_error("escaping-template"),
        )
        self.assertEqual(self.template_registry.all(), [])

    def test_invalid_later_template_rolls_back_earlier_registration(self):
        specs = [
            self._spec(template_id="valid", path="automations/valid.json"),
            self._spec(template_id="missing", path="automations/missing.json"),
        ]
        plugin_dir = self._create_plugin("partial-template-plugin", specs)
        template_dir = plugin_dir / "automations"
        template_dir.mkdir()
        (template_dir / "valid.json").write_text(
            json.dumps(_document()), encoding="utf-8"
        )

        self.assertFalse(self.registry.enable_plugin("partial-template-plugin"))

        self.assertIsNone(
            self.template_registry.get("plugin:partial-template-plugin:valid")
        )
        self.assertEqual(self.template_registry.all(), [])


if __name__ == "__main__":
    unittest.main()
