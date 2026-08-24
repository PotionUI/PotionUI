"""Tests for plugin-provided automation node registration (A3)."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.automation_nodes import NodeTypeRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState


class TestPluginAutomationNodeRegistration(unittest.TestCase):
    """Test that a plugin's `automation_nodes:` manifest entries are wired
    into a `NodeTypeRegistry` on enable, and removed on disable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.automation_node_registry = NodeTypeRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            automation_node_registry=self.automation_node_registry,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, automation_nodes: list) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'full-stack',
            'automation_nodes': automation_nodes,
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)

        (plugin_dir / "nodes.py").write_text(
            "async def execute_custom_action(ctx):\n"
            "    from src.platform.plugins.automation_nodes import NodeResult\n"
            "    return NodeResult(output={'ok': True})\n\n"
            "async def start_custom_trigger(trigger):\n"
            "    pass\n\n"
            "async def stop_custom_trigger(trigger):\n"
            "    pass\n"
        )

        return plugin_dir

    def test_enable_registers_action_node(self):
        self._create_plugin('custom-node-plugin', [
            {
                'key': 'action.custom_thing',
                'kind': 'action',
                'title': 'Custom Thing',
                'description': 'Does a custom thing',
                'icon': 'bolt',
                'category': 'custom',
                'config_schema': [{'name': 'foo', 'type': 'string', 'label': 'Foo'}],
                'handler': 'nodes.execute_custom_action',
            }
        ])

        success = self.registry.enable_plugin('custom-node-plugin')
        self.assertTrue(success)

        spec = self.automation_node_registry.get('action.custom_thing')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind, 'action')
        self.assertEqual(spec.title, 'Custom Thing')
        self.assertEqual(spec.source, 'custom-node-plugin')
        self.assertIsNotNone(spec.execute)
        self.assertEqual(len(spec.config_schema), 1)

    def test_enable_registers_custom_trigger_node(self):
        self._create_plugin('custom-trigger-plugin', [
            {
                'key': 'trigger.custom_thing',
                'kind': 'trigger',
                'title': 'Custom Trigger',
                'start_handler': 'nodes.start_custom_trigger',
                'stop_handler': 'nodes.stop_custom_trigger',
            }
        ])

        success = self.registry.enable_plugin('custom-trigger-plugin')
        self.assertTrue(success)

        spec = self.automation_node_registry.get('trigger.custom_thing')
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.start)
        self.assertIsNotNone(spec.stop)

    def test_disable_unregisters_node_type(self):
        self._create_plugin('custom-node-plugin-2', [
            {'key': 'action.custom_thing_2', 'kind': 'action', 'title': 'Custom Thing 2',
             'handler': 'nodes.execute_custom_action'}
        ])

        self.assertTrue(self.registry.enable_plugin('custom-node-plugin-2'))
        self.assertIsNotNone(self.automation_node_registry.get('action.custom_thing_2'))

        self.assertTrue(self.registry.disable_plugin('custom-node-plugin-2'))
        self.assertIsNone(self.automation_node_registry.get('action.custom_thing_2'))

    def test_enable_fails_on_key_collision(self):
        from src.platform.plugins.automation_nodes import NodeTypeSpec
        self.automation_node_registry.register(NodeTypeSpec(
            key='action.custom_thing_3', kind='action', title='Existing', source='core',
        ))

        self._create_plugin('colliding-node-plugin', [
            {'key': 'action.custom_thing_3', 'kind': 'action', 'title': 'Colliding',
             'handler': 'nodes.execute_custom_action'}
        ])

        success = self.registry.enable_plugin('colliding-node-plugin')
        self.assertFalse(success)
        self.assertEqual(self.registry.get_plugin_state('colliding-node-plugin'), PluginState.ERROR)
        error = self.registry.get_plugin_error('colliding-node-plugin')
        self.assertIn('action.custom_thing_3', error)

    def test_enable_registers_dynamic_ports_config_key(self):
        """A plugin can declare a switch-like node - `dynamic_ports_config_key`
        round-trips manifest.yml -> AutomationNodeSpec (pydantic) -> PluginManifest
        (dataclass, via model_dump()) -> PluginRegistry -> NodeTypeSpec."""
        self._create_plugin('custom-switch-plugin', [
            {
                'key': 'condition.custom_switch',
                'kind': 'condition',
                'title': 'Custom Switch',
                'config_schema': [{'name': 'cases', 'type': 'textbox', 'title': 'Cases'}],
                'handler': 'nodes.execute_custom_action',
                'dynamic_ports_config_key': 'cases',
            }
        ])

        success = self.registry.enable_plugin('custom-switch-plugin')
        self.assertTrue(success)

        spec = self.automation_node_registry.get('condition.custom_switch')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.dynamic_ports_config_key, 'cases')

    def test_enable_fails_without_registry_configured(self):
        registry_without_automation = PluginRegistry(
            str(self.marketplace_dir), str(self.local_dir),
        )
        self._create_plugin('no-registry-plugin', [
            {'key': 'action.whatever', 'kind': 'action', 'title': 'Whatever',
             'handler': 'nodes.execute_custom_action'}
        ])

        success = registry_without_automation.enable_plugin('no-registry-plugin')
        self.assertFalse(success)
        error = registry_without_automation.get_plugin_error('no-registry-plugin')
        self.assertIn('automation node registry', error)


if __name__ == '__main__':
    unittest.main()
