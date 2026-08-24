"""Tests for PluginRouterManager (A6 dynamic router mount/unmount)."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.plugins.loader import PluginLoader
from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.router_manager import PluginRouterManager
from src.platform.plugins.field_types import FieldTypeRegistry


API_MODULE_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True, "plugin": "{plugin_id}"}}
"""

BAD_PREFIX_API_MODULE_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/wrong-prefix", tags=["Test"])


@router.get("/ping")
def ping():
    return {"ok": True}
"""


class TestPluginRouterManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.loader = PluginLoader(str(self.marketplace_dir), str(self.local_dir))
        self.app = FastAPI()
        self.router_manager = PluginRouterManager(loader=self.loader)
        self.router_manager.attach(self.app)
        self.client = TestClient(self.app)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, api_source: str) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        backend_dir = plugin_dir / "backend"
        backend_dir.mkdir()

        manifest_data = {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "type": "full-stack",
            "api": {"module": "backend/api.py"},
        }
        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump(manifest_data, f)

        (backend_dir / "api.py").write_text(api_source)
        return plugin_dir

    def _manifest_for(self, plugin_id: str):
        loader = PluginLoader(str(self.marketplace_dir), str(self.local_dir))
        manifests = loader.discover_plugins()
        return next(m for m in manifests if m.id == plugin_id)

    def test_mount_registers_routes_and_they_respond(self):
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        manifest = self._manifest_for("plugin-a")

        ok = self.router_manager.mount(manifest, loader=self.loader)
        self.assertTrue(ok)
        self.assertTrue(self.router_manager.is_mounted("plugin-a"))

        resp = self.client.get("/api/plugins/plugin-a/ping")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "plugin": "plugin-a"})

    def test_unmount_removes_only_that_plugins_routes(self):
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        self._create_plugin("plugin-b", API_MODULE_SOURCE.format(plugin_id="plugin-b"))

        manifest_a = self._manifest_for("plugin-a")
        manifest_b = self._manifest_for("plugin-b")

        self.router_manager.mount(manifest_a, loader=self.loader)
        self.router_manager.mount(manifest_b, loader=self.loader)

        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)
        self.assertEqual(self.client.get("/api/plugins/plugin-b/ping").status_code, 200)

        self.router_manager.unmount("plugin-a")

        self.assertFalse(self.router_manager.is_mounted("plugin-a"))
        self.assertTrue(self.router_manager.is_mounted("plugin-b"))

        # plugin-a's routes are gone (404), plugin-b's still respond
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 404)
        self.assertEqual(self.client.get("/api/plugins/plugin-b/ping").status_code, 200)

    def test_double_mount_is_idempotent(self):
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        manifest = self._manifest_for("plugin-a")

        route_count_before = len(self.app.router.routes)
        self.assertTrue(self.router_manager.mount(manifest, loader=self.loader))
        route_count_after_first = len(self.app.router.routes)
        self.assertTrue(self.router_manager.mount(manifest, loader=self.loader))
        route_count_after_second = len(self.app.router.routes)

        self.assertGreater(route_count_after_first, route_count_before)
        self.assertEqual(route_count_after_first, route_count_after_second)

    def test_unmount_never_mounted_plugin_is_noop(self):
        self.assertTrue(self.router_manager.unmount("never-mounted"))

    def test_remount_after_unmount_works(self):
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        manifest = self._manifest_for("plugin-a")

        self.router_manager.mount(manifest, loader=self.loader)
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)

        self.router_manager.unmount("plugin-a")
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 404)

        self.router_manager.mount(manifest, loader=self.loader)
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)

    def test_mount_warns_on_prefix_violation_but_does_not_fail(self):
        self._create_plugin("plugin-bad", BAD_PREFIX_API_MODULE_SOURCE)
        manifest = self._manifest_for("plugin-bad")

        ok = self.router_manager.mount(manifest, loader=self.loader)
        self.assertTrue(ok)
        # Mounted despite the prefix violation (warning, not a hard failure).
        resp = self.client.get("/api/wrong-prefix/ping")
        self.assertEqual(resp.status_code, 200)

    def test_mount_before_attach_defers(self):
        deferred_manager = PluginRouterManager(loader=self.loader)
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        manifest = self._manifest_for("plugin-a")

        ok = deferred_manager.mount(manifest, loader=self.loader)
        self.assertTrue(ok)
        self.assertFalse(deferred_manager.is_mounted("plugin-a"))

    def test_mount_all_enabled_only_mounts_plugins_with_api_section(self):
        self._create_plugin("plugin-a", API_MODULE_SOURCE.format(plugin_id="plugin-a"))
        manifest = self._manifest_for("plugin-a")

        # A manifest with no api section at all
        no_api_dir = self.marketplace_dir / "plugin-noapi"
        no_api_dir.mkdir()
        with open(no_api_dir / "manifest.yml", "w") as f:
            yaml.dump({
                "id": "plugin-noapi",
                "name": "plugin-noapi",
                "version": "1.0.0",
                "description": "no api",
                "author": "Test",
                "type": "full-stack",
            }, f)
        no_api_manifest = self._manifest_for("plugin-noapi")

        results = self.router_manager.mount_all_enabled(
            [manifest, no_api_manifest], loader=self.loader
        )
        self.assertEqual(results, {"plugin-a": True})
        self.assertTrue(self.router_manager.is_mounted("plugin-a"))


class TestPluginRegistryRouterIntegration(unittest.TestCase):
    """Verify PluginRegistry.enable_plugin/disable_plugin drive the router manager."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()

        self.app = FastAPI()
        self.router_manager = PluginRouterManager()
        self.router_manager.attach(self.app)
        self.client = TestClient(self.app)

        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            field_registry=FieldTypeRegistry(),
            router_manager=self.router_manager,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        backend_dir = plugin_dir / "backend"
        backend_dir.mkdir()

        manifest_data = {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "type": "full-stack",
            "api": {"module": "backend/api.py"},
        }
        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump(manifest_data, f)
        (backend_dir / "api.py").write_text(API_MODULE_SOURCE.format(plugin_id=plugin_id))
        return plugin_dir

    def test_enable_mounts_and_disable_unmounts(self):
        self._create_plugin("plugin-a")

        self.assertTrue(self.registry.enable_plugin("plugin-a"))
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)

        self.assertTrue(self.registry.disable_plugin("plugin-a"))
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 404)

    def test_disable_evicts_only_that_plugins_loader_cache(self):
        self._create_plugin("plugin-a")
        self._create_plugin("plugin-b")

        self.registry.enable_plugin("plugin-a")
        self.registry.enable_plugin("plugin-b")

        self.assertTrue(any(k.startswith("plugin-a.") for k in self.registry.loader._loaded_modules))
        self.assertTrue(any(k.startswith("plugin-b.") for k in self.registry.loader._loaded_modules))

        self.registry.disable_plugin("plugin-a")

        self.assertFalse(any(k.startswith("plugin-a.") for k in self.registry.loader._loaded_modules))
        self.assertTrue(any(k.startswith("plugin-b.") for k in self.registry.loader._loaded_modules))

    def test_reload_remounts_router(self):
        self._create_plugin("plugin-a")
        self.registry.enable_plugin("plugin-a")
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)

        self.assertTrue(self.registry.reload_plugin("plugin-a"))
        self.assertEqual(self.client.get("/api/plugins/plugin-a/ping").status_code, 200)


if __name__ == "__main__":
    unittest.main()
