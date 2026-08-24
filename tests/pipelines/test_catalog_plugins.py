"""
Test plugin integration with PipeCatalog.

This module tests the integration between the plugin system and the pipe registry,
ensuring that plugin-provided pipes are correctly discovered, loaded, and managed.
"""

import unittest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List

from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    PipeStatus,
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
    IOType,
)
from src.platform.plugins import PluginRegistry, PluginManifest, PluginState


class MockPluginPipe(BasePipe):
    """Mock pipe implementation from a plugin for testing"""

    name = 'mock_plugin_pipe'
    description = 'A mock pipe from a plugin'

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={'result': 'plugin_output'})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {'plugin_param': 'plugin_value'}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec(name='plugin_input', io_type=IOType.TEXT, required=True)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec(name='plugin_result', io_type=IOType.TEXT)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(name='plugin_param', param_type=str, default='plugin_value')
        ]


class TestPipeCatalogPluginIntegration(unittest.TestCase):
    """Test cases for PipeCatalog plugin integration"""

    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.core_pipes_path = os.path.join(self.temp_dir, 'core_pipes')
        self.custom_pipes_path = os.path.join(self.temp_dir, 'custom_pipes')

        os.makedirs(self.core_pipes_path, exist_ok=True)
        os.makedirs(self.custom_pipes_path, exist_ok=True)

        # Create mock plugin registry
        self.plugin_registry = Mock(spec=PluginRegistry)

    def tearDown(self):
        """Clean up test environment"""
        shutil.rmtree(self.temp_dir)

    def test_pipe_catalog_without_plugin_registry(self):
        """Test that PipeCatalog works without PluginRegistry"""
        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path)
        self.assertIsNone(registry.plugin_registry)

        # Should discover pipes without errors
        registry.discover_pipes()
        self.assertEqual(len(registry.pipes), 0)

    def test_pipe_catalog_with_plugin_registry(self):
        """Test that PipeCatalog accepts PluginRegistry"""
        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )
        self.assertEqual(registry.plugin_registry, self.plugin_registry)

    def test_discover_plugin_pipes_no_enabled_plugins(self):
        """Test discovering plugin pipes when no plugins are enabled"""
        self.plugin_registry.get_enabled_plugins.return_value = []

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )
        registry.discover_pipes()

        self.plugin_registry.get_enabled_plugins.assert_called_once()
        self.assertEqual(len(registry.pipes), 0)

    def test_discover_plugin_pipes_with_enabled_plugin(self):
        """Test discovering pipes from an enabled plugin"""
        # Create plugin directory structure
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        pipes_dir = plugin_dir / "backend" / "pipes" / "test_pipe"
        pipes_dir.mkdir(parents=True, exist_ok=True)

        # Create main.py for the plugin pipe
        main_file = pipes_dir / "main.py"
        with open(main_file, 'w') as f:
            f.write('# dummy plugin pipe file')

        # Create mock plugin manifest
        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        # Mock the _load_pipe_module to return our MockPluginPipe
        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry.discover_pipes()

        # Verify plugin pipe was discovered with correct prefix
        expected_name = f"plugin:test-plugin:{MockPluginPipe.name}"
        self.assertIn(expected_name, registry.pipes)
        self.assertEqual(registry.pipes[expected_name], MockPluginPipe)

    def test_plugin_pipe_source_tracking(self):
        """Test that plugin pipes are correctly tagged with their source"""
        # Create plugin directory structure
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        pipes_dir = plugin_dir / "backend" / "pipes" / "test_pipe"
        pipes_dir.mkdir(parents=True, exist_ok=True)

        # Create main.py
        main_file = pipes_dir / "main.py"
        with open(main_file, 'w') as f:
            f.write('# dummy plugin pipe file')

        # Create mock plugin manifest
        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry.discover_pipes()

        expected_name = f"plugin:test-plugin:{MockPluginPipe.name}"
        source = registry.get_pipe_source(expected_name)
        self.assertEqual(source, "test-plugin")

    def test_plugin_pipe_no_backend_pipes_directory(self):
        """Test handling of plugins without backend/pipes directory"""
        # Create plugin directory without pipes
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )
        registry.discover_pipes()

        # Should not raise exception, just skip the plugin
        self.assertEqual(len(registry.pipes), 0)

    def test_plugin_pipe_variant_discovery(self):
        """Test discovering pipe variants from plugins"""
        # Create plugin directory structure with variant
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        variant_dir = plugin_dir / "backend" / "pipes" / "test_pipe" / "variant1"
        variant_dir.mkdir(parents=True, exist_ok=True)

        # Create main.py for variant
        main_file = variant_dir / "main.py"
        with open(main_file, 'w') as f:
            f.write('# dummy variant file')

        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry.discover_pipes()

        # Verify variant was discovered
        expected_name = "plugin:test-plugin:test_pipe/variant1"
        self.assertIn(expected_name, registry.pipes)

    def test_multiple_plugins_with_pipes(self):
        """Test discovering pipes from multiple plugins"""
        plugins = []
        for i in range(3):
            plugin_dir = Path(self.temp_dir) / f"plugin_{i}"
            pipes_dir = plugin_dir / "backend" / "pipes" / f"pipe_{i}"
            pipes_dir.mkdir(parents=True, exist_ok=True)

            main_file = pipes_dir / "main.py"
            with open(main_file, 'w') as f:
                f.write('# dummy pipe file')

            mock_manifest = Mock(spec=PluginManifest)
            mock_manifest.id = f"plugin-{i}"
            mock_manifest.plugin_dir = plugin_dir
            plugins.append(mock_manifest)

        self.plugin_registry.get_enabled_plugins.return_value = plugins

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry.discover_pipes()

        # Verify all plugin pipes were discovered
        for i in range(3):
            expected_name = f"plugin:plugin-{i}:{MockPluginPipe.name}"
            self.assertIn(expected_name, registry.pipes)

    def test_disabled_plugin_pipes_not_available(self):
        """Test that pipes from disabled plugins are not discovered"""
        # First discovery with enabled plugin
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        pipes_dir = plugin_dir / "backend" / "pipes" / "test_pipe"
        pipes_dir.mkdir(parents=True, exist_ok=True)

        main_file = pipes_dir / "main.py"
        with open(main_file, 'w') as f:
            f.write('# dummy file')

        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        # Initially enabled
        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry.discover_pipes()

        expected_name = f"plugin:test-plugin:{MockPluginPipe.name}"
        self.assertIn(expected_name, registry.pipes)

        # Now disable the plugin and rediscover
        self.plugin_registry.get_enabled_plugins.return_value = []
        registry.discover_pipes()

        # Plugin pipe should no longer be available
        self.assertNotIn(expected_name, registry.pipes)

    def test_plugin_pipe_loading_error_handling(self):
        """Test error handling when loading plugin pipes fails"""
        plugin_dir = Path(self.temp_dir) / "test_plugin"
        pipes_dir = plugin_dir / "backend" / "pipes" / "test_pipe"
        pipes_dir.mkdir(parents=True, exist_ok=True)

        main_file = pipes_dir / "main.py"
        with open(main_file, 'w') as f:
            f.write('# dummy file')

        mock_manifest = Mock(spec=PluginManifest)
        mock_manifest.id = "test-plugin"
        mock_manifest.plugin_dir = plugin_dir

        self.plugin_registry.get_enabled_plugins.return_value = [mock_manifest]

        registry = PipeCatalog(
            self.core_pipes_path,
            self.custom_pipes_path,
            plugin_registry=self.plugin_registry
        )

        # Mock _load_pipe_module to return None (loading failed)
        with patch.object(registry, '_load_pipe_module', return_value=None):
            registry.discover_pipes()

        # Should not crash, just not add the pipe
        expected_name = f"plugin:test-plugin:{MockPluginPipe.name}"
        self.assertNotIn(expected_name, registry.pipes)


class TestRescanPluginPipes(unittest.TestCase):
    """get_pipe()'s LIGHT-SCAN tier only scans plugin pipes once
    (_light_discovered latches true) - the tier live generation requests
    actually use, unlike discover_pipes()'s eager tier (which already
    correctly re-scans on every call, per test_disabled_plugin_pipes_not_
    available above - that test is NOT evidence this tier was already fine).
    rescan_plugin_pipes() is the fix; these tests exercise get_pipe(), never
    discover_pipes(), so they can't accidentally pass against the wrong tier.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.core_pipes_path = os.path.join(self.temp_dir, 'core_pipes')
        self.custom_pipes_path = os.path.join(self.temp_dir, 'custom_pipes')
        os.makedirs(self.core_pipes_path, exist_ok=True)
        os.makedirs(self.custom_pipes_path, exist_ok=True)
        self.plugin_registry = Mock(spec=PluginRegistry)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _manifest_with_register_as(self, plugin_id: str, pipe_dir_name: str, register_as: str):
        plugin_dir = Path(self.temp_dir) / plugin_id
        pipe_path = plugin_dir / "pipes" / pipe_dir_name
        pipe_path.mkdir(parents=True, exist_ok=True)
        (pipe_path / "main.py").write_text("# dummy pipe file")

        manifest = Mock(spec=PluginManifest)
        manifest.id = plugin_id
        manifest.plugin_dir = plugin_dir
        manifest.pipes = [{"path": f"pipes/{pipe_dir_name}", "register_as": register_as}]
        return manifest

    def test_noop_before_any_scan_has_happened(self):
        self.plugin_registry.get_enabled_plugins.return_value = []
        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path, plugin_registry=self.plugin_registry)
        registry.rescan_plugin_pipes()  # must not raise, must not trigger a scan
        self.assertFalse(registry._light_discovered)
        self.assertFalse(registry._discovered)

    def test_newly_enabled_plugin_pipe_becomes_resolvable_after_rescan(self):
        manifest = self._manifest_with_register_as("plug-a", "gen", "generator/plug_a")
        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path, plugin_registry=self.plugin_registry)

        # Boot-time scan happened BEFORE the plugin was enabled.
        self.plugin_registry.get_enabled_plugins.return_value = []
        self.assertIsNone(registry.get_pipe("generator/plug_a"))

        # Plugin gets enabled live.
        self.plugin_registry.get_enabled_plugins.return_value = [manifest]
        registry.rescan_plugin_pipes()

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            self.assertIs(registry.get_pipe("generator/plug_a"), MockPluginPipe)

    def test_disabled_plugin_pipe_stops_resolving_after_rescan(self):
        manifest = self._manifest_with_register_as("plug-b", "gen", "generator/plug_b")
        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path, plugin_registry=self.plugin_registry)

        self.plugin_registry.get_enabled_plugins.return_value = [manifest]
        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            self.assertIs(registry.get_pipe("generator/plug_b"), MockPluginPipe)

        # Plugin gets disabled live.
        self.plugin_registry.get_enabled_plugins.return_value = []
        registry.rescan_plugin_pipes()

        self.assertIsNone(registry.get_pipe("generator/plug_b"))
        self.assertNotIn("generator/plug_b", registry.pipes)
        self.assertNotIn("generator/plug_b", registry.pipe_sources)

    def test_disabled_plugin_pipe_from_auto_discovery_stops_resolving(self):
        """The eager auto-discover/no-register_as branch also runs INSIDE the
        light-scan tier (see _light_scan_plugin_pipes) - rescan must clean up
        entries it registered too, not just register_as-aliased locations."""
        plugin_dir = Path(self.temp_dir) / "plug-c"
        pipes_dir = plugin_dir / "backend" / "pipes" / "test_pipe"
        pipes_dir.mkdir(parents=True, exist_ok=True)
        (pipes_dir / "main.py").write_text("# dummy pipe file")
        manifest = Mock(spec=PluginManifest)
        manifest.id = "plug-c"
        manifest.plugin_dir = plugin_dir

        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path, plugin_registry=self.plugin_registry)
        expected_name = f"plugin:plug-c:{MockPluginPipe.name}"

        self.plugin_registry.get_enabled_plugins.return_value = [manifest]
        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            registry._ensure_light_discovered()  # triggers the eager auto-discover branch
        self.assertIn(expected_name, registry.pipes)

        self.plugin_registry.get_enabled_plugins.return_value = []
        registry.rescan_plugin_pipes()

        self.assertNotIn(expected_name, registry.pipes)
        self.assertIsNone(registry.get_pipe(expected_name))

    def test_core_and_custom_pipes_untouched_by_rescan(self):
        core_pipe_dir = os.path.join(self.core_pipes_path, 'core_pipe')
        os.makedirs(core_pipe_dir, exist_ok=True)
        with open(os.path.join(core_pipe_dir, 'main.py'), 'w') as f:
            f.write('# core pipe')

        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path, plugin_registry=self.plugin_registry)
        self.plugin_registry.get_enabled_plugins.return_value = []

        with patch.object(registry, '_load_pipe_module', return_value=MockPluginPipe):
            with patch('src.pipelines.catalog.requirements_satisfied', return_value=True):
                self.assertIs(registry.get_pipe("core_pipe"), MockPluginPipe)
        registry.rescan_plugin_pipes()
        self.assertIn("core_pipe", registry.pipes)
        self.assertIn("core_pipe", registry._locations)

    def test_rescan_is_a_noop_without_a_plugin_registry(self):
        registry = PipeCatalog(self.core_pipes_path, self.custom_pipes_path)  # no plugin_registry
        registry._ensure_light_discovered()
        registry.rescan_plugin_pipes()  # must not raise


if __name__ == '__main__':
    unittest.main()
