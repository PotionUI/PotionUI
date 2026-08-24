"""Tests for plugin-provided prompt importer registration."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.prompt_importers import PromptImporterRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState


class TestPluginPromptImporterRegistration(unittest.TestCase):
    """Test that a plugin's `prompt_importers:` manifest entries are wired
    into a `PromptImporterRegistry` on enable, and removed on disable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.importer_registry = PromptImporterRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            prompt_importer_registry=self.importer_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, prompt_importers: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            'prompt_importers': prompt_importers,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "importers.py").write_text(
            "from src.plugin_api.prompts import PromptImporter, PromptImportOutcome\n\n"
            "class FixtureImporter(PromptImporter):\n"
            "    async def run(self, payload, user_id):\n"
            "        return PromptImportOutcome(imported=1, skipped=0, total=1)\n"
        )

        return plugin_dir

    def test_enable_registers_prompt_importer(self):
        self._create_plugin('fixture-importer-plugin', [
            {
                'id': 'fixture',
                'label': 'Fixture Import',
                'component': 'ImportModal.svelte',
                'backend': 'importers:FixtureImporter',
            }
        ])

        success = self.registry.enable_plugin('fixture-importer-plugin')
        self.assertTrue(success)

        definition = self.importer_registry.get('fixture')
        self.assertIsNotNone(definition)
        self.assertEqual(definition.label, 'Fixture Import')
        self.assertEqual(definition.frontend_component, 'plugin:fixture-importer-plugin:ImportModal.svelte')
        self.assertEqual(definition.source, 'fixture-importer-plugin')
        self.assertEqual(definition.backend.__class__.__name__, 'FixtureImporter')

    def test_disable_unregisters_prompt_importer(self):
        self._create_plugin('fixture-importer-plugin-2', [
            {'id': 'fixture-2', 'label': 'Fixture Import', 'component': 'Modal.svelte', 'backend': 'importers:FixtureImporter'}
        ])

        self.assertTrue(self.registry.enable_plugin('fixture-importer-plugin-2'))
        self.assertIsNotNone(self.importer_registry.get('fixture-2'))

        self.assertTrue(self.registry.disable_plugin('fixture-importer-plugin-2'))
        self.assertIsNone(self.importer_registry.get('fixture-2'))

    def test_enable_fails_on_id_collision(self):
        self._create_plugin('first-plugin', [
            {'id': 'shared-id', 'label': 'First', 'component': 'Modal.svelte', 'backend': 'importers:FixtureImporter'}
        ])
        self._create_plugin('second-plugin', [
            {'id': 'shared-id', 'label': 'Second', 'component': 'Modal.svelte', 'backend': 'importers:FixtureImporter'}
        ])

        self.assertTrue(self.registry.enable_plugin('first-plugin'))
        success = self.registry.enable_plugin('second-plugin')

        self.assertFalse(success)
        self.assertEqual(self.registry.get_plugin_state('second-plugin'), PluginState.ERROR)
        error = self.registry.get_plugin_error('second-plugin')
        self.assertIn('shared-id', error)
        # Rollback leaves the first plugin's registration intact.
        self.assertEqual(self.importer_registry.get('shared-id').source, 'first-plugin')

    def test_enable_fails_when_backend_class_is_missing(self):
        self._create_plugin('broken-plugin', [
            {'id': 'broken', 'label': 'Broken', 'component': 'Modal.svelte', 'backend': 'importers:DoesNotExist'}
        ])

        success = self.registry.enable_plugin('broken-plugin')

        self.assertFalse(success)
        self.assertIsNone(self.importer_registry.get('broken'))


if __name__ == '__main__':
    unittest.main()
