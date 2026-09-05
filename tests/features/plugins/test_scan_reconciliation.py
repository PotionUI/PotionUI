"""`scan_plugins()` against a real registry, repository and plugin directories.

The admin "Scan for Plugins" action runs against a live process, so it has to
leave the plugins that did not move alone and bring the database in line with
the ones that did.
"""

import shutil
import tempfile
from pathlib import Path

import yaml

from src.features.plugins import operations
from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry, PluginState
from tests.fixtures.persistence_base import PersistenceTestBase


HANDLER_SOURCE = """
def handle(context):
    context.set("handled", True)
    return context
"""

HOOK = "generation.before_start"


class ScanReconciliationTests(PersistenceTestBase):
    def setUp(self):
        super().setUp()

        self.plugins_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.plugins_dir / "marketplace"
        self.local_dir = self.plugins_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.registry = PluginRegistry(
            marketplace_dir=str(self.marketplace_dir),
            local_dir=str(self.local_dir),
        )
        self.repo = PluginRepository()

    def tearDown(self):
        shutil.rmtree(self.plugins_dir)
        super().tearDown()

    def _write_plugin(self, plugin_id: str, version: str = "1.0.0") -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir(exist_ok=True)
        (plugin_dir / "handlers.py").write_text(HANDLER_SOURCE)
        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump({
                "id": plugin_id,
                "name": plugin_id,
                "version": version,
                "description": "Test plugin",
                "author": "Test Author",
                "type": "full-stack",
                "hooks": {"backend": [{"hook": HOOK, "handler": "handlers.handle"}]},
                "pages": [{
                    "route": f"/{plugin_id}",
                    "component": "Page.svelte",
                    "label": plugin_id,
                }],
            }, f)
        return plugin_dir

    def _scan_and_enable(self, plugin_id: str) -> None:
        operations.scan_plugins(self.repo, self.registry)
        self.assertTrue(self.repo.enable_plugin(plugin_id))
        self.assertTrue(self.registry.enable_plugin(plugin_id))

    def test_scan_leaves_an_unchanged_enabled_plugin_running(self):
        self._write_plugin("steady")
        self._scan_and_enable("steady")

        result = operations.scan_plugins(self.repo, self.registry)

        self.assertEqual(result.added_plugin_ids, [])
        self.assertEqual(result.changed_plugin_ids, [])
        self.assertEqual(result.removed_plugin_ids, [])
        self.assertEqual(self.registry.get_plugin_state("steady"), PluginState.ENABLED)
        self.assertEqual(
            [pid for pid, _ in self.registry.hook_chain._handlers.get(HOOK, [])],
            ["steady"],
        )
        self.assertTrue(self.repo.get_plugin_by_id("steady").enabled)
        self.assertEqual(len(self.repo.get_plugin_hooks("steady")), 1)
        self.assertEqual(len(self.repo.get_plugin_pages("steady")), 1)

    def test_scan_reports_and_cleans_up_a_plugin_that_left_the_disk(self):
        self._write_plugin("vanishing")
        self._write_plugin("steady")
        self._scan_and_enable("vanishing")
        self._scan_and_enable("steady")

        shutil.rmtree(self.marketplace_dir / "vanishing")
        result = operations.scan_plugins(self.repo, self.registry)

        self.assertEqual(result.removed_plugin_ids, ["vanishing"])
        self.assertIsNone(self.registry.get_plugin("vanishing"))
        self.assertEqual(
            [pid for pid, _ in self.registry.hook_chain._handlers.get(HOOK, [])],
            ["steady"],
        )

        # The row survives - uninstalling is a separate, explicit action - but
        # it can no longer be enabled or contribute hooks and pages.
        row = self.repo.get_plugin_by_id("vanishing")
        self.assertIsNotNone(row)
        self.assertFalse(row.enabled)
        self.assertEqual(self.repo.get_plugin_hooks("vanishing"), [])
        self.assertEqual(self.repo.get_plugin_pages("vanishing"), [])

        self.assertTrue(self.repo.get_plugin_by_id("steady").enabled)
        self.assertEqual(len(self.repo.get_plugin_pages("steady")), 1)

    def test_scan_disables_a_plugin_whose_changed_manifest_no_longer_enables(self):
        self._write_plugin("breaks")
        self._scan_and_enable("breaks")

        with open(self.marketplace_dir / "breaks" / "manifest.yml", "w") as f:
            yaml.dump({
                "id": "breaks",
                "name": "breaks",
                "version": "2.0.0",
                "description": "Points at a handler that isn't there",
                "author": "Test Author",
                "type": "full-stack",
                "hooks": {"backend": [{"hook": HOOK, "handler": "handlers.missing"}]},
            }, f)

        result = operations.scan_plugins(self.repo, self.registry)

        self.assertEqual(result.changed_plugin_ids, ["breaks"])
        self.assertEqual(result.errored_plugin_ids, ["breaks"])
        self.assertEqual(self.registry.get_plugin_state("breaks"), PluginState.ERROR)
        self.assertEqual(self.registry.hook_chain._handlers.get(HOOK, []), [])
        self.assertFalse(self.repo.get_plugin_by_id("breaks").enabled)

    def test_scan_reports_a_newly_added_plugin_as_added_and_disabled(self):
        self._write_plugin("steady")
        self._scan_and_enable("steady")

        self._write_plugin("latecomer")
        result = operations.scan_plugins(self.repo, self.registry)

        self.assertEqual(result.added_plugin_ids, ["latecomer"])
        self.assertEqual({p.id for p in result.new_plugins}, {"latecomer"})
        self.assertEqual(self.registry.get_plugin_state("latecomer"), PluginState.DISCOVERED)
        self.assertFalse(self.repo.get_plugin_by_id("latecomer").enabled)
        self.assertEqual(self.registry.get_plugin_state("steady"), PluginState.ENABLED)
