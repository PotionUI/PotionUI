import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe
from src.platform.plugins import PluginManifest, PluginRegistry


class CorePipe(BasePipe):
    name = "gen"


class PluginPipe(BasePipe):
    name = "gen"


class TestPluginPipeCollisions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.core = os.path.join(self.temp_dir, "core")
        self.custom = os.path.join(self.temp_dir, "custom")
        (Path(self.core) / "generator" / "gen").mkdir(parents=True)
        (Path(self.core) / "generator" / "gen" / "main.py").write_text("# core pipe")
        os.makedirs(self.custom)
        self.registry = Mock(spec=PluginRegistry)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _manifest(self, plugin_id, register_as):
        plugin_dir = Path(self.temp_dir) / plugin_id
        pipe_dir = plugin_dir / "pipes" / "p"
        pipe_dir.mkdir(parents=True)
        (pipe_dir / "main.py").write_text("# plugin pipe")
        manifest = Mock(spec=PluginManifest)
        manifest.id = plugin_id
        manifest.plugin_dir = plugin_dir
        manifest.pipes = [{"path": "pipes/p", "register_as": register_as}]
        return manifest

    def _loader(self, name, cls):
        def load(path, module_name):
            return CorePipe if "core" in path else cls
        return load

    def test_lazy_scan_keeps_the_core_pipe_and_warns(self):
        self.registry.get_enabled_plugins.return_value = [self._manifest("colliding", "generator/gen")]
        catalog = PipeCatalog(self.core, self.custom, plugin_registry=self.registry)
        with self.assertLogs("", level=logging.WARNING) as logs:
            catalog._ensure_light_discovered()
        self.assertEqual(catalog._locations["generator/gen"].source, "core")
        self.assertEqual(catalog.pipe_sources["generator/gen"], "core")
        self.assertTrue(any("colliding" in m and "generator/gen" in m and "collides" in m for m in logs.output))

    def test_eager_discovery_keeps_the_core_pipe_and_warns(self):
        self.registry.get_enabled_plugins.return_value = [self._manifest("colliding", "generator/gen")]
        catalog = PipeCatalog(self.core, self.custom, plugin_registry=self.registry)
        with patch.object(catalog, "_load_pipe_module", side_effect=self._loader("gen", PluginPipe)):
            with self.assertLogs("", level=logging.WARNING) as logs:
                catalog.discover_pipes()
        self.assertIs(catalog.pipes["generator/gen"], CorePipe)
        self.assertEqual(catalog.pipe_sources["generator/gen"], "core")
        self.assertTrue(any("colliding" in m and "collides" in m for m in logs.output))

    def test_a_non_colliding_plugin_pipe_still_loads(self):
        self.registry.get_enabled_plugins.return_value = [self._manifest("fine", "generator/other")]
        catalog = PipeCatalog(self.core, self.custom, plugin_registry=self.registry)
        with patch.object(catalog, "_load_pipe_module", side_effect=self._loader("other", PluginPipe)):
            catalog.discover_pipes()
        self.assertIs(catalog.pipes["generator/other"], PluginPipe)
        self.assertEqual(catalog.pipe_sources["generator/other"], "fine")
