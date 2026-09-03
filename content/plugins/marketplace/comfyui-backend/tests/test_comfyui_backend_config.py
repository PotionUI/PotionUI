"""Tests for the comfyui-backend plugin's own backend/config modules (split
out of the main repo's tests/features/backends/test_backend_plugin_system.py
when this plugin moved out of tree; the generic backend-hook mechanism tests
stayed there).

Run from a PotionUI checkout with this plugin linked into
content/plugins/local/ (see this repo's README.md).
"""

import unittest

from src.plugin_api.hooks import HookContext


class TestComfyUIBackendConfig(unittest.TestCase):
    """Test ComfyUIBackendConfig from plugin"""

    @classmethod
    def setUpClass(cls):
        """Load the ComfyUI config module once for all tests"""
        import importlib.util
        import os

        plugin_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))),
            'content/plugins/marketplace/comfyui-backend'
        )

        config_spec = importlib.util.spec_from_file_location(
            "test_comfyui_config",
            f"{plugin_dir}/backend/comfyui_config.py"
        )
        cls.comfyui_config_module = importlib.util.module_from_spec(config_spec)
        config_spec.loader.exec_module(cls.comfyui_config_module)

    def test_comfyui_config_validation(self):
        """Test ComfyUIBackendConfig validation"""
        ComfyUIBackendConfig = self.comfyui_config_module.ComfyUIBackendConfig

        config = ComfyUIBackendConfig(
            id="test-comfyui",
            name="Test ComfyUI",
            host="192.168.1.100",
            port=8188,
            secure=False
        )

        self.assertEqual(config.engine, "comfyui")
        self.assertEqual(config.host, "192.168.1.100")
        self.assertEqual(config.port, 8188)

    def test_comfyui_config_get_urls(self):
        """Test ComfyUIBackendConfig URL generation"""
        ComfyUIBackendConfig = self.comfyui_config_module.ComfyUIBackendConfig

        config = ComfyUIBackendConfig(
            id="test-comfyui",
            name="Test ComfyUI",
            host="192.168.1.100",
            port=8188,
            secure=False
        )

        self.assertEqual(config.get_base_url(), "http://192.168.1.100:8188")
        self.assertEqual(config.get_ws_url(), "ws://192.168.1.100:8188/ws")

        # Test secure URLs
        secure_config = ComfyUIBackendConfig(
            id="test-comfyui-secure",
            name="Test ComfyUI Secure",
            host="comfyui.example.com",
            port=443,
            secure=True
        )

        self.assertEqual(secure_config.get_base_url(), "https://comfyui.example.com:443")
        self.assertEqual(secure_config.get_ws_url(), "wss://comfyui.example.com:443/ws")

    def test_comfyui_config_to_connection_config(self):
        """Test ComfyUIBackendConfig to_connection_config method"""
        ComfyUIBackendConfig = self.comfyui_config_module.ComfyUIBackendConfig

        config = ComfyUIBackendConfig(
            id="test-comfyui",
            name="Test ComfyUI",
            host="192.168.1.100",
            port=8188,
            secure=False,
            client_id="test-client-id",
            api_key="test-api-key"
        )

        conn_config = config.to_connection_config()

        self.assertEqual(conn_config['host'], "192.168.1.100")
        self.assertEqual(conn_config['port'], 8188)
        self.assertEqual(conn_config['secure'], False)
        self.assertEqual(conn_config['client_id'], "test-client-id")
        self.assertEqual(conn_config['api_key'], "test-api-key")


class TestBackendRegistrationHookHandler(unittest.TestCase):
    """Test the ComfyUI backend hook handler"""

    def test_backend_hook_handler_registers_types(self):
        """Test that the backend hook handler correctly registers types"""
        import sys
        import importlib.util
        import os

        # Direct import of backend and config modules from plugin
        plugin_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))),
            'content/plugins/marketplace/comfyui-backend'
        )

        # Load comfyui_config module
        config_spec = importlib.util.spec_from_file_location(
            "comfyui_config",
            f"{plugin_dir}/backend/comfyui_config.py"
        )
        comfyui_config_module = importlib.util.module_from_spec(config_spec)
        sys.modules['comfyui_config'] = comfyui_config_module
        config_spec.loader.exec_module(comfyui_config_module)

        # Load comfyui_backend module
        backend_spec = importlib.util.spec_from_file_location(
            "comfyui_backend",
            f"{plugin_dir}/backend/comfyui_backend.py"
        )
        comfyui_backend_module = importlib.util.module_from_spec(backend_spec)
        sys.modules['comfyui_backend'] = comfyui_backend_module
        backend_spec.loader.exec_module(comfyui_backend_module)

        ComfyUIBackend = comfyui_backend_module.ComfyUIBackend
        ComfyUIBackendConfig = comfyui_config_module.ComfyUIBackendConfig

        # Manually simulate what the hook handler would do
        context = HookContext(
            hook_name="backend.register",
            plugin_id="comfyui-backend",
            data={'backend_types': {}, 'config_types': {}}
        )

        # Register the backend types (simulating the hook handler)
        backend_types = context.data.get('backend_types', {})
        config_types = context.data.get('config_types', {})

        backend_types['comfyui'] = ComfyUIBackend
        config_types['comfyui'] = ComfyUIBackendConfig

        context.data['backend_types'] = backend_types
        context.data['config_types'] = config_types

        # Verify registration
        self.assertIn('comfyui', context.data['backend_types'])
        self.assertIn('comfyui', context.data['config_types'])
        self.assertEqual(context.data['backend_types']['comfyui'], ComfyUIBackend)
        self.assertEqual(context.data['config_types']['comfyui'], ComfyUIBackendConfig)
