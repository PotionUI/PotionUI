"""Tests for the plugin-based backend system."""

import unittest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from src.platform.plugins.hooks import HookContext, HookChain, hooks_registry
from src.features.backends.hooks import BACKEND_HOOKS
from src.features.backends.backend_config import (
    NATIVE_ENGINE,
    BackendConfigStore,
    NativeBackendConfig,
    BaseBackendConfig
)


class TestBackendRegisterHook(unittest.TestCase):
    """Test the backend.register hook declaration"""

    def test_backend_register_hook_exists(self):
        """Test that BACKEND_HOOKS.register hook is defined"""
        self.assertEqual(BACKEND_HOOKS.register, "backend.register")

    def test_backend_register_is_backend_hook(self):
        """Test that backend.register is classified as a backend hook"""
        spec = hooks_registry.get(BACKEND_HOOKS.register)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.type, "backend")

    def test_hook_chain_can_execute_backend_register(self):
        """Test that HookChain can execute backend.register hook"""
        chain = HookChain()

        # Register a mock handler
        def mock_handler(context: HookContext) -> HookContext:
            backend_types = context.data.get('backend_types', {})
            backend_types['test_backend'] = MagicMock
            context.data['backend_types'] = backend_types
            return context

        chain.register("backend.register", "test-plugin", mock_handler)

        # Execute the hook
        context, results = chain.execute(
            "backend.register",
            initial_data={'backend_types': {}, 'config_types': {}}
        )

        # Verify the handler was called and modified the context
        self.assertIn('test_backend', context.data['backend_types'])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)


class TestBackendConfigStorePluginSupport(unittest.TestCase):
    """Test BackendConfigStore with plugin-registered types"""

    def test_config_manager_accepts_registered_types(self):
        """Test that BackendConfigStore accepts registered config types"""
        # Create a mock config class
        class MockBackendConfig(BaseBackendConfig):
            engine: str = "mock_backend"
            custom_field: str = "test"

        registered_types = {
            NATIVE_ENGINE: NativeBackendConfig,
            'mock_backend': MockBackendConfig,
        }

        manager = BackendConfigStore(
            backend_repository=MagicMock(),
            registered_config_types=registered_types
        )

        self.assertIn('mock_backend', manager._registered_config_types)

    def test_config_manager_get_supported_types(self):
        """Test get_supported_engines returns all registered engines"""
        registered_types = {
            NATIVE_ENGINE: NativeBackendConfig,
            'custom_type': MagicMock,
        }

        manager = BackendConfigStore(
            backend_repository=MagicMock(),
            registered_config_types=registered_types
        )

        supported_engines = manager.get_supported_engines()

        self.assertIn('native', supported_engines)
        self.assertIn('custom_type', supported_engines)

    def test_validate_backend_config_with_plugin_type(self):
        """Test validate_backend_config works with plugin-registered engines"""
        # Create a mock config class
        class CustomBackendConfig(BaseBackendConfig):
            engine: str = "custom"
            extra_field: str = "default"

        registered_types = {
            NATIVE_ENGINE: NativeBackendConfig,
            'custom': CustomBackendConfig,
        }

        manager = BackendConfigStore(
            backend_repository=MagicMock(),
            registered_config_types=registered_types
        )

        # Validate a custom engine config
        config = manager.validate_backend_config({
            'id': 'test-id',
            'name': 'Test Backend',
            'engine': 'custom',
            'extra_field': 'custom_value'
        })

        self.assertIsInstance(config, CustomBackendConfig)
        self.assertEqual(config.extra_field, 'custom_value')


class TestPresetSupportedBackends(unittest.TestCase):
    """Test PresetTemplate engine field"""

    def test_preset_template_has_engine(self):
        """Test that PresetTemplate has a scalar engine field"""
        from src.features.presets.templates import PresetTemplate, GenerationMode

        preset = PresetTemplate(
            id="test-preset",
            name="Test Preset",
            version="1.0",
            path="presets/test/test/1.0/test",
            modes={GenerationMode.TXT2IMG: []},
            engine="comfyui"
        )

        self.assertEqual(preset.engine, "comfyui")

    def test_preset_template_defaults_to_native(self):
        """Test that PresetTemplate defaults to 'native' if not specified"""
        from src.features.presets.templates import PresetTemplate, GenerationMode

        preset = PresetTemplate(
            id="test-preset",
            name="Test Preset",
            version="1.0",
            path="presets/test/test/1.0/test",
            modes={GenerationMode.TXT2IMG: []},
        )

        self.assertEqual(preset.engine, "native")

    def test_preset_template_copy_includes_engine(self):
        """Test that copy() preserves engine"""
        from src.features.presets.templates import PresetTemplate, GenerationMode

        preset = PresetTemplate(
            id="test-preset",
            name="Test Preset",
            version="1.0",
            path="presets/test/test/1.0/test",
            modes={GenerationMode.TXT2IMG: []},
            engine="comfyui"
        )

        copy = preset.copy()

        self.assertEqual(copy.engine, "comfyui")

    def test_preset_template_to_dict_includes_engine(self):
        """Test that to_dict() includes engine"""
        from src.features.presets.templates import PresetTemplate, ModeTemplate

        preset = PresetTemplate(
            id="test-preset",
            name="Test Preset",
            version="1.0",
            path="presets/test/test/1.0/test",
            modes={"txt2img": ModeTemplate(forms=[], pipes=[])},
            engine="comfyui"
        )

        dict_repr = preset.to_dict()

        self.assertEqual(dict_repr["engine"], "comfyui")


class TestEngineSelfDescription(unittest.TestCase):
    """
    Engines describe their own configuration fields, so no frontend or core code
    needs to know what settings a plugin-provided engine requires.
    """

    def test_native_engine_declares_its_gpu_fields(self):
        """device/dtype/gpu_max_vram are native-engine config, not global settings."""
        from src.features.backends.backend_config import NativeBackendConfig

        names = [f["name"] for f in NativeBackendConfig.engine_fields()]
        self.assertEqual(names, ["device", "dtype", "gpu_max_vram"])
        self.assertTrue(NativeBackendConfig.engine_singleton)
        self.assertEqual(NativeBackendConfig.engine_label, "Native")

    def test_base_fields_are_never_reported_as_engine_fields(self):
        from src.features.backends.backend_config import BaseBackendConfig, BASE_CONFIG_FIELDS
        from pydantic import Field

        class Custom(BaseBackendConfig):
            engine: str = "custom"
            endpoint: str = Field(default="http://x", title="Endpoint")

        names = {f["name"] for f in Custom.engine_fields()}
        self.assertEqual(names, {"endpoint"})
        self.assertFalse(names & BASE_CONFIG_FIELDS)

    def test_field_types_and_secret_flag(self):
        from typing import Optional
        from src.features.backends.backend_config import BaseBackendConfig
        from pydantic import Field

        class Custom(BaseBackendConfig):
            engine: str = "custom"
            host: str = Field(default="h")
            port: int = Field(default=1)
            secure: bool = Field(default=False)
            token: Optional[str] = Field(default=None, json_schema_extra={"secret": True})

        by_name = {f["name"]: f for f in Custom.engine_fields()}
        self.assertEqual(by_name["host"]["type"], "string")
        self.assertEqual(by_name["port"]["type"], "number")
        self.assertEqual(by_name["secure"]["type"], "boolean")
        # Optional[str] must resolve to "string", not fall through to the default
        self.assertEqual(by_name["token"]["type"], "string")
        self.assertTrue(by_name["token"]["secret"])
        self.assertFalse(by_name["host"]["secret"])

    def test_engine_label_defaults_to_engine_name(self):
        from src.features.backends.backend_config import BaseBackendConfig

        class Custom(BaseBackendConfig):
            engine: str = "custom"

        self.assertIsNone(Custom.engine_label)
        self.assertFalse(Custom.engine_singleton)
