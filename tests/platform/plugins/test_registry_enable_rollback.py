"""Enabling a plugin is a transaction: either the whole contribution set lands,
or nothing of it does.

Each test injects a failure at one stage of `PluginRegistry.enable_plugin` and
asserts the plugin owns nothing afterwards - no hook handlers, no field types,
no model attributes, no extension-registry entries, no mounted routes - while a
healthy plugin enabled alongside it keeps everything.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.plugins.field_types import FieldTypeRegistry
from src.platform.plugins.prompt_importers import PromptImporterRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry
from src.platform.plugins.router_mounter import PluginRouterMounter


HANDLER_SOURCE = """
def first(context):
    context.set("first_ran", True)
    return context


def second(context):
    context.set("second_ran", True)
    return context
"""

API_MODULE_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True}}
"""

# A plugin whose HTTP router mounts fine but whose `ws_router` blows up while
# FastAPI walks its routes - the second of two includes in the same mount.
EXPLODING_WS_API_MODULE_SOURCE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True}}


class _ExplodingRouter:
    prefix = "/api/plugins/{plugin_id}"

    @property
    def routes(self):
        raise RuntimeError("ws_router is broken")


ws_router = _ExplodingRouter()
"""


class _ModelAttributesManagerStub:
    """Duck-types the model attribute definitions editor the registry expects."""

    def __init__(self):
        self.sources = set()
        self.error = None
        self.raises = None

    def upsert_from_plugin(self, plugin_id, entries):
        if self.raises is not None:
            raise self.raises
        if self.error is not None:
            return self.error
        self.sources.add(plugin_id)
        return None

    def remove_source(self, plugin_id):
        self.sources.discard(plugin_id)


class EnableRollbackTests(unittest.TestCase):
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
        self.model_attributes = _ModelAttributesManagerStub()
        self.prompt_importers = PromptImporterRegistry()
        self.requirement_checkers = RequirementCheckerRegistry()

        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            field_registry=self.field_registry,
            model_attributes_manager=self.model_attributes,
            router_mounter=self.router_mounter,
            prompt_importer_registry=self.prompt_importers,
            requirement_checker_registry=self.requirement_checkers,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    # -- fixtures ---------------------------------------------------------

    def _write_plugin(self, plugin_id: str, api_source: str = None, **sections) -> Path:
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        (plugin_dir / "handlers.py").write_text(HANDLER_SOURCE)

        manifest_data = {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "type": "full-stack",
        }
        manifest_data.update(sections)

        if api_source is not None:
            backend_dir = plugin_dir / "backend"
            backend_dir.mkdir()
            (backend_dir / "api.py").write_text(api_source.format(plugin_id=plugin_id))
            manifest_data["api"] = {"module": "backend/api.py"}

        with open(plugin_dir / "manifest.yml", "w") as f:
            yaml.dump(manifest_data, f)
        return plugin_dir

    def _two_hooks(self, second_handler: str = "handlers.second"):
        return {
            "backend": [
                {"hook": "generation.before_start", "handler": "handlers.first"},
                {"hook": "generation.after_complete", "handler": second_handler},
            ]
        }

    def _healthy_plugin(self, plugin_id: str = "good-plugin"):
        self._write_plugin(
            plugin_id,
            api_source=API_MODULE_SOURCE,
            hooks={"backend": [{"hook": "generation.before_start", "handler": "handlers.first"}]},
            field_types=[{"type": f"{plugin_id}_field", "component": "Field.js"}],
            model_metadata_fields=[
                {"key": f"{plugin_id}_key", "label": "L", "field_type": "text"}
            ],
        )
        self.assertTrue(self.registry.enable_plugin(plugin_id))
        return plugin_id

    # -- assertions -------------------------------------------------------

    def _hook_owners(self, hook_name: str):
        return [pid for pid, _ in self.registry.hook_chain._handlers.get(hook_name, [])]

    def assertNoResidue(self, plugin_id: str):
        for hook_name in self.registry.hook_chain._handlers:
            self.assertNotIn(plugin_id, self._hook_owners(hook_name))
        self.assertEqual(self.registry.get_plugins_for_hook("generation.before_start"), [])
        self.assertEqual(
            [d for d in self.field_registry.all() if d.source == plugin_id], []
        )
        self.assertNotIn(plugin_id, self.model_attributes.sources)
        self.assertEqual(
            [d for d in self.prompt_importers.all() if d.source == plugin_id], []
        )
        self.assertEqual(
            [r for r in self.requirement_checkers.all() if r.source == plugin_id], []
        )
        self.assertFalse(self.router_mounter.is_mounted(plugin_id))
        self.assertEqual(self.client.get(f"/api/plugins/{plugin_id}/ping").status_code, 404)

    def assertFailedWith(self, plugin_id: str, message_fragment: str):
        self.assertEqual(self.registry.get_plugin_state(plugin_id), PluginState.ERROR)
        self.assertIn(message_fragment, self.registry.get_plugin_error(plugin_id) or "")

    # -- failure injection ------------------------------------------------

    def test_second_hook_failure_rolls_back_the_first_hook(self):
        self._write_plugin("half-hooked", hooks=self._two_hooks("handlers.nonexistent"))

        self.assertFalse(self.registry.enable_plugin("half-hooked"))

        self.assertFailedWith("half-hooked", "Failed to load hook handler")
        self.assertNoResidue("half-hooked")

    def test_field_type_failure_rolls_back_hooks(self):
        self._write_plugin(
            "bad-field",
            hooks=self._two_hooks(),
            field_types=[{"type": "broken", "schema_class": "handlers.NoSuchClass"}],
        )

        self.assertFalse(self.registry.enable_plugin("bad-field"))

        self.assertFailedWith("bad-field", "Failed to load field schema class")
        self.assertNoResidue("bad-field")

    def test_model_attribute_failure_rolls_back_hooks_and_field_types(self):
        self.model_attributes.error = "key 'x' is owned by core"
        self._write_plugin(
            "bad-attrs",
            hooks=self._two_hooks(),
            field_types=[{"type": "attrs_field", "component": "Field.js"}],
            model_metadata_fields=[{"key": "x", "label": "X", "field_type": "text"}],
        )

        self.assertFalse(self.registry.enable_plugin("bad-attrs"))

        self.assertFailedWith("bad-attrs", "owned by core")
        self.assertNoResidue("bad-attrs")

    def test_prompt_importer_failure_rolls_back_earlier_stages(self):
        self._write_plugin(
            "bad-importer",
            hooks=self._two_hooks(),
            field_types=[{"type": "importer_field", "component": "Field.js"}],
            model_metadata_fields=[{"key": "y", "label": "Y", "field_type": "text"}],
            prompt_importers=[{
                "id": "broken",
                "label": "Broken",
                "component": "Import.js",
                "backend": "handlers:NoSuchImporter",
            }],
        )

        self.assertFalse(self.registry.enable_plugin("bad-importer"))

        self.assertFailedWith("bad-importer", "Failed to load prompt importer backend")
        self.assertNoResidue("bad-importer")

    def test_requirement_checker_failure_rolls_back_earlier_stages(self):
        self._write_plugin(
            "bad-checker",
            hooks=self._two_hooks(),
            field_types=[{"type": "checker_field", "component": "Field.js"}],
            prompt_importers=[{
                "id": "fine",
                "label": "Fine",
                "component": "Import.js",
                "backend": "handlers:NoSuchImporter",
            }],
            requirement_checkers=[{"type": "broken", "backend": "handlers:NoSuchChecker"}],
        )

        self.assertFalse(self.registry.enable_plugin("bad-checker"))
        self.assertNoResidue("bad-checker")

    def test_unexpected_exception_mid_enable_rolls_back(self):
        self.model_attributes.raises = RuntimeError("database is gone")
        self._write_plugin(
            "exploding",
            hooks=self._two_hooks(),
            field_types=[{"type": "exploding_field", "component": "Field.js"}],
            model_metadata_fields=[{"key": "z", "label": "Z", "field_type": "text"}],
        )

        self.assertFalse(self.registry.enable_plugin("exploding"))

        self.assertFailedWith("exploding", "database is gone")
        self.assertNoResidue("exploding")

    def test_router_mount_failure_leaves_no_routes_and_rolls_back_the_plugin(self):
        self._write_plugin(
            "bad-ws",
            api_source=EXPLODING_WS_API_MODULE_SOURCE,
            hooks=self._two_hooks(),
            field_types=[{"type": "ws_field", "component": "Field.js"}],
        )

        self.assertFalse(self.registry.enable_plugin("bad-ws"))

        self.assertFailedWith("bad-ws", "Failed to mount plugin API router")
        self.assertNoResidue("bad-ws")

    # -- the rest of the app is untouched ---------------------------------

    def test_failed_enable_leaves_a_healthy_plugin_untouched(self):
        # Both plugins must exist before the first enable triggers discovery.
        self._write_plugin("half-hooked", hooks=self._two_hooks("handlers.nonexistent"))
        good = self._healthy_plugin()

        self.assertFalse(self.registry.enable_plugin("half-hooked"))

        self.assertEqual(self.registry.get_plugin_state(good), PluginState.ENABLED)
        self.assertEqual(self._hook_owners("generation.before_start"), [good])
        self.assertEqual(self.field_registry.get(f"{good}_field").source, good)
        self.assertIn(good, self.model_attributes.sources)
        self.assertEqual(self.client.get(f"/api/plugins/{good}/ping").status_code, 200)

    def test_failed_mount_leaves_a_healthy_plugins_routes_mounted(self):
        self._write_plugin("bad-ws", api_source=EXPLODING_WS_API_MODULE_SOURCE)
        good = self._healthy_plugin()

        self.assertFalse(self.registry.enable_plugin("bad-ws"))

        self.assertEqual(self.client.get(f"/api/plugins/{good}/ping").status_code, 200)
        self.assertEqual(self.client.get("/api/plugins/bad-ws/ping").status_code, 404)

    # -- idempotency ------------------------------------------------------

    def test_retry_after_failure_enables_cleanly(self):
        self.model_attributes.error = "transient conflict"
        self._write_plugin(
            "retried",
            api_source=API_MODULE_SOURCE,
            hooks=self._two_hooks(),
            field_types=[{"type": "retried_field", "component": "Field.js"}],
            model_metadata_fields=[{"key": "r", "label": "R", "field_type": "text"}],
        )

        self.assertFalse(self.registry.enable_plugin("retried"))
        self.assertNoResidue("retried")

        self.model_attributes.error = None
        self.assertTrue(self.registry.enable_plugin("retried"))

        self.assertEqual(self.registry.get_plugin_state("retried"), PluginState.ENABLED)
        self.assertIsNone(self.registry.get_plugin_error("retried"))
        self.assertEqual(self._hook_owners("generation.before_start"), ["retried"])
        self.assertEqual(self._hook_owners("generation.after_complete"), ["retried"])
        self.assertEqual(self.field_registry.get("retried_field").source, "retried")
        self.assertEqual(self.client.get("/api/plugins/retried/ping").status_code, 200)

        self.assertTrue(self.registry.disable_plugin("retried"))
        self.assertNoResidue("retried")

    def test_fail_enabled_plugin_tears_down_a_live_plugin(self):
        good = self._healthy_plugin("live-plugin")

        self.assertTrue(
            self.registry.fail_enabled_plugin(good, "Failed to mount plugin API router")
        )

        self.assertFailedWith(good, "Failed to mount plugin API router")
        self.assertNoResidue(good)

    def test_fail_enabled_plugin_is_false_for_an_unknown_plugin(self):
        self.assertFalse(self.registry.fail_enabled_plugin("no-such-plugin", "boom"))

    def test_disable_after_failed_enable_is_a_noop(self):
        self._write_plugin("half-hooked", hooks=self._two_hooks("handlers.nonexistent"))
        self.assertFalse(self.registry.enable_plugin("half-hooked"))

        self.assertTrue(self.registry.disable_plugin("half-hooked"))
        self.assertEqual(self.registry.get_plugin_state("half-hooked"), PluginState.DISABLED)
        self.assertNoResidue("half-hooked")


if __name__ == "__main__":
    unittest.main()
