"""Tests for plugin-provided field type registration (A3)."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.field_types import FieldTypeRegistry, FieldTypeDefinition
from src.platform.plugins.registry import PluginRegistry, PluginState


class TestPluginFieldTypeRegistration(unittest.TestCase):
    """Test that a plugin's `field_types:` manifest entries are wired into a
    `FieldTypeRegistry` on enable, and removed on disable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.field_registry = FieldTypeRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            field_registry=self.field_registry
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, field_types: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            'field_types': field_types,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "fields.py").write_text(
            "from src.features.fields.base_field import BaseField\n\n"
            "class CustomField(BaseField):\n"
            "    def map_field(self, field, preset_id=None):\n"
            "        field_info = self.get_field_info(field)\n"
            "        return self.create_base_schema(field_info)\n\n"
            "def get_custom_options(config):\n"
            "    return [{'label': 'Custom', 'value': 'custom'}]\n"
        )

        return plugin_dir

    def test_enable_registers_field_type(self):
        self._create_plugin('custom-field-plugin', [
            {
                'type': 'custom_widget',
                'schema_class': 'fields:CustomField',
                'options_handler': 'fields.get_custom_options',
                'component': 'CustomWidget.svelte',
            }
        ])

        success = self.registry.enable_plugin('custom-field-plugin')
        self.assertTrue(success)

        definition = self.field_registry.get('custom_widget')
        self.assertEqual(definition.type_name, 'custom_widget')
        self.assertIsNotNone(definition.schema_cls)
        self.assertEqual(definition.schema_cls.__name__, 'CustomField')
        self.assertIsNotNone(definition.options_provider)
        self.assertEqual(definition.options_provider({}), [{'label': 'Custom', 'value': 'custom'}])
        self.assertEqual(definition.frontend_component, 'plugin:custom-field-plugin:CustomWidget.svelte')
        self.assertEqual(definition.source, 'custom-field-plugin')

    def test_disable_unregisters_field_type(self):
        self._create_plugin('custom-field-plugin-2', [
            {'type': 'custom_widget_2', 'schema_class': 'fields:CustomField'}
        ])

        self.assertTrue(self.registry.enable_plugin('custom-field-plugin-2'))
        self.assertIsNotNone(self.field_registry.get('custom_widget_2').schema_cls)

        self.assertTrue(self.registry.disable_plugin('custom-field-plugin-2'))
        # get() never returns None - unknown types fall back to the default definition
        self.assertIsNone(self.field_registry.get('custom_widget_2').schema_cls)

    def test_enable_fails_on_type_collision(self):
        self.field_registry.register(FieldTypeDefinition(type_name='select', source='core'))

        self._create_plugin('colliding-plugin', [
            {'type': 'select', 'schema_class': 'fields:CustomField'}
        ])

        success = self.registry.enable_plugin('colliding-plugin')
        self.assertFalse(success)
        self.assertEqual(self.registry.get_plugin_state('colliding-plugin'), PluginState.ERROR)
        error = self.registry.get_plugin_error('colliding-plugin')
        self.assertIn('select', error)


if __name__ == '__main__':
    unittest.main()
