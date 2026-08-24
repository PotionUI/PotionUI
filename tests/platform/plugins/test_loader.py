"""Tests for the plugin loader"""

import unittest
import tempfile
import shutil
from pathlib import Path

from src.platform.plugins.loader import PluginLoader, PluginManifest


class TestPluginLoader(unittest.TestCase):
    """Test PluginLoader functionality"""

    def setUp(self):
        """Set up test fixtures"""
        # Create temporary directories for test plugins
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"

        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.loader = PluginLoader(
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
        manifest_data: dict
    ) -> Path:
        """Helper to create a test plugin"""
        import yaml

        plugin_dir = directory / plugin_id
        plugin_dir.mkdir()

        manifest_file = plugin_dir / "manifest.yml"
        with open(manifest_file, 'w') as f:
            yaml.dump(manifest_data, f)

        return plugin_dir

    def test_discover_no_plugins(self):
        """Test discovering when no plugins exist"""
        plugins = self.loader.discover_plugins()
        self.assertEqual(len(plugins), 0)

    def test_discover_single_marketplace_plugin(self):
        """Test discovering a single marketplace plugin"""
        manifest_data = {
            'id': 'test-plugin',
            'name': 'Test Plugin',
            'version': '1.0.0',
            'description': 'A test plugin',
            'author': 'Test Author',
            'type': 'full-stack'
        }

        self._create_test_plugin(self.marketplace_dir, 'test-plugin', manifest_data)

        plugins = self.loader.discover_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].id, 'test-plugin')
        self.assertEqual(plugins[0].source, 'marketplace')
        self.assertIsNone(plugins[0].validation_error)

    def test_discover_single_local_plugin(self):
        """Test discovering a single local plugin"""
        manifest_data = {
            'id': 'local-plugin',
            'name': 'Local Plugin',
            'version': '1.0.0',
            'description': 'A local plugin',
            'author': 'Local Author',
            'type': 'backend-only'
        }

        self._create_test_plugin(self.local_dir, 'local-plugin', manifest_data)

        plugins = self.loader.discover_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].id, 'local-plugin')
        self.assertEqual(plugins[0].source, 'local')

    def test_discover_multiple_plugins(self):
        """Test discovering multiple plugins from different sources"""
        marketplace_manifest = {
            'id': 'marketplace-plugin',
            'name': 'Marketplace Plugin',
            'version': '1.0.0',
            'description': 'A marketplace plugin',
            'author': 'Marketplace Author',
            'type': 'full-stack'
        }

        local_manifest = {
            'id': 'local-plugin',
            'name': 'Local Plugin',
            'version': '1.0.0',
            'description': 'A local plugin',
            'author': 'Local Author',
            'type': 'backend-only'
        }

        self._create_test_plugin(self.marketplace_dir, 'marketplace-plugin', marketplace_manifest)
        self._create_test_plugin(self.local_dir, 'local-plugin', local_manifest)

        plugins = self.loader.discover_plugins()
        self.assertEqual(len(plugins), 2)

        plugin_ids = [p.id for p in plugins]
        self.assertIn('marketplace-plugin', plugin_ids)
        self.assertIn('local-plugin', plugin_ids)

    def test_load_manifest_with_hooks(self):
        """Test loading a manifest with the canonical hooks.backend/hooks.frontend format"""
        manifest_data = {
            'id': 'hook-plugin',
            'name': 'Hook Plugin',
            'version': '1.0.0',
            'description': 'Plugin with hooks',
            'author': 'Hook Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'generation.before_start', 'handler': 'hooks.before_generation'},
                    {'hook': 'generation.after_complete', 'handler': 'hooks.after_generation'},
                ],
                'frontend': [
                    {'hook': 'workbench.actions', 'component': 'Action.js', 'position': 'top', 'order': 5},
                ],
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'hook-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(len(manifest.hooks), 2)
        self.assertEqual(
            manifest.hooks['generation.before_start'],
            'hooks.before_generation'
        )
        self.assertEqual(
            manifest.hooks['generation.after_complete'],
            'hooks.after_generation'
        )
        self.assertEqual(len(manifest.frontend_hooks), 1)
        self.assertEqual(manifest.frontend_hooks[0]['hook_name'], 'workbench.actions')
        self.assertEqual(manifest.frontend_hooks[0]['sort_order'], 5)

    def test_load_manifest_collects_remote_hooks(self):
        """Backend hooks declared `remote: true` land in `manifest.remote_hooks`
        as "hook:handler" strings; hooks without the flag are excluded."""
        manifest_data = {
            'id': 'remote-hook-plugin',
            'name': 'Remote Hook Plugin',
            'version': '1.0.0',
            'description': 'Plugin with a remote-relevant hook',
            'author': 'Hook Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': 'prompt.transform', 'handler': 'hooks.transform', 'remote': True},
                    {'hook': 'generation.before_start', 'handler': 'hooks.before_generation'},
                ],
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'remote-hook-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(manifest.remote_hooks, ['prompt.transform:hooks.transform'])

    def test_load_manifest_normalizes_provides_hooks(self):
        """Test that both string and object provides_hooks entries normalize to dicts"""
        manifest_data = {
            'id': 'hook-provider-plugin',
            'name': 'Hook Provider Plugin',
            'version': '1.0.0',
            'description': 'Plugin providing hooks',
            'author': 'Hook Author',
            'type': 'full-stack',
            'provides_hooks': [
                'hook_provider_plugin.simple_event',
                {
                    'name': 'hook_provider_plugin.custom_event',
                    'description': 'Fires on a custom condition',
                    'payload': {'value': {'type': 'int', 'description': 'The value'}},
                    'mutable': ['value'],
                    'use_when': ['Adjust the value before it is used'],
                    'example': 'hooks.backend: [{hook: hook_provider_plugin.custom_event, handler: mod.fn}]',
                },
            ],
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'hook-provider-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(len(manifest.provides_hooks), 2)

        simple = manifest.provides_hooks[0]
        self.assertEqual(simple['name'], 'hook_provider_plugin.simple_event')
        self.assertEqual(simple['description'], '')
        self.assertEqual(simple['payload'], {})

        custom = manifest.provides_hooks[1]
        self.assertEqual(custom['name'], 'hook_provider_plugin.custom_event')
        self.assertEqual(custom['description'], 'Fires on a custom condition')
        self.assertEqual(custom['payload'], {'value': {'type': 'int', 'description': 'The value'}})
        self.assertEqual(custom['mutable'], ['value'])
        self.assertEqual(custom['use_when'], ['Adjust the value before it is used'])

    def test_load_manifest_preserves_documentation_category_metadata(self):
        manifest_data = {
            'id': 'documented-plugin',
            'name': 'Documented Plugin',
            'version': '1.0.0',
            'description': 'Plugin with categorized documentation',
            'author': 'Docs Author',
            'type': 'full-stack',
            'docs': [{
                'title': 'Model Integration',
                'path': 'docs/model-integration.md',
                'audience': 'developer',
                'order': 15,
                'category': 'Presets / Models',
                'category_order': 20,
            }],
        }

        plugin_dir = self._create_test_plugin(
            self.marketplace_dir, 'documented-plugin', manifest_data
        )
        manifest = self.loader._load_manifest(
            plugin_dir / "manifest.yml", plugin_dir, 'marketplace'
        )

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(manifest.docs[0]['category'], 'Presets / Models')
        self.assertEqual(manifest.docs[0]['category_order'], 20)

    def test_load_manifest_with_legacy_flat_hooks_fails(self):
        """The legacy flat-dict hooks format is no longer supported"""
        manifest_data = {
            'id': 'legacy-hook-plugin',
            'name': 'Legacy Hook Plugin',
            'version': '1.0.0',
            'description': 'Plugin with legacy hooks',
            'author': 'Author',
            'type': 'full-stack',
            'hooks': {
                'generation.before_start': 'hooks.before_generation',
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'legacy-hook-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNotNone(manifest.validation_error)

    def test_load_manifest_with_dependencies(self):
        """Test loading a manifest with the canonical dependencies.python/binaries format"""
        manifest_data = {
            'id': 'dep-plugin',
            'name': 'Plugin with Dependencies',
            'version': '1.0.0',
            'description': 'Plugin with dependencies',
            'author': 'Dep Author',
            'type': 'full-stack',
            'dependencies': {
                'python': ['numpy>=1.24.0', 'pillow'],
                'binaries': ['ffmpeg'],
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'dep-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNone(manifest.validation_error)
        self.assertEqual(manifest.dependencies_python, ['numpy>=1.24.0', 'pillow'])
        self.assertEqual(manifest.dependencies_binaries, ['ffmpeg'])

    def test_load_manifest_with_legacy_dependencies_fails(self):
        """The legacy flat-dict/list dependency formats are no longer supported"""
        manifest_data = {
            'id': 'legacy-dep-plugin',
            'name': 'Legacy Dependency Plugin',
            'version': '1.0.0',
            'description': 'Plugin with legacy dependencies',
            'author': 'Dep Author',
            'type': 'full-stack',
            'dependencies': ['numpy==1.24.0', 'pillow'],
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'legacy-dep-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertIsNotNone(manifest.validation_error)

    def test_load_manifest_with_frontend(self):
        """Test loading manifest with frontend entry"""
        manifest_data = {
            'id': 'frontend-plugin',
            'name': 'Frontend Plugin',
            'version': '1.0.0',
            'description': 'Plugin with frontend',
            'author': 'Frontend Author',
            'type': 'full-stack',
            'frontend': 'src/index.js'
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'frontend-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.frontend_entry, 'src/index.js')

    def test_load_manifest_missing_required_fields(self):
        """Missing required fields surface as a validation error, plugin still discovered"""
        manifest_data = {
            'id': 'incomplete-plugin',
            'name': 'Incomplete Plugin',
            # Missing version, description, author, type
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'incomplete-plugin', manifest_data)
        manifest_file = plugin_dir / "manifest.yml"

        manifest = self.loader._load_manifest(manifest_file, plugin_dir, 'marketplace')

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.id, 'incomplete-plugin')
        self.assertIsNotNone(manifest.validation_error)

    def test_load_plugin_module(self):
        """Test loading a Python module from a plugin"""
        manifest_data = {
            'id': 'module-plugin',
            'name': 'Module Plugin',
            'version': '1.0.0',
            'description': 'Plugin with module',
            'author': 'Module Author',
            'type': 'full-stack'
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'module-plugin', manifest_data)

        # Create a Python module
        module_dir = plugin_dir / "hooks"
        module_dir.mkdir()

        module_file = module_dir / "test_module.py"
        with open(module_file, 'w') as f:
            f.write("""
def test_function():
    return "Hello from plugin"
""")

        plugins = self.loader.discover_plugins()
        manifest = plugins[0]

        # Load the module
        module = self.loader.load_plugin_module(manifest, "hooks.test_module")

        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, 'test_function'))
        self.assertEqual(module.test_function(), "Hello from plugin")

    def test_load_hook_handler(self):
        """Test loading a hook handler function"""
        manifest_data = {
            'id': 'handler-plugin',
            'name': 'Handler Plugin',
            'version': '1.0.0',
            'description': 'Plugin with handler',
            'author': 'Handler Author',
            'type': 'full-stack'
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'handler-plugin', manifest_data)

        # Create a handler module
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()

        handler_file = hooks_dir / "generation.py"
        with open(handler_file, 'w') as f:
            f.write("""
def before_start(context):
    context.set("processed", True)
    return context
""")

        plugins = self.loader.discover_plugins()
        manifest = plugins[0]

        # Load the handler
        handler = self.loader.load_hook_handler(manifest, "hooks.generation.before_start")

        self.assertIsNotNone(handler)
        self.assertTrue(callable(handler))

    def test_validate_dependencies_satisfied(self):
        """Test validating dependencies that are satisfied"""
        manifest_data = {
            'id': 'dep-satisfied-plugin',
            'name': 'Dependency Satisfied Plugin',
            'version': '1.0.0',
            'description': 'Plugin with satisfied dependencies',
            'author': 'Author',
            'type': 'full-stack',
            'dependencies': {
                'python': ['sys', 'os'],  # Built-in modules, always available
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'dep-satisfied-plugin', manifest_data)
        plugins = self.loader.discover_plugins()
        manifest = plugins[0]

        satisfied, missing = self.loader.validate_dependencies(manifest)

        self.assertTrue(satisfied)
        self.assertEqual(len(missing), 0)

    def test_validate_dependencies_missing(self):
        """Test validating dependencies that are missing"""
        manifest_data = {
            'id': 'dep-missing-plugin',
            'name': 'Dependency Missing Plugin',
            'version': '1.0.0',
            'description': 'Plugin with missing dependencies',
            'author': 'Author',
            'type': 'full-stack',
            'dependencies': {
                'python': ['nonexistent_package_xyz==1.0.0'],
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'dep-missing-plugin', manifest_data)
        plugins = self.loader.discover_plugins()
        manifest = plugins[0]

        satisfied, missing = self.loader.validate_dependencies(manifest)

        self.assertFalse(satisfied)
        self.assertEqual(len(missing), 1)
        self.assertIn('nonexistent_package_xyz==1.0.0', missing)

    def test_validate_dependencies_missing_binary(self):
        """Test validating a binary dependency that isn't on PATH"""
        manifest_data = {
            'id': 'binary-missing-plugin',
            'name': 'Binary Missing Plugin',
            'version': '1.0.0',
            'description': 'Plugin with a missing binary dependency',
            'author': 'Author',
            'type': 'full-stack',
            'dependencies': {
                'binaries': ['definitely_not_a_real_binary_xyz'],
            }
        }

        plugin_dir = self._create_test_plugin(self.marketplace_dir, 'binary-missing-plugin', manifest_data)
        plugins = self.loader.discover_plugins()
        manifest = plugins[0]

        satisfied, missing = self.loader.validate_dependencies(manifest)

        self.assertFalse(satisfied)
        self.assertIn('definitely_not_a_real_binary_xyz', missing)



if __name__ == '__main__':
    unittest.main()
