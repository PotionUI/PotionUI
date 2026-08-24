import unittest
from datetime import datetime
import json
from src.features.plugins.records import Plugin, PluginSetting, PluginHook


class TestPluginModel(unittest.TestCase):

    def setUp(self):
        """Set up test data"""
        self.test_plugin = Plugin(
            id="plugin_123",
            name="Test Plugin",
            version="1.0.0",
            type="full-stack",
            enabled=True,
            manifest_path="/plugins/test-plugin/manifest.json",
            description="A test plugin",
            author="Test Author",
            installed_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 30, 0)
        )

    def test_from_row(self):
        """Test creating Plugin from database row"""
        mock_row = {
            'id': 'plugin_456',
            'name': 'Another Plugin',
            'version': '3.1.0',
            'type': 'frontend-only',
            'enabled': 1,
            'manifest_path': '/plugins/another/manifest.json',
            'description': 'Another test plugin',
            'author': 'Another Author',
            'installed_at': '2024-02-01T10:00:00',
            'updated_at': '2024-02-01T11:00:00'
        }

        plugin = Plugin.from_row(mock_row)

        self.assertEqual(plugin.id, 'plugin_456')
        self.assertEqual(plugin.name, 'Another Plugin')
        self.assertEqual(plugin.version, '3.1.0')
        self.assertEqual(plugin.type, 'frontend-only')
        self.assertTrue(plugin.enabled)
        self.assertEqual(plugin.manifest_path, '/plugins/another/manifest.json')
        self.assertEqual(plugin.description, 'Another test plugin')
        self.assertEqual(plugin.author, 'Another Author')
        self.assertEqual(plugin.installed_at, datetime(2024, 2, 1, 10, 0, 0))
        self.assertEqual(plugin.updated_at, datetime(2024, 2, 1, 11, 0, 0))

    def test_from_row_with_null_dates(self):
        """Test creating Plugin from database row with null dates"""
        mock_row = {
            'id': 'plugin_789',
            'name': 'Plugin Without Dates',
            'version': '1.0.0',
            'type': 'full-stack',
            'enabled': 0,
            'manifest_path': '/plugins/test/manifest.json',
            'description': None,
            'author': None,
            'installed_at': None,
            'updated_at': None
        }

        plugin = Plugin.from_row(mock_row)

        self.assertEqual(plugin.id, 'plugin_789')
        self.assertFalse(plugin.enabled)
        self.assertIsNone(plugin.description)
        self.assertIsNone(plugin.author)
        self.assertIsNone(plugin.installed_at)
        self.assertIsNone(plugin.updated_at)

    def test_to_dict(self):
        """Test converting Plugin to dictionary"""
        result = self.test_plugin.to_dict()

        expected = {
            'id': 'plugin_123',
            'name': 'Test Plugin',
            'version': '1.0.0',
            'type': 'full-stack',
            'enabled': True,
            'manifest_path': '/plugins/test-plugin/manifest.json',
            'description': 'A test plugin',
            'author': 'Test Author',
            'installed_at': '2024-01-01T12:00:00',
            'updated_at': '2024-01-01T12:30:00'
        }

        self.assertEqual(result, expected)


class TestPluginSettingModel(unittest.TestCase):

    def test_from_row(self):
        """Test creating PluginSetting from database row"""
        mock_row = {
            'id': 42,
            'plugin_id': 'plugin_456',
            'setting_key': 'max_retries',
            'setting_value': '5',
            'user_id': None,
            'is_secret': 0
        }

        setting = PluginSetting.from_row(mock_row)

        self.assertEqual(setting.id, 42)
        self.assertEqual(setting.plugin_id, 'plugin_456')
        self.assertEqual(setting.setting_key, 'max_retries')
        self.assertEqual(setting.setting_value, '5')
        self.assertIsNone(setting.user_id)
        self.assertFalse(setting.is_secret)

    def test_get_value_string(self):
        """Test getting setting value as string"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value="test_value"
        )

        self.assertEqual(setting.get_value(str), "test_value")

    def test_get_value_int(self):
        """Test getting setting value as integer"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value="42"
        )

        self.assertEqual(setting.get_value(int), 42)

    def test_get_value_float(self):
        """Test getting setting value as float"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value="3.14"
        )

        self.assertEqual(setting.get_value(float), 3.14)

    def test_get_value_bool_true(self):
        """Test getting setting value as boolean (true variants)"""
        for value in ['true', 'True', '1', 'yes', 'YES', 'on', 'ON']:
            setting = PluginSetting(
                id=1,
                plugin_id="plugin_1",
                setting_key="key",
                setting_value=value
            )
            self.assertTrue(setting.get_value(bool), f"Failed for value: {value}")

    def test_get_value_bool_false(self):
        """Test getting setting value as boolean (false variants)"""
        for value in ['false', 'False', '0', 'no', 'NO', 'off', 'OFF']:
            setting = PluginSetting(
                id=1,
                plugin_id="plugin_1",
                setting_key="key",
                setting_value=value
            )
            self.assertFalse(setting.get_value(bool), f"Failed for value: {value}")

    def test_get_value_json_list(self):
        """Test getting setting value as JSON list"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value='["item1", "item2", "item3"]'
        )

        result = setting.get_value(list)
        self.assertEqual(result, ["item1", "item2", "item3"])

    def test_get_value_json_dict(self):
        """Test getting setting value as JSON dict"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value='{"name": "test", "count": 5}'
        )

        result = setting.get_value(dict)
        self.assertEqual(result, {"name": "test", "count": 5})

    def test_get_value_null(self):
        """Test getting null setting value"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value=None
        )

        self.assertIsNone(setting.get_value(str))
        self.assertIsNone(setting.get_value(int))

    def test_get_value_invalid_conversion(self):
        """Test getting setting value with invalid conversion"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value="not_a_number"
        )

        # Should return original string if conversion fails
        result = setting.get_value(int)
        self.assertEqual(result, "not_a_number")

    def test_to_dict_with_secret(self):
        """Test converting PluginSetting to dictionary (secret value)"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="api_key",
            setting_value="secret_value",
            user_id="user_123",
            is_secret=True
        )

        result = setting.to_dict()

        expected = {
            'id': 1,
            'plugin_id': 'plugin_1',
            'setting_key': 'api_key',
            'setting_value': '***',  # Secret values are masked
            'user_id': 'user_123',
            'is_secret': True
        }

        self.assertEqual(result, expected)

    def test_to_dict_without_secret(self):
        """Test converting PluginSetting to dictionary (non-secret value)"""
        setting = PluginSetting(
            id=2,
            plugin_id="plugin_1",
            setting_key="timeout",
            setting_value="30",
            user_id=None,
            is_secret=False
        )

        result = setting.to_dict()

        expected = {
            'id': 2,
            'plugin_id': 'plugin_1',
            'setting_key': 'timeout',
            'setting_value': '30',
            'user_id': None,
            'is_secret': False
        }

        self.assertEqual(result, expected)

    def test_serialize_value_string(self):
        """Test serializing string value"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key",
            setting_value="value"
        )

        result = setting.serialize_value("test_string")
        self.assertEqual(result, "test_string")

    def test_serialize_value_dict(self):
        """Test serializing dict value"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key"
        )

        result = setting.serialize_value({"name": "test", "value": 42})
        parsed = json.loads(result)
        self.assertEqual(parsed, {"name": "test", "value": 42})

    def test_serialize_value_list(self):
        """Test serializing list value"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key"
        )

        result = setting.serialize_value([1, 2, 3])
        parsed = json.loads(result)
        self.assertEqual(parsed, [1, 2, 3])

    def test_serialize_value_none(self):
        """Test serializing None value"""
        setting = PluginSetting(
            id=1,
            plugin_id="plugin_1",
            setting_key="key"
        )

        result = setting.serialize_value(None)
        self.assertIsNone(result)


class TestPluginHookModel(unittest.TestCase):

    def test_from_row(self):
        """Test creating PluginHook from database row"""
        mock_row = {
            'id': 42,
            'plugin_id': 'plugin_456',
            'hook_name': 'generation_panel_header',
            'hook_type': 'frontend',
            'handler_path': None,
            'component_path': '/components/CustomHeader.tsx',
            'position': 'top',
            'sort_order': 5
        }

        hook = PluginHook.from_row(mock_row)

        self.assertEqual(hook.id, 42)
        self.assertEqual(hook.plugin_id, 'plugin_456')
        self.assertEqual(hook.hook_name, 'generation_panel_header')
        self.assertEqual(hook.hook_type, 'frontend')
        self.assertIsNone(hook.handler_path)
        self.assertEqual(hook.component_path, '/components/CustomHeader.tsx')
        self.assertEqual(hook.position, 'top')
        self.assertEqual(hook.sort_order, 5)

    def test_from_row_with_null_sort_order(self):
        """Test creating PluginHook from database row with null sort_order"""
        mock_row = {
            'id': 1,
            'plugin_id': 'plugin_1',
            'hook_name': 'test_hook',
            'hook_type': 'backend',
            'handler_path': '/handler.py',
            'component_path': None,
            'position': None,
            'sort_order': None
        }

        hook = PluginHook.from_row(mock_row)

        self.assertEqual(hook.sort_order, 0)

    def test_to_dict(self):
        """Test converting PluginHook to dictionary"""
        hook = PluginHook(
            id=1,
            plugin_id="plugin_1",
            hook_name="before_save",
            hook_type="backend",
            handler_path="/handlers/before_save.py",
            component_path=None,
            position=None,
            sort_order=20
        )

        result = hook.to_dict()

        expected = {
            'id': 1,
            'plugin_id': 'plugin_1',
            'hook_name': 'before_save',
            'hook_type': 'backend',
            'handler_path': '/handlers/before_save.py',
            'component_path': None,
            'position': None,
            'sort_order': 20
        }

        self.assertEqual(result, expected)

    def test_backend_hook(self):
        """Test creating a backend hook"""
        hook = PluginHook(
            id=1,
            plugin_id="plugin_1",
            hook_name="after_generation",
            hook_type="backend",
            handler_path="/handlers/post_process.py"
        )

        self.assertEqual(hook.hook_type, "backend")
        self.assertIsNotNone(hook.handler_path)
        self.assertIsNone(hook.component_path)

    def test_frontend_hook(self):
        """Test creating a frontend hook"""
        hook = PluginHook(
            id=2,
            plugin_id="plugin_2",
            hook_name="generation_panel_footer",
            hook_type="frontend",
            component_path="/components/Footer.tsx",
            position="bottom"
        )

        self.assertEqual(hook.hook_type, "frontend")
        self.assertIsNone(hook.handler_path)
        self.assertIsNotNone(hook.component_path)
        self.assertEqual(hook.position, "bottom")


if __name__ == '__main__':
    unittest.main()
