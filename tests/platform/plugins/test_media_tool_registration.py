import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from src.platform.media_tools import MediaToolRegistry, validate_params
from src.platform.media_tools.ops import CORE_MODULES, register_core_tools
from src.platform.plugins.registry import PluginRegistry, PluginState

LOOP_MODULE = """
from src.plugin_api.media_tools import FilterFragment, MediaTool


class LoopTool(MediaTool):
    def video_filter(self, media, params):
        return FilterFragment(vf=["loop=%d:32767" % params["repeats"]])
"""

NOT_A_TOOL_MODULE = """
class Impostor:
    pass
"""


class TestPluginMediaToolRegistration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.media_tools = MediaToolRegistry()
        register_core_tools(self.media_tools)
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            media_tool_registry=self.media_tools,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, media_tools, modules=None) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        manifest = {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "type": "full-stack",
            "media_tools": media_tools,
        }
        (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest))
        for relative, source in (modules or {}).items():
            target = plugin_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        return plugin_dir

    def _loop_entry(self, plugin_id="boomerang", **overrides):
        entry = {
            "id": f"{plugin_id}.loop",
            "label": "Boomerang",
            "icon": "repeat",
            "accepts": ["video"],
            "produces": "same",
            "cardinality": "one_to_one",
            "params": [{"name": "repeats", "type": "integer", "min": 1, "max": 8, "default": 2}],
            "python": "tools/loop.py:LoopTool",
            "scopes": ["library", "history"],
        }
        entry.update(overrides)
        return entry

    def test_enable_registers_python_tool_with_manifest_fields(self):
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})

        self.assertTrue(self.registry.enable_plugin("boomerang"))

        spec = self.media_tools.get("boomerang.loop")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.source, "boomerang")
        self.assertEqual(spec.label, "Boomerang")
        self.assertEqual(spec.accepts, ("video",))
        self.assertEqual(spec.scopes, ("library", "history"))
        self.assertIsNotNone(spec.impl)
        self.assertIsNone(spec.component)
        self.assertEqual(validate_params(spec, {}), {"repeats": 2})
        self.assertEqual(spec.impl.video_filter(None, {"repeats": 3}).vf, ["loop=3:32767"])

    def test_component_only_tool_registers_without_impl_and_serves_plugin_component_ref(self):
        entry = {
            "id": "exporter.zip",
            "label": "Export",
            "accepts": ["image"],
            "selection": {"min": 1},
            "scopes": ["history"],
            "component": "ExportTool.js",
        }
        self._create_plugin("exporter", [entry])

        self.assertTrue(self.registry.enable_plugin("exporter"))

        spec = self.media_tools.get("exporter.zip")
        self.assertIsNone(spec.impl)
        self.assertEqual(spec.component, "plugin:exporter:ExportTool.js")
        self.assertEqual(spec.selection, {"min": 1})
        self.assertEqual(spec.scopes, ("history",))
        self.assertEqual(spec.to_dict()["component"], "plugin:exporter:ExportTool.js")

    def test_entry_without_scopes_registers_for_every_scope(self):
        manifest = SimpleNamespace(
            id="loose",
            media_tools=[{"id": "loose.view", "label": "View", "accepts": ["image"], "component": "View.js"}],
        )

        self.assertIsNone(self.registry._register_plugin_media_tools(manifest))

        self.assertEqual(self.media_tools.get("loose.view").scopes, ("library", "history", "field"))

    def test_param_required_semantics_survive_the_manifest_round_trip(self):
        entry = self._loop_entry(
            params=[
                {"name": "end", "type": "float", "label": "End"},
                {"name": "width", "type": "integer", "default": None},
            ]
        )
        self._create_plugin("boomerang", [entry], {"tools/loop.py": LOOP_MODULE})
        self.registry.enable_plugin("boomerang")

        spec = self.media_tools.get("boomerang.loop")

        self.assertNotIn("default", spec.params[0])
        self.assertIn("default", spec.params[1])
        self.assertEqual(validate_params(spec, {"end": 3}), {"end": 3.0, "width": None})

    def test_disable_unregisters_the_plugins_tools_only(self):
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})
        self.registry.enable_plugin("boomerang")
        core_ids = {spec.id for spec in self.media_tools.list() if spec.source == "core"}

        self.assertTrue(self.registry.disable_plugin("boomerang"))

        self.assertIsNone(self.media_tools.get("boomerang.loop"))
        self.assertEqual({spec.id for spec in self.media_tools.list()}, core_ids)
        self.assertEqual(len(core_ids), len(CORE_MODULES))

    def test_reenable_registers_again(self):
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})
        self.registry.enable_plugin("boomerang")
        self.registry.disable_plugin("boomerang")

        self.assertTrue(self.registry.enable_plugin("boomerang"))

        self.assertIsNotNone(self.media_tools.get("boomerang.loop"))

    def test_catalog_listing_follows_plugin_state(self):
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})

        before = [spec.id for spec in self.media_tools.list(kind="video", scope="history")]
        self.registry.enable_plugin("boomerang")
        during = [spec.id for spec in self.media_tools.list(kind="video", scope="history")]
        during_field = [spec.id for spec in self.media_tools.list(kind="video", scope="field")]
        self.registry.disable_plugin("boomerang")
        after = [spec.id for spec in self.media_tools.list(kind="video", scope="history")]

        self.assertNotIn("boomerang.loop", before)
        self.assertIn("boomerang.loop", during)
        self.assertNotIn("boomerang.loop", during_field)
        self.assertEqual(before, after)

    def test_missing_python_file_fails_enable_and_leaves_nothing_registered(self):
        self._create_plugin("boomerang", [self._loop_entry()])

        self.assertFalse(self.registry.enable_plugin("boomerang"))

        self.assertIsNone(self.media_tools.get("boomerang.loop"))
        self.assertEqual(self.registry.get_plugin_state("boomerang"), PluginState.ERROR)

    def test_class_that_is_not_a_media_tool_fails_enable(self):
        self._create_plugin(
            "boomerang",
            [self._loop_entry(python="tools/loop.py:Impostor")],
            {"tools/loop.py": NOT_A_TOOL_MODULE},
        )

        self.assertFalse(self.registry.enable_plugin("boomerang"))

        self.assertIsNone(self.media_tools.get("boomerang.loop"))
        self.assertIn("MediaTool", self.registry.get_plugin_error("boomerang"))

    def test_second_tool_failing_rolls_back_the_first(self):
        good = self._loop_entry()
        bad = self._loop_entry(id="boomerang.other", python="tools/loop.py:Missing")
        self._create_plugin("boomerang", [good, bad], {"tools/loop.py": LOOP_MODULE})

        self.assertFalse(self.registry.enable_plugin("boomerang"))

        self.assertIsNone(self.media_tools.get("boomerang.loop"))
        self.assertIsNone(self.media_tools.get("boomerang.other"))

    def test_invalid_manifest_never_registers(self):
        self._create_plugin("boomerang", [self._loop_entry(id="someone-else.loop")], {"tools/loop.py": LOOP_MODULE})

        self.assertFalse(self.registry.enable_plugin("boomerang"))

        self.assertIsNone(self.media_tools.get("someone-else.loop"))
        self.assertIsNone(self.media_tools.get("boomerang.loop"))
        self.assertEqual(self.registry.get_plugin_state("boomerang"), PluginState.ERROR)

    def test_two_plugins_cannot_share_a_tool_id(self):
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})
        self.registry.enable_plugin("boomerang")
        spec = self.media_tools.get("boomerang.loop")
        clash = type(spec)(**{**spec.__dict__, "source": "other"})

        with self.assertRaises(ValueError):
            self.media_tools.register(clash)

    def test_plugin_without_media_registry_reports_a_clear_error(self):
        registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))
        self._create_plugin("boomerang", [self._loop_entry()], {"tools/loop.py": LOOP_MODULE})

        self.assertFalse(registry.enable_plugin("boomerang"))

        self.assertIn("media tool registry", registry.get_plugin_error("boomerang"))


if __name__ == "__main__":
    unittest.main()
