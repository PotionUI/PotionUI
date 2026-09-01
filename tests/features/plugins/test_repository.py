import unittest
from datetime import datetime
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.plugins.repository import PluginRepository
from src.features.plugins.records import Plugin, PluginSetting, PluginHook


class TestPluginRepository(PersistenceTestBase):
    """Test plugin repository operations"""

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = PluginRepository()

    def _create_sample_plugin(self, plugin_id="test_plugin_01"):
        """Helper to create a sample plugin"""
        return Plugin(
            id=plugin_id,
            name="Test Plugin",
            version="1.0.0",
            type="full-stack",
            enabled=True,
            manifest_path="/plugins/test/manifest.json",
            description="A test plugin",
            author="Test Author",
            installed_at=datetime.now(),
            updated_at=datetime.now()
        )

    # ========== Plugin CRUD Tests ==========

    def test_create_plugin(self):
        """Test creating a plugin"""
        plugin = self._create_sample_plugin()
        created = self.repo.create_plugin(plugin)

        self.assertIsNotNone(created)
        self.assertEqual(created.id, plugin.id)
        self.assertEqual(created.name, plugin.name)
        self.assertEqual(created.version, plugin.version)

    def test_create_plugin_generates_id(self):
        """Test that plugin ID is generated if not provided"""
        plugin = Plugin(
            id="",
            name="Test Plugin",
            version="1.0.0",
            type="frontend-only",
            enabled=True,
            manifest_path="/plugins/test/manifest.json"
        )

        created = self.repo.create_plugin(plugin)
        self.assertNotEqual(created.id, "")
        self.assertTrue(len(created.id) > 0)

    def test_get_plugin_by_id(self):
        """Test getting a plugin by ID"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        fetched = self.repo.get_plugin_by_id(plugin.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, plugin.id)
        self.assertEqual(fetched.name, plugin.name)

    def test_get_plugin_by_id_not_found(self):
        """Test getting a non-existent plugin"""
        fetched = self.repo.get_plugin_by_id("nonexistent")
        self.assertIsNone(fetched)

    def test_get_all_plugins(self):
        """Test getting all plugins"""
        plugin1 = self._create_sample_plugin("plugin_01")
        plugin1.name = "Plugin A"
        plugin2 = self._create_sample_plugin("plugin_02")
        plugin2.name = "Plugin B"

        self.repo.create_plugin(plugin1)
        self.repo.create_plugin(plugin2)

        all_plugins = self.repo.get_all_plugins()
        self.assertEqual(len(all_plugins), 2)
        self.assertEqual(all_plugins[0].name, "Plugin A")
        self.assertEqual(all_plugins[1].name, "Plugin B")

    def test_get_enabled_plugins(self):
        """Test getting only enabled plugins"""
        plugin1 = self._create_sample_plugin("plugin_01")
        plugin1.name = "Enabled Plugin"
        plugin1.enabled = True

        plugin2 = self._create_sample_plugin("plugin_02")
        plugin2.name = "Disabled Plugin"
        plugin2.enabled = False

        self.repo.create_plugin(plugin1)
        self.repo.create_plugin(plugin2)

        enabled = self.repo.get_enabled_plugins()
        self.assertEqual(len(enabled), 1)
        self.assertTrue(enabled[0].enabled)

    def test_get_plugins_by_type(self):
        """Test getting plugins by type"""
        plugin1 = self._create_sample_plugin("plugin_01")
        plugin1.type = "backend-only"

        plugin2 = self._create_sample_plugin("plugin_02")
        plugin2.type = "frontend-only"

        self.repo.create_plugin(plugin1)
        self.repo.create_plugin(plugin2)

        backend_plugins = self.repo.get_plugins_by_type("backend-only")
        self.assertEqual(len(backend_plugins), 1)
        self.assertEqual(backend_plugins[0].type, "backend-only")

    def test_update_plugin(self):
        """Test updating a plugin"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        plugin.name = "Updated Plugin"
        plugin.version = "2.0.0"
        plugin.enabled = False

        updated = self.repo.update_plugin(plugin.id, plugin)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.name, "Updated Plugin")
        self.assertEqual(updated.version, "2.0.0")
        self.assertFalse(updated.enabled)

    def test_update_plugin_not_found(self):
        """Test updating a non-existent plugin"""
        plugin = self._create_sample_plugin()
        updated = self.repo.update_plugin("nonexistent", plugin)
        self.assertIsNone(updated)

    def test_delete_plugin(self):
        """Test deleting a plugin"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        result = self.repo.delete_plugin(plugin.id)
        self.assertTrue(result)

        fetched = self.repo.get_plugin_by_id(plugin.id)
        self.assertIsNone(fetched)

    def test_enable_disable_plugin(self):
        """Test enabling and disabling a plugin"""
        plugin = self._create_sample_plugin()
        plugin.enabled = False
        self.repo.create_plugin(plugin)

        # Enable
        result = self.repo.enable_plugin(plugin.id)
        self.assertTrue(result)
        fetched = self.repo.get_plugin_by_id(plugin.id)
        self.assertTrue(fetched.enabled)

        # Disable
        result = self.repo.disable_plugin(plugin.id)
        self.assertTrue(result)
        fetched = self.repo.get_plugin_by_id(plugin.id)
        self.assertFalse(fetched.enabled)

    # ========== Plugin Settings Tests ==========

    def test_set_and_get_plugin_setting(self):
        """Test setting and getting a plugin setting"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        setting = self.repo.set_plugin_setting(
            plugin.id,
            "test_key",
            "test_value"
        )

        self.assertIsNotNone(setting)
        self.assertEqual(setting.plugin_id, plugin.id)
        self.assertEqual(setting.setting_key, "test_key")
        self.assertEqual(setting.setting_value, "test_value")

        # Get the setting
        fetched = self.repo.get_plugin_setting(plugin.id, "test_key")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.setting_value, "test_value")

    def test_set_plugin_setting_upsert(self):
        """Test that setting a plugin setting upserts"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        # Set initial value
        setting1 = self.repo.set_plugin_setting(plugin.id, "test_key", "value1")
        self.assertEqual(setting1.setting_value, "value1")

        # Update to new value
        setting2 = self.repo.set_plugin_setting(plugin.id, "test_key", "value2")
        self.assertEqual(setting2.setting_value, "value2")

        # Verify only one setting exists
        settings = self.repo.get_plugin_settings(plugin.id)
        self.assertEqual(len(settings), 1)

    def test_get_plugin_settings_user_specific(self):
        """Test getting user-specific settings"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        # Create a test user first
        user_id = self.create_test_user("user_123", "testuser123", "test123@example.com")

        # Create global settings
        self.repo.set_plugin_setting(plugin.id, "global_key", "global_value")

        # Create user-specific settings
        self.repo.set_plugin_setting(plugin.id, "user_key", "user_value", user_id=user_id)

        # Get global settings
        global_settings = self.repo.get_plugin_settings(plugin.id)
        self.assertEqual(len(global_settings), 1)
        self.assertEqual(global_settings[0].setting_key, "global_key")

        # Get user-specific settings
        user_settings = self.repo.get_plugin_settings(plugin.id, user_id=user_id)
        self.assertEqual(len(user_settings), 1)
        self.assertEqual(user_settings[0].setting_key, "user_key")

    def test_delete_plugin_setting(self):
        """Test deleting a plugin setting"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)
        self.repo.set_plugin_setting(plugin.id, "test_key", "test_value")

        result = self.repo.delete_plugin_setting(plugin.id, "test_key")
        self.assertTrue(result)

        setting = self.repo.get_plugin_setting(plugin.id, "test_key")
        self.assertIsNone(setting)

    def test_secret_setting(self):
        """Test creating a secret setting"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        setting = self.repo.set_plugin_setting(
            plugin.id,
            "api_key",
            "secret_value",
            is_secret=True
        )

        self.assertTrue(setting.is_secret)
        self.assertEqual(setting.setting_value, "secret_value")

    # ========== Plugin Hooks Tests ==========

    def test_register_hook(self):
        """Test registering a hook"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="before_generation",
            hook_type="backend",
            handler_path="/plugins/test/handlers/before_generation.py"
        )

        registered = self.repo.register_hook(hook)

        self.assertIsNotNone(registered)
        self.assertGreater(registered.id, 0)
        self.assertEqual(registered.plugin_id, plugin.id)

    def test_get_plugin_hooks(self):
        """Test getting all hooks for a plugin"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook1 = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="hook1",
            hook_type="backend",
            handler_path="/path/to/hook1.py"
        )
        hook2 = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="hook2",
            hook_type="frontend",
            component_path="/path/to/Component.svelte"
        )

        self.repo.register_hook(hook1)
        self.repo.register_hook(hook2)

        hooks = self.repo.get_plugin_hooks(plugin.id)
        self.assertEqual(len(hooks), 2)

    def test_get_hooks_by_name(self):
        """Test getting hooks by name"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook1 = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="before_generation",
            hook_type="backend",
            handler_path="/path/to/hook1.py",
            sort_order=0
        )
        hook2 = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="before_generation",
            hook_type="backend",
            handler_path="/path/to/hook2.py",
            sort_order=1
        )

        self.repo.register_hook(hook1)
        self.repo.register_hook(hook2)

        hooks = self.repo.get_hooks_by_name("before_generation")
        self.assertEqual(len(hooks), 2)
        self.assertEqual(hooks[0].sort_order, 0)
        self.assertEqual(hooks[1].sort_order, 1)

    def test_get_hooks_by_name_only_enabled_plugins(self):
        """Test that get_hooks_by_name only returns hooks from enabled plugins"""
        enabled_plugin = self._create_sample_plugin("enabled_plugin")
        enabled_plugin.enabled = True
        self.repo.create_plugin(enabled_plugin)

        disabled_plugin = self._create_sample_plugin("disabled_plugin")
        disabled_plugin.enabled = False
        self.repo.create_plugin(disabled_plugin)

        hook1 = PluginHook(
            id=0,
            plugin_id=enabled_plugin.id,
            hook_name="test_hook",
            hook_type="backend",
            handler_path="/path/to/enabled.py"
        )
        hook2 = PluginHook(
            id=0,
            plugin_id=disabled_plugin.id,
            hook_name="test_hook",
            hook_type="backend",
            handler_path="/path/to/disabled.py"
        )

        self.repo.register_hook(hook1)
        self.repo.register_hook(hook2)

        hooks = self.repo.get_hooks_by_name("test_hook")
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].plugin_id, enabled_plugin.id)

    def test_get_hooks_by_type(self):
        """Test getting hooks by type"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        backend_hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="backend_hook",
            hook_type="backend",
            handler_path="/path/to/backend.py"
        )
        frontend_hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="frontend_hook",
            hook_type="frontend",
            component_path="/path/to/Frontend.svelte"
        )

        self.repo.register_hook(backend_hook)
        self.repo.register_hook(frontend_hook)

        backend_hooks = self.repo.get_hooks_by_type("backend")
        self.assertEqual(len(backend_hooks), 1)
        self.assertEqual(backend_hooks[0].hook_type, "backend")

        frontend_hooks = self.repo.get_hooks_by_type("frontend")
        self.assertEqual(len(frontend_hooks), 1)
        self.assertEqual(frontend_hooks[0].hook_type, "frontend")

    def test_update_hook(self):
        """Test updating a hook"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="test_hook",
            hook_type="backend",
            handler_path="/path/to/hook.py"
        )
        registered = self.repo.register_hook(hook)

        # Update the hook
        hook.hook_name = "updated_hook"
        hook.sort_order = 10

        updated = self.repo.update_hook(registered.id, hook)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.hook_name, "updated_hook")
        self.assertEqual(updated.sort_order, 10)

    def test_unregister_hook(self):
        """Test unregistering a hook"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="test_hook",
            hook_type="backend",
            handler_path="/path/to/hook.py"
        )
        registered = self.repo.register_hook(hook)

        result = self.repo.unregister_hook(registered.id)
        self.assertTrue(result)

        hooks = self.repo.get_plugin_hooks(plugin.id)
        self.assertEqual(len(hooks), 0)

    def test_clear_plugin_hooks(self):
        """Test clearing all hooks for a plugin"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        # Register multiple hooks
        for i in range(5):
            hook = PluginHook(
                id=0,
                plugin_id=plugin.id,
                hook_name=f"hook_{i}",
                hook_type="backend",
                handler_path=f"/path/to/hook_{i}.py"
            )
            self.repo.register_hook(hook)

        count = self.repo.clear_plugin_hooks(plugin.id)
        self.assertEqual(count, 5)

        hooks = self.repo.get_plugin_hooks(plugin.id)
        self.assertEqual(len(hooks), 0)

    def test_delete_plugin_cascades_to_hooks(self):
        """Test that deleting a plugin cascades to its hooks"""
        plugin = self._create_sample_plugin()
        self.repo.create_plugin(plugin)

        hook = PluginHook(
            id=0,
            plugin_id=plugin.id,
            hook_name="test_hook",
            hook_type="backend",
            handler_path="/path/to/hook.py"
        )
        self.repo.register_hook(hook)

        # Delete the plugin
        self.repo.delete_plugin(plugin.id)

        # Verify hooks are deleted
        hooks = self.repo.get_plugin_hooks(plugin.id)
        self.assertEqual(len(hooks), 0)


if __name__ == '__main__':
    unittest.main()
