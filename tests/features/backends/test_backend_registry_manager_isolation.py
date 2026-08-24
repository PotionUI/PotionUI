"""
Each in-process backend must execute on its OWN GenerationManager.

Backends run in parallel (one generation per backend), and GenerationManager
carries the single `_cancelled` flag. Sharing one instance across backends
means cancelling a generation on backend A aborts whatever backend B is
running. These tests pin the factory wiring that prevents that.
"""

import unittest
from unittest.mock import Mock

from src.features.backends.backend_config import NativeBackendConfig, NATIVE_LOCAL_DRIVER
from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.native_backend import NativeBackend


def _registry_without_db(factory) -> BackendRegistry:
    """
    Build a BackendRegistry without running __init__ (which reads the backend
    config table). Only the pieces `_create_backend_instance` touches are set.
    """
    registry = object.__new__(BackendRegistry)
    registry.generation_manager_factory = factory
    registry.plugin_registry = None
    registry._registered_backend_types = {NATIVE_LOCAL_DRIVER: NativeBackend}
    registry._registered_config_types = {NATIVE_LOCAL_DRIVER: NativeBackendConfig}
    return registry


class TestBackendGetsItsOwnGenerationManager(unittest.TestCase):
    def test_each_backend_instance_gets_a_distinct_manager(self):
        registry = _registry_without_db(lambda: Mock(name="generation_manager"))

        a = registry._create_backend_instance(NativeBackendConfig(id="a", name="A"))
        b = registry._create_backend_instance(NativeBackendConfig(id="b", name="B"))

        self.assertIsNotNone(a.generation_manager)
        self.assertIsNotNone(b.generation_manager)
        self.assertIsNot(
            a.generation_manager,
            b.generation_manager,
            "backends share a GenerationManager: cancelling one would abort the other",
        )

    def test_the_factory_is_called_once_per_backend(self):
        factory = Mock(side_effect=lambda: Mock())
        registry = _registry_without_db(factory)

        registry._create_backend_instance(NativeBackendConfig(id="a", name="A"))
        registry._create_backend_instance(NativeBackendConfig(id="b", name="B"))

        self.assertEqual(factory.call_count, 2)


if __name__ == "__main__":
    unittest.main()
