"""Tests for the plugin registry"""

import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.hooks import HookContext


class TestPluginRegistry(unittest.TestCase):
    """Test PluginRegistry functionality"""

    def setUp(self):
        """Set up test fixtures"""
        # Create temporary directories for test plugins
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"

        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir)
        )

    def tearDown(self):
        """Clean up test fixtures"""
        shutil.rmtree(self.temp_dir)

    def _create_test_plugin(
        self,
        directory: Path,
        plugin_id: str,
        manifest_data: dict,
        create_handler: bool = False
    ) -> Path:
        """Helper to create a test plugin"""
        import yaml

        plugin_dir = directory / plugin_id
        plugin_dir.mkdir()

        manifest_file = plugin_dir / "manifest.yml"
        with open(manifest_file, 'w') as f:
            yaml.dump(manifest_data, f)

        if create_handler and 'hooks' in manifest_data:
            # Create handler modules
            for hook_def in manifest_data['hooks'].get('backend', []):
                hook_name = hook_def['hook']
                handler_path = hook_def['handler']
                # Parse handler path
                parts = handler_path.rsplit('.', 1)
                if len(parts) == 2:
                    module_path, function_name = parts

                    # Create module file
                    module_parts = module_path.split('.')
                    current_dir = plugin_dir

                    for part in module_parts:
                        current_dir = current_dir / part
                        current_dir.mkdir(exist_ok=True)

                        # Create __init__.py
                        init_file = current_dir / "__init__.py"
                        if not init_file.exists():
                            init_file.touch()

                    # Create handler file
                    handler_file = current_dir.parent / f"{module_parts[-1]}.py"
                    with open(handler_file, 'w') as f:
                        f.write(f"""
def {function_name}(context):
    context.set("{hook_name}_executed", True)
    return context
""")

        return plugin_dir

    def test_lazy_discovery(self):
        """Test that plugins are discovered lazily"""
        # Registry should not discover until accessed
        self.assertFalse(self.registry._discovered)

        # Access should trigger discovery
        plugins = self.registry.get_all_plugins()
        self.assertTrue(self.registry._discovered)

    def test_get_all_plugins_empty(self):
        """Test getting all plugins when none exist"""
        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 0)

    def test_get_all_plugins(self):
        """Test getting all discovered plugins"""
        manifest_data = {
            'id': 'test-plugin',
            'name': 'Test Plugin',
            'version': '1.0.0',
            'description': 'A test plugin',
            'author': 'Test Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'test-plugin', manifest_data)

        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].id, 'test-plugin')

    def test_get_plugin(self):
        """Test getting a specific plugin by ID"""
        manifest_data = {
            'id': 'specific-plugin',
            'name': 'Specific Plugin',
            'version': '1.0.0',
            'description': 'A specific plugin',
            'author': 'Specific Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'specific-plugin', manifest_data)

        plugin = self.registry.get_plugin('specific-plugin')
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.id, 'specific-plugin')

        # Non-existent plugin should return None
        self.assertIsNone(self.registry.get_plugin('nonexistent'))

    def test_get_plugin_state(self):
        """Test getting plugin state"""
        manifest_data = {
            'id': 'state-plugin',
            'name': 'State Plugin',
            'version': '1.0.0',
            'description': 'Plugin for state testing',
            'author': 'State Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'state-plugin', manifest_data)

        # Initial state should be DISCOVERED
        state = self.registry.get_plugin_state('state-plugin')
        self.assertEqual(state, PluginState.DISCOVERED)

    def test_enable_plugin_with_hooks(self):
        """Test enabling a plugin with hooks"""
        manifest_data = {
            'id': 'hook-plugin',
            'name': 'Hook Plugin',
            'version': '1.0.0',
            'description': 'Plugin with hooks',
            'author': 'Hook Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.test_handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'hook-plugin',
            manifest_data,
            create_handler=True
        )

        # Enable the plugin
        success = self.registry.enable_plugin('hook-plugin')
        self.assertTrue(success)

        # Check state
        state = self.registry.get_plugin_state('hook-plugin')
        self.assertEqual(state, PluginState.ENABLED)

        # Check that hook was registered
        self.assertIn('hook-plugin', self.registry.get_plugins_for_hook('test.hook'))

    def test_enable_already_enabled_plugin(self):
        """Test enabling a plugin that's already enabled"""
        manifest_data = {
            'id': 'already-enabled',
            'name': 'Already Enabled',
            'version': '1.0.0',
            'description': 'Already enabled plugin',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.test_handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'already-enabled',
            manifest_data,
            create_handler=True
        )

        # Enable twice
        self.assertTrue(self.registry.enable_plugin('already-enabled'))
        self.assertTrue(self.registry.enable_plugin('already-enabled'))

        state = self.registry.get_plugin_state('already-enabled')
        self.assertEqual(state, PluginState.ENABLED)

    def test_enable_nonexistent_plugin(self):
        """Test enabling a plugin that doesn't exist"""
        success = self.registry.enable_plugin('nonexistent')
        self.assertFalse(success)

    def test_disable_plugin(self):
        """Test disabling an enabled plugin"""
        manifest_data = {
            'id': 'disable-plugin',
            'name': 'Disable Plugin',
            'version': '1.0.0',
            'description': 'Plugin for disable testing',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.test_handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'disable-plugin',
            manifest_data,
            create_handler=True
        )

        # Enable then disable
        self.assertTrue(self.registry.enable_plugin('disable-plugin'))
        self.assertTrue(self.registry.disable_plugin('disable-plugin'))

        # Check state
        state = self.registry.get_plugin_state('disable-plugin')
        self.assertEqual(state, PluginState.DISABLED)

        # Check that hooks were unregistered
        self.assertNotIn('disable-plugin', self.registry.get_plugins_for_hook('test.hook'))

    def test_disable_already_disabled_plugin(self):
        """Test disabling a plugin that's already disabled"""
        manifest_data = {
            'id': 'already-disabled',
            'name': 'Already Disabled',
            'version': '1.0.0',
            'description': 'Already disabled plugin',
            'author': 'Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'already-disabled', manifest_data)

        # Disable twice
        self.assertTrue(self.registry.disable_plugin('already-disabled'))
        self.assertTrue(self.registry.disable_plugin('already-disabled'))

    def test_get_enabled_plugins(self):
        """Test getting list of enabled plugins"""
        # Create two plugins
        for i in range(2):
            manifest_data = {
                'id': f'plugin-{i}',
                'name': f'Plugin {i}',
                'version': '1.0.0',
                'description': f'Plugin {i}',
                'author': 'Author',
                'type': 'full-stack',
                'hooks': {
                    'backend': [
                        {'hook': 'test.hook', 'handler': 'hooks.test_handler.handle'}
                    ]
                }
            }

            self._create_test_plugin(
                self.marketplace_dir,
                f'plugin-{i}',
                manifest_data,
                create_handler=True
            )

        # Enable only the first one
        self.registry.enable_plugin('plugin-0')

        enabled = self.registry.get_enabled_plugins()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].id, 'plugin-0')

    def test_execute_hook(self):
        """Test executing a hook"""
        manifest_data = {
            'id': 'exec-plugin',
            'name': 'Exec Plugin',
            'version': '1.0.0',
            'description': 'Plugin for execution testing',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.test_handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'exec-plugin',
            manifest_data,
            create_handler=True
        )

        # Enable plugin
        self.registry.enable_plugin('exec-plugin')

        # Execute hook
        context, success = self.registry.execute_hook(
            'test.hook',
            initial_data={'key': 'value'}
        )

        self.assertTrue(success)
        self.assertTrue(context.get('test.hook_executed'))
        self.assertEqual(context.get('key'), 'value')

    def test_execute_hook_no_handlers(self):
        """Test executing a hook with no handlers"""
        context, success = self.registry.execute_hook(
            'nonexistent.hook',
            initial_data={'key': 'value'}
        )

        # Should succeed with no modifications
        self.assertTrue(success)
        self.assertEqual(context.get('key'), 'value')

    def test_get_plugins_for_hook(self):
        """Test getting plugins that handle a specific hook"""
        # Create two plugins with same hook but unique handler names
        for i in range(2):
            manifest_data = {
                'id': f'hook-handler-{i}',
                'name': f'Hook Handler {i}',
                'version': '1.0.0',
                'description': f'Handler {i}',
                'author': 'Author',
                'type': 'full-stack',
                'hooks': {
                    'backend': [
                        {'hook': 'shared.hook', 'handler': f'hooks.handler{i}.handle'}
                    ]
                }
            }

            self._create_test_plugin(
                self.marketplace_dir,
                f'hook-handler-{i}',
                manifest_data,
                create_handler=True
            )

        # Rediscover plugins to pick up both
        self.registry.discover_plugins()

        # Enable both plugins
        self.registry.enable_plugin('hook-handler-0')
        self.registry.enable_plugin('hook-handler-1')

        # Get plugins for hook
        plugins = self.registry.get_plugins_for_hook('shared.hook')
        self.assertEqual(len(plugins), 2)
        self.assertIn('hook-handler-0', plugins)
        self.assertIn('hook-handler-1', plugins)

if __name__ == '__main__':
    unittest.main()
