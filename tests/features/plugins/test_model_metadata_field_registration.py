"""Tests for plugin-provided model attribute definitions (mirrors
test_field_type_registration.py for `model_metadata_fields:`, but against the
DB-backed `ModelAttributeDefinitionsEditor` - Attributes v2 supersedes the
migration-133 in-memory registry)."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.platform.plugins.registry import PluginRegistry, PluginState

from tests.fixtures.persistence_base import PersistenceTestBase


class TestPluginModelAttributeDefinitionRegistration(PersistenceTestBase):
    """A plugin's `model_metadata_fields:` manifest entries are upserted into
    `model_attribute_definitions` on enable, and removed on disable."""

    def setUp(self):
        super().setUp()
        import src.features.models.attributes.repository as def_repo_module
        def_repo_module.db = self.db
        import src.features.models.attributes.user_repository as user_repo_module
        user_repo_module.db = self.db

        self.definitions = AttributeDefinitionRepository()
        self.model_attributes_manager = ModelAttributeDefinitionsEditor(
            self.definitions, UserModelAttributeRepository()
        )

        self.plugin_temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.plugin_temp_dir / "marketplace"
        self.local_dir = self.plugin_temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            model_attributes_manager=self.model_attributes_manager,
        )

    def tearDown(self):
        shutil.rmtree(self.plugin_temp_dir)
        super().tearDown()

    def _create_plugin(self, plugin_id: str, model_metadata_fields: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'backend-only',
            'model_metadata_fields': model_metadata_fields,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        return plugin_dir

    def test_enable_registers_model_attribute_definition(self):
        self._create_plugin('metadata-field-plugin', [
            {
                'key': 'clip_skip', 'label': 'CLIP Skip', 'field_type': 'number',
                'model_types': ['checkpoint'], 'config': {'min': 1, 'max': 4}, 'default_value': 1,
            }
        ])

        success = self.registry.enable_plugin('metadata-field-plugin')
        self.assertTrue(success)

        definition = self.definitions.get_by_key('clip_skip')
        self.assertIsNotNone(definition)
        self.assertEqual(definition.field_type, 'number')
        self.assertEqual(definition.model_types, ['checkpoint'])
        self.assertEqual(definition.source, 'metadata-field-plugin')
        self.assertFalse(definition.system)

    def test_disable_unregisters_model_attribute_definition(self):
        self._create_plugin('metadata-field-plugin-2', [
            {'key': 'clip_skip', 'label': 'CLIP Skip', 'field_type': 'number'}
        ])

        self.assertTrue(self.registry.enable_plugin('metadata-field-plugin-2'))
        self.assertIsNotNone(self.definitions.get_by_key('clip_skip'))

        self.assertTrue(self.registry.disable_plugin('metadata-field-plugin-2'))
        self.assertIsNone(self.definitions.get_by_key('clip_skip'))

    def test_enable_fails_on_core_owned_collision(self):
        """A plugin may not silently override a core-owned key."""
        self.definitions.create(ModelAttributeDefinition(
            key='strength', label='Strength', field_type='slider', system=True, source='core',
        ))

        self._create_plugin('colliding-plugin', [
            {'key': 'strength', 'label': 'Strength', 'field_type': 'slider'}
        ])

        success = self.registry.enable_plugin('colliding-plugin')
        self.assertFalse(success)
        self.assertEqual(self.registry.get_plugin_state('colliding-plugin'), PluginState.ERROR)
        error = self.registry.get_plugin_error('colliding-plugin')
        self.assertIn('strength', error)
        # Rejected registration must not leave the core definition overwritten.
        self.assertEqual(self.definitions.get_by_key('strength').source, 'core')

    def test_reenabling_the_same_plugin_is_not_a_collision_with_itself(self):
        self._create_plugin('metadata-field-plugin-3', [
            {'key': 'clip_skip', 'label': 'CLIP Skip', 'field_type': 'number'}
        ])

        self.assertTrue(self.registry.enable_plugin('metadata-field-plugin-3'))
        self.assertTrue(self.registry.disable_plugin('metadata-field-plugin-3'))
        self.assertTrue(self.registry.enable_plugin('metadata-field-plugin-3'))

        self.assertIsNotNone(self.definitions.get_by_key('clip_skip'))


if __name__ == '__main__':
    unittest.main()
