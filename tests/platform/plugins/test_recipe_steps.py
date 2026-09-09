"""A plugin contributing a recipe step kind: the manifest section, the
registry bookkeeping, and the enable/disable round trip.

The registration path goes through the real `PluginRegistry`, so a manifest
that declares `recipe_steps:` really does make the kind runnable, and
disabling the plugin really does take it away again.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.recipe_steps import (
    DuplicateRecipeStepKindError,
    RecipeStepKindRegistration,
    RecipeStepKindRegistry,
)
from src.platform.plugins.registry import PluginRegistry, PluginState

STEP_SOURCE = """
class EnsureCollections:
    def execute(self, context):
        return None


class Broken:
    def __init__(self):
        raise RuntimeError("cannot build me")
"""


class RecipeStepKindRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = RecipeStepKindRegistry()

    def _registration(self, kind, source="core"):
        return RecipeStepKindRegistration(kind=kind, executor=object(), source=source)

    def test_register_get_all(self):
        self.registry.register(self._registration("collections.ensure", source="plugin-a"))

        self.assertEqual(self.registry.get("collections.ensure").source, "plugin-a")
        self.assertIsNone(self.registry.get("nope"))
        self.assertEqual([r.kind for r in self.registry.all()], ["collections.ensure"])

    def test_duplicate_kind_rejected(self):
        self.registry.register(self._registration("collections.ensure"))
        with self.assertRaises(DuplicateRecipeStepKindError):
            self.registry.register(self._registration("collections.ensure", source="plugin-a"))

    def test_unregister_source_leaves_other_sources(self):
        self.registry.register(self._registration("a", source="plugin-a"))
        self.registry.register(self._registration("b", source="plugin-a"))
        self.registry.register(self._registration("c", source="plugin-b"))

        self.registry.unregister_source("plugin-a")

        self.assertEqual([r.kind for r in self.registry.all()], ["c"])


class RecipeStepManifestTests(unittest.TestCase):
    def test_schema_accepts_recipe_steps(self):
        schema = PluginManifestSchema.model_validate({
            "id": "p", "name": "P", "version": "1.0.0", "description": "d",
            "author": "a", "type": "backend-only",
            "recipe_steps": [{"kind": "collections.ensure", "backend": "steps:EnsureCollections"}],
        })

        self.assertEqual(
            [(s.kind, s.backend) for s in schema.recipe_steps],
            [("collections.ensure", "steps:EnsureCollections")],
        )

    def test_schema_rejects_an_entry_missing_backend(self):
        with self.assertRaises(Exception):
            PluginManifestSchema.model_validate({
                "id": "p", "name": "P", "version": "1.0.0", "description": "d",
                "author": "a", "type": "backend-only",
                "recipe_steps": [{"kind": "collections.ensure"}],
            })


class RecipeStepPluginEnableTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.step_kinds = RecipeStepKindRegistry()
        self.registry = PluginRegistry(
            str(self.marketplace_dir),
            str(self.local_dir),
            recipe_step_kind_registry=self.step_kinds,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _write_plugin(self, plugin_id, recipe_steps):
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        (plugin_dir / "steps.py").write_text(STEP_SOURCE)
        (plugin_dir / "manifest.yml").write_text(yaml.dump({
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "type": "backend-only",
            "recipe_steps": recipe_steps,
        }))
        return plugin_dir

    def test_enabling_registers_the_kind_and_disabling_removes_it(self):
        self._write_plugin(
            "steps-plugin",
            [{"kind": "collections.ensure", "backend": "steps:EnsureCollections"}],
        )

        self.assertTrue(self.registry.enable_plugin("steps-plugin"))
        registration = self.step_kinds.get("collections.ensure")
        self.assertIsNotNone(registration)
        self.assertEqual(registration.source, "steps-plugin")
        self.assertEqual(type(registration.executor).__name__, "EnsureCollections")

        self.registry.disable_plugin("steps-plugin")

        self.assertIsNone(self.step_kinds.get("collections.ensure"))

    def test_a_backend_that_will_not_construct_fails_the_enable(self):
        self._write_plugin(
            "broken-plugin", [{"kind": "collections.ensure", "backend": "steps:Broken"}]
        )

        self.assertFalse(self.registry.enable_plugin("broken-plugin"))

        self.assertIsNone(self.step_kinds.get("collections.ensure"))
        self.assertEqual(
            self.registry.get_plugin_state("broken-plugin"), PluginState.ERROR
        )

    def test_a_kind_another_plugin_already_owns_fails_the_enable(self):
        self._write_plugin(
            "first-plugin",
            [{"kind": "collections.ensure", "backend": "steps:EnsureCollections"}],
        )
        self._write_plugin(
            "second-plugin",
            [{"kind": "collections.ensure", "backend": "steps:EnsureCollections"}],
        )

        self.assertTrue(self.registry.enable_plugin("first-plugin"))
        self.assertFalse(self.registry.enable_plugin("second-plugin"))

        self.assertEqual(self.step_kinds.get("collections.ensure").source, "first-plugin")
