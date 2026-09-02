"""Tests for plugin-provided phrasebook batch operation registration."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.phrasebook_ops import PhrasebookOperationRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState


class TestPluginPhrasebookOpRegistration(unittest.TestCase):
    """A plugin's `phrasebook_ops:` manifest entries are wired into a
    `PhrasebookOperationRegistry` on enable, and removed on disable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.op_registry = PhrasebookOperationRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            phrasebook_operation_registry=self.op_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, phrasebook_ops: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            'phrasebook_ops': phrasebook_ops,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "ops.py").write_text(
            "from src.plugin_api.phrasebook import PhrasebookBatchOperation, BatchOutcome, BatchPreview\n\n"
            "class ShoutOperation(PhrasebookBatchOperation):\n"
            "    supports_preview = True\n"
            "    async def preview(self, ctx, value_ids, params):\n"
            "        return BatchPreview()\n"
            "    async def run(self, ctx, value_ids, params):\n"
            "        return BatchOutcome(message='shouted')\n"
        )

        return plugin_dir

    def test_enable_registers_operation_with_component(self):
        self._create_plugin('shout-plugin', [
            {'id': 'shout', 'label': 'Shout', 'component': 'ShoutModal.svelte', 'backend': 'ops:ShoutOperation'}
        ])

        self.assertTrue(self.registry.enable_plugin('shout-plugin'))

        definition = self.op_registry.get('shout')
        self.assertIsNotNone(definition)
        self.assertEqual(definition.label, 'Shout')
        self.assertEqual(definition.frontend_component, 'plugin:shout-plugin:ShoutModal.svelte')
        self.assertEqual(definition.source, 'shout-plugin')
        self.assertEqual(definition.backend.__class__.__name__, 'ShoutOperation')
        self.assertEqual(self.op_registry.frontend_manifest()[0]["has_preview"], True)

    def test_component_is_optional(self):
        self._create_plugin('quiet-plugin', [{'id': 'quiet', 'label': 'Quiet', 'backend': 'ops:ShoutOperation'}])

        self.assertTrue(self.registry.enable_plugin('quiet-plugin'))

        self.assertIsNone(self.op_registry.get('quiet').frontend_component)

    def test_disable_unregisters_operation(self):
        self._create_plugin('shout-plugin-2', [
            {'id': 'shout-2', 'label': 'Shout', 'component': 'Modal.svelte', 'backend': 'ops:ShoutOperation'}
        ])

        self.assertTrue(self.registry.enable_plugin('shout-plugin-2'))
        self.assertIsNotNone(self.op_registry.get('shout-2'))

        self.assertTrue(self.registry.disable_plugin('shout-plugin-2'))
        self.assertIsNone(self.op_registry.get('shout-2'))

    def test_enable_fails_on_id_collision_and_rolls_back(self):
        self._create_plugin('first-plugin', [
            {'id': 'shared-id', 'label': 'First', 'backend': 'ops:ShoutOperation'}
        ])
        self._create_plugin('second-plugin', [
            {'id': 'other', 'label': 'Other', 'backend': 'ops:ShoutOperation'},
            {'id': 'shared-id', 'label': 'Second', 'backend': 'ops:ShoutOperation'},
        ])

        self.assertTrue(self.registry.enable_plugin('first-plugin'))
        self.assertFalse(self.registry.enable_plugin('second-plugin'))

        self.assertEqual(self.registry.get_plugin_state('second-plugin'), PluginState.ERROR)
        self.assertIn('shared-id', self.registry.get_plugin_error('second-plugin'))
        self.assertEqual(self.op_registry.get('shared-id').source, 'first-plugin')
        self.assertIsNone(self.op_registry.get('other'))

    def test_enable_fails_when_backend_class_is_missing(self):
        self._create_plugin('broken-plugin', [
            {'id': 'broken', 'label': 'Broken', 'backend': 'ops:DoesNotExist'}
        ])

        self.assertFalse(self.registry.enable_plugin('broken-plugin'))
        self.assertIsNone(self.op_registry.get('broken'))


if __name__ == '__main__':
    unittest.main()
