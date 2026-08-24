"""Advanced tests for the plugin registry - thread safety, edge cases, and integration"""

import unittest
import tempfile
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.hooks import HookContext


class TestPluginRegistryThreadSafety(unittest.TestCase):
    """Test thread-safety of plugin registry operations"""

    def setUp(self):
        """Set up test fixtures"""
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
            for hook_def in manifest_data['hooks'].get('backend', []):
                hook_name = hook_def['hook']
                handler_path = hook_def['handler']
                parts = handler_path.rsplit('.', 1)
                if len(parts) == 2:
                    module_path, function_name = parts
                    module_parts = module_path.split('.')
                    current_dir = plugin_dir

                    for part in module_parts:
                        current_dir = current_dir / part
                        current_dir.mkdir(exist_ok=True)
                        init_file = current_dir / "__init__.py"
                        if not init_file.exists():
                            init_file.touch()

                    handler_file = current_dir.parent / f"{module_parts[-1]}.py"
                    with open(handler_file, 'w') as f:
                        f.write(f"""
def {function_name}(context):
    context.set("{hook_name}_executed", True)
    return context
""")

        return plugin_dir

    def test_concurrent_discovery(self):
        """Test that concurrent discovery operations are thread-safe"""
        # Create multiple plugins
        for i in range(5):
            manifest_data = {
                'id': f'plugin-{i}',
                'name': f'Plugin {i}',
                'version': '1.0.0',
                'description': f'Plugin {i}',
                'author': 'Author',
                'type': 'full-stack'
            }
            self._create_test_plugin(
                self.marketplace_dir,
                f'plugin-{i}',
                manifest_data
            )

        results = []
        errors = []

        def discover():
            try:
                plugins = self.registry.get_all_plugins()
                results.append(len(plugins))
            except Exception as e:
                errors.append(e)

        # Run discovery in multiple threads
        threads = [threading.Thread(target=discover) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should have discovered the same number of plugins
        self.assertEqual(len(errors), 0)
        self.assertTrue(all(r == 5 for r in results))

    def test_concurrent_enable_disable(self):
        """Test concurrent enable/disable operations"""
        manifest_data = {
            'id': 'concurrent-plugin',
            'name': 'Concurrent Plugin',
            'version': '1.0.0',
            'description': 'Plugin for concurrency testing',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'concurrent-plugin',
            manifest_data,
            create_handler=True
        )

        # Trigger initial discovery
        self.registry.get_all_plugins()

        errors = []

        def toggle():
            try:
                for _ in range(5):
                    self.registry.enable_plugin('concurrent-plugin')
                    time.sleep(0.001)
                    self.registry.disable_plugin('concurrent-plugin')
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=toggle) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not have any errors
        self.assertEqual(len(errors), 0)

        # Final state should be consistent
        state = self.registry.get_plugin_state('concurrent-plugin')
        self.assertIn(state, [PluginState.ENABLED, PluginState.DISABLED])

    def test_concurrent_hook_execution(self):
        """Test concurrent hook execution"""
        manifest_data = {
            'id': 'exec-plugin',
            'name': 'Exec Plugin',
            'version': '1.0.0',
            'description': 'Plugin for execution testing',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.handler.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'exec-plugin',
            manifest_data,
            create_handler=True
        )

        self.registry.enable_plugin('exec-plugin')

        results = []
        errors = []

        def execute():
            try:
                context, success = self.registry.execute_hook(
                    'test.hook',
                    initial_data={'counter': 0}
                )
                results.append((context, success))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=execute) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All executions should succeed
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 10)
        self.assertTrue(all(success for _, success in results))


class TestPluginRegistryEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions"""

    def setUp(self):
        """Set up test fixtures"""
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
            for hook_def in manifest_data['hooks'].get('backend', []):
                hook_name = hook_def['hook']
                handler_path = hook_def['handler']
                parts = handler_path.rsplit('.', 1)
                if len(parts) == 2:
                    module_path, function_name = parts
                    module_parts = module_path.split('.')
                    current_dir = plugin_dir

                    for part in module_parts:
                        current_dir = current_dir / part
                        current_dir.mkdir(exist_ok=True)
                        init_file = current_dir / "__init__.py"
                        if not init_file.exists():
                            init_file.touch()

                    handler_file = current_dir.parent / f"{module_parts[-1]}.py"
                    with open(handler_file, 'w') as f:
                        f.write(f"""
def {function_name}(context):
    context.set("{hook_name}_executed", True)
    return context
""")

        return plugin_dir

    def test_invalid_plugin_directory(self):
        """Test handling of invalid plugin directories"""
        # Create a file instead of directory
        invalid_file = self.marketplace_dir / "not-a-plugin.txt"
        invalid_file.touch()

        # Should not crash, just skip the file
        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 0)

    def test_plugin_with_missing_manifest(self):
        """Test handling of plugin directory without manifest"""
        plugin_dir = self.marketplace_dir / "no-manifest"
        plugin_dir.mkdir()

        # Should skip this directory
        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 0)

    def test_plugin_with_corrupted_manifest(self):
        """Corrupted manifest stays discovered, but goes straight to ERROR state"""
        plugin_dir = self.marketplace_dir / "corrupted-plugin"
        plugin_dir.mkdir()

        manifest_file = plugin_dir / "manifest.yml"
        with open(manifest_file, 'w') as f:
            f.write("invalid: yaml: content: [")

        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 1)

        state = self.registry.get_plugin_state('corrupted-plugin')
        self.assertEqual(state, PluginState.ERROR)
        self.assertIsNotNone(self.registry.get_plugin_error('corrupted-plugin'))

    def test_enable_plugin_with_missing_handler(self):
        """Test enabling a plugin with missing handler file"""
        manifest_data = {
            'id': 'missing-handler',
            'name': 'Missing Handler',
            'version': '1.0.0',
            'description': 'Plugin with missing handler',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.nonexistent.handle'}
                ]
            }
        }

        self._create_test_plugin(
            self.marketplace_dir,
            'missing-handler',
            manifest_data,
            create_handler=False  # Don't create handler
        )

        # Should fail to enable
        success = self.registry.enable_plugin('missing-handler')
        self.assertFalse(success)

        state = self.registry.get_plugin_state('missing-handler')
        self.assertIn(state, [PluginState.DISCOVERED, PluginState.ERROR])

    def test_execute_hook_with_handler_exception(self):
        """Test hook execution when handler raises exception"""
        manifest_data = {
            'id': 'error-plugin',
            'name': 'Error Plugin',
            'version': '1.0.0',
            'description': 'Plugin that throws errors',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.error_handler.handle'}
                ]
            }
        }

        plugin_dir = self._create_test_plugin(
            self.marketplace_dir,
            'error-plugin',
            manifest_data
        )

        # Create handler that raises exception
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "__init__.py").touch()

        handler_file = hooks_dir / "error_handler.py"
        with open(handler_file, 'w') as f:
            f.write("""
def handle(context):
    raise RuntimeError("Intentional error")
""")

        self.registry.enable_plugin('error-plugin')

        # Execute hook - should not crash
        context, success = self.registry.execute_hook('test.hook')

        # Depending on implementation, might succeed with error logged
        # or might fail. Either way, should not crash.
        self.assertIsNotNone(context)

    def test_multiple_plugins_same_hook_priority(self):
        """Test multiple plugins registering same hook"""
        for i in range(3):
            manifest_data = {
                'id': f'priority-plugin-{i}',
                'name': f'Priority Plugin {i}',
                'version': '1.0.0',
                'description': f'Plugin {i}',
                'author': 'Author',
                'type': 'full-stack',
                'hooks': {
                    'backend': [
                        {'hook': 'shared.hook', 'handler': f'hooks.handler{i}.handle'}
                    ]
                }
            }

            plugin_dir = self._create_test_plugin(
                self.marketplace_dir,
                f'priority-plugin-{i}',
                manifest_data
            )

            # Create unique handler
            hooks_dir = plugin_dir / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "__init__.py").touch()

            handler_file = hooks_dir / f"handler{i}.py"
            with open(handler_file, 'w') as f:
                f.write(f"""
def handle(context):
    order = context.get("order", [])
    order.append({i})
    context.set("order", order)
    return context
""")

        # Enable all plugins
        self.registry.discover_plugins()
        for i in range(3):
            self.registry.enable_plugin(f'priority-plugin-{i}')

        # Execute hook
        context, success = self.registry.execute_hook('shared.hook')

        # All handlers should have executed
        self.assertTrue(success)
        order = context.get("order", [])
        self.assertEqual(len(order), 3)
        self.assertEqual(sorted(order), [0, 1, 2])

    def test_disable_plugin_during_hook_execution(self):
        """Test disabling a plugin doesn't affect in-progress execution"""
        manifest_data = {
            'id': 'disable-test',
            'name': 'Disable Test',
            'version': '1.0.0',
            'description': 'Plugin for disable testing',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'test.hook', 'handler': 'hooks.handler.handle'}
                ]
            }
        }

        plugin_dir = self._create_test_plugin(
            self.marketplace_dir,
            'disable-test',
            manifest_data
        )

        # Create handler with delay
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "__init__.py").touch()

        handler_file = hooks_dir / "handler.py"
        with open(handler_file, 'w') as f:
            f.write("""
import time
def handle(context):
    time.sleep(0.1)
    context.set("completed", True)
    return context
""")

        self.registry.enable_plugin('disable-test')

        # Execute hook in thread
        result = {}

        def execute():
            ctx, success = self.registry.execute_hook('test.hook')
            result['context'] = ctx
            result['success'] = success

        thread = threading.Thread(target=execute)
        thread.start()

        # Disable while executing
        time.sleep(0.05)
        self.registry.disable_plugin('disable-test')

        thread.join()

        # Execution should still complete successfully
        self.assertTrue(result.get('success', False))
        self.assertTrue(result['context'].get('completed', False))

    def test_rediscover_after_plugin_added(self):
        """Test that rediscovery picks up new plugins"""
        # Initial discovery
        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 0)

        # Add a plugin
        manifest_data = {
            'id': 'new-plugin',
            'name': 'New Plugin',
            'version': '1.0.0',
            'description': 'A new plugin',
            'author': 'Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'new-plugin', manifest_data)

        # Rediscover
        self.registry.discover_plugins()
        plugins = self.registry.get_all_plugins()
        self.assertEqual(len(plugins), 1)


    def test_empty_hooks_list(self):
        """Test plugin with empty hooks dictionary"""
        manifest_data = {
            'id': 'no-hooks',
            'name': 'No Hooks',
            'version': '1.0.0',
            'description': 'Plugin without hooks',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {}
        }

        self._create_test_plugin(self.marketplace_dir, 'no-hooks', manifest_data)

        plugin = self.registry.get_plugin('no-hooks')
        self.assertIsNotNone(plugin)

        # Should be able to enable
        success = self.registry.enable_plugin('no-hooks')
        self.assertTrue(success)

        # Should have no hooks
        self.assertEqual(len(self.registry._plugin_hooks.get('no-hooks', set())), 0)


if __name__ == '__main__':
    unittest.main()
