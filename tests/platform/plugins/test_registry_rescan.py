"""A rescan reconciles; it does not reset.

`PluginRegistry.discover_plugins()` runs while plugins are live, so a plugin
whose manifest has not changed must come out the other side still enabled and
still owning every handler, field type and mounted route it registered. Only a
manifest that changed, turned invalid or vanished may move, and it moves through
the same teardown/enable transaction an admin toggle uses.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.router_mounter import PluginRouterMounter


HANDLER_SOURCE = """
def first(context):
    context.set("who_ran", "first")
    return context


def second(context):
    context.set("who_ran", "second")
    return context
"""

API_MODULE_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True}}
"""

HOOK = "generation.before_start"


class RescanReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.app = FastAPI()
        self.client = TestClient(self.app)
        self.router_mounter = PluginRouterMounter()
        self.router_mounter.attach(self.app)
        self.field_registry = FieldTypeRegistry()

        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            field_registry=self.field_registry,
            router_mounter=self.router_mounter,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    # -- fixtures ---------------------------------------------------------

    def _write_plugin(
        self,
        plugin_id: str,
        handler: str = "handlers.first",
        version: str = "1.0.0",
        with_api: bool = True,
        manifest_override: dict = None,
    ) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir(exist_ok=True)
        (plugin_dir / "handlers.py").write_text(HANDLER_SOURCE)

        if manifest_override is not None:
            manifest_data = dict(manifest_override)
        else:
            manifest_data = {
                "id": plugin_id,
                "name": plugin_id,
                "version": version,
                "description": "Test plugin",
                "author": "Test Author",
                "type": "full-stack",
                "hooks": {"backend": [{"hook": HOOK, "handler": handler}]},
                "field_types": [{"type": f"{plugin_id}_field", "component": "Field.js"}],
            }
            if with_api:
                backend_dir = plugin_dir / "backend"
                backend_dir.mkdir(exist_ok=True)
                (backend_dir / "api.py").write_text(
                    API_MODULE_SOURCE.format(plugin_id=plugin_id)
                )
                manifest_data["api"] = {"module": "backend/api.py"}

        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump(manifest_data, f)
        return plugin_dir

    def _enabled_plugin(self, plugin_id: str = "steady", **kwargs) -> str:
        self._write_plugin(plugin_id, **kwargs)
        self.registry.discover_plugins()
        self.assertTrue(self.registry.enable_plugin(plugin_id))
        return plugin_id

    # -- assertions -------------------------------------------------------

    def _hook_owners(self, hook_name: str = HOOK):
        return [pid for pid, _ in self.registry.hook_chain._handlers.get(hook_name, [])]

    def _field_types(self, plugin_id: str):
        return [d for d in self.field_registry.all() if d.source == plugin_id]

    def _ran(self):
        context, _ = self.registry.execute_hook(HOOK)
        return context.get("who_ran")

    def assertFullyRegistered(self, plugin_id: str):
        self.assertEqual(self.registry.get_plugin_state(plugin_id), PluginState.ENABLED)
        self.assertEqual(self._hook_owners(), [plugin_id])
        self.assertEqual(len(self._field_types(plugin_id)), 1)
        self.assertTrue(self.router_mounter.is_mounted(plugin_id))
        self.assertEqual(self.client.get(f"/api/plugins/{plugin_id}/ping").status_code, 200)

    def assertNoResidue(self, plugin_id: str):
        for hook_name in self.registry.hook_chain._handlers:
            self.assertNotIn(plugin_id, self._hook_owners(hook_name))
        self.assertEqual(self._field_types(plugin_id), [])
        self.assertFalse(self.router_mounter.is_mounted(plugin_id))
        self.assertEqual(self.client.get(f"/api/plugins/{plugin_id}/ping").status_code, 404)

    # -- unchanged --------------------------------------------------------

    def test_unchanged_rescan_leaves_an_enabled_plugin_untouched(self):
        self._enabled_plugin("steady")

        changes = self.registry.discover_plugins()

        self.assertEqual(changes.added, [])
        self.assertEqual(changes.changed, [])
        self.assertEqual(changes.removed, [])
        self.assertFullyRegistered("steady")
        self.assertEqual(self._ran(), "first")

    def test_a_plugin_that_survived_a_rescan_can_still_be_disabled(self):
        self._enabled_plugin("steady")
        self.registry.discover_plugins()

        self.assertTrue(self.registry.disable_plugin("steady"))

        self.assertEqual(self.registry.get_plugin_state("steady"), PluginState.DISABLED)
        self.assertNoResidue("steady")

    def test_repeated_rescans_do_not_duplicate_registrations(self):
        self._enabled_plugin("steady")

        for _ in range(3):
            self.registry.discover_plugins()

        self.assertEqual(self._hook_owners(), ["steady"])
        self.assertEqual(len(self.registry.hook_chain._handlers[HOOK]), 1)
        self.assertEqual(len(self._field_types("steady")), 1)
        self.assertEqual(len(self.field_registry.all()), 1)
        self.assertEqual(
            len([r for r in self.app.routes if getattr(r, "path", "") == "/api/plugins/steady/ping"]),
            1,
        )
        self.assertFullyRegistered("steady")

    # -- changed ----------------------------------------------------------

    def test_changed_manifest_re_enables_with_the_new_handler(self):
        self._enabled_plugin("swapped")
        self.assertEqual(self._ran(), "first")

        self._write_plugin("swapped", handler="handlers.second", version="2.0.0")
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.changed, ["swapped"])
        self.assertEqual(changes.errored, [])
        self.assertFullyRegistered("swapped")
        self.assertEqual(len(self.registry.hook_chain._handlers[HOOK]), 1)
        self.assertEqual(self._ran(), "second")
        self.assertEqual(self.registry.get_plugin("swapped").version, "2.0.0")

    def test_changed_manifest_of_a_disabled_plugin_stays_disabled(self):
        self._enabled_plugin("dormant")
        self.assertTrue(self.registry.disable_plugin("dormant"))

        self._write_plugin("dormant", handler="handlers.second", version="2.0.0")
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.changed, ["dormant"])
        self.assertEqual(self.registry.get_plugin_state("dormant"), PluginState.DISABLED)
        self.assertEqual(self.registry.get_plugin("dormant").version, "2.0.0")
        self.assertNoResidue("dormant")

    def test_failed_re_enable_leaves_an_error_and_nothing_registered(self):
        self._enabled_plugin("broken-later")

        self._write_plugin("broken-later", handler="handlers.nonexistent")
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.changed, ["broken-later"])
        self.assertEqual(changes.errored, ["broken-later"])
        self.assertEqual(self.registry.get_plugin_state("broken-later"), PluginState.ERROR)
        self.assertIn("Failed to load hook handler", self.registry.get_plugin_error("broken-later"))
        self.assertNoResidue("broken-later")

    def test_invalid_replacement_of_a_valid_manifest_errors_without_residue(self):
        self._enabled_plugin("turns-bad")

        # Missing the required `author` field: the loader hands back an
        # error-tagged placeholder instead of a parsed manifest.
        self._write_plugin("turns-bad", manifest_override={
            "id": "turns-bad",
            "name": "turns-bad",
            "version": "1.0.0",
            "description": "Now missing its author",
            "type": "full-stack",
        })
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.changed, ["turns-bad"])
        self.assertEqual(changes.errored, ["turns-bad"])
        self.assertEqual(self.registry.get_plugin_state("turns-bad"), PluginState.ERROR)
        self.assertIn("author", self.registry.get_plugin_error("turns-bad"))
        self.assertNoResidue("turns-bad")

    def test_a_manifest_rewritten_mid_scan_cannot_look_unchanged_next_scan(self):
        """The fingerprint must describe the bytes that were actually parsed.

        If it were taken from a second read of the file, an edit landing between
        the loader's read and that one would stamp the new file's hash onto the
        old file's registrations - and the following scan, parsing the new file,
        would match that hash and keep the old handler live under the new
        manifest.
        """
        self._enabled_plugin("racy")
        loader = self.registry.loader
        original_load = loader._load_manifest
        swapped = []

        def load_then_rewrite(manifest_path, plugin_dir, source):
            manifest = original_load(manifest_path, plugin_dir, source)
            if manifest is not None and manifest.id == "racy" and not swapped:
                swapped.append(True)
                self._write_plugin("racy", handler="handlers.second", version="2.0.0")
            return manifest

        loader._load_manifest = load_then_rewrite
        try:
            self.registry.discover_plugins()
        finally:
            loader._load_manifest = original_load

        self.assertEqual(swapped, [True])
        # The scan parsed the old manifest, so the old handler is what is live.
        self.assertEqual(self._ran(), "first")

        changes = self.registry.discover_plugins()

        self.assertEqual(changes.changed, ["racy"])
        self.assertEqual(self._ran(), "second")
        self.assertEqual(len(self.registry.hook_chain._handlers[HOOK]), 1)
        self.assertEqual(self.registry.get_plugin("racy").version, "2.0.0")
        self.assertFullyRegistered("racy")

    # -- removed ----------------------------------------------------------

    def test_removed_manifest_drops_the_plugin_and_everything_it_owned(self):
        self._enabled_plugin("vanishing")

        shutil.rmtree(self.marketplace_dir / "vanishing")
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.removed, ["vanishing"])
        self.assertIsNone(self.registry.get_plugin("vanishing"))
        self.assertIsNone(self.registry.get_plugin_state("vanishing"))
        self.assertEqual(self.registry.get_plugins_for_hook(HOOK), [])
        self.assertNoResidue("vanishing")

    def test_removing_one_plugin_leaves_its_neighbour_running(self):
        self._enabled_plugin("vanishing")
        self._enabled_plugin("survivor")

        shutil.rmtree(self.marketplace_dir / "vanishing")
        self.registry.discover_plugins()

        self.assertEqual(self._hook_owners(), ["survivor"])
        self.assertFullyRegistered("survivor")

    # -- new --------------------------------------------------------------

    def test_a_plugin_added_between_scans_is_discovered_disabled(self):
        self._enabled_plugin("steady")

        self._write_plugin("latecomer", with_api=False)
        changes = self.registry.discover_plugins()

        self.assertEqual(changes.added, ["latecomer"])
        self.assertEqual(self.registry.get_plugin_state("latecomer"), PluginState.DISCOVERED)
        self.assertEqual(
            [m.id for m in self.registry.get_enabled_plugins()], ["steady"]
        )
        self.assertEqual(self._hook_owners(), ["steady"])


if __name__ == "__main__":
    unittest.main()
