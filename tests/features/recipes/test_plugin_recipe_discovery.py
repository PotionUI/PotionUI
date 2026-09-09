"""Recipes contributed by a plugin's `recipes:` manifest root.

Mirrors tests/features/presets/test_plugin_preset_discovery.py. The real
comfyui-backend fixture cases moved with that plugin to its own tests/
(content/plugins/marketplace/comfyui-backend is out of tree); these are the
mechanism tests that only need a synthetic manifest.
"""

from pathlib import Path
from types import SimpleNamespace

import yaml

from src.platform.plugins.manifest import PluginManifestSchema
from src.features.recipes.catalog import RecipeCatalog, plugin_recipe_roots

# A comfyui recipe that ships inside the comfyui-backend plugin.
COMFYUI_RECIPE_ID = "comfyui-detect"

VALID_RECIPE = """
schema_version: 1
id: "{recipe_id}"
version: 1
name: "Test Recipe"
engine: "native"
plugins: []
presets:
  - preset_id: "PRESET1"
steps:
  - key: "backend.ensure"
    kind: "backend.ensure"
    title: "Ensure backend"
    params:
      engine: "native"
"""


def _registry(enabled):
    return SimpleNamespace(get_enabled_plugins=lambda: list(enabled))


def _write_recipe(root: Path, recipe_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{recipe_id}.yml").write_text(VALID_RECIPE.format(recipe_id=recipe_id))


def test_manifest_schema_accepts_recipes_root():
    schema = PluginManifestSchema.model_validate({
        "id": "p", "name": "P", "version": "1.0.0", "description": "d",
        "author": "a", "type": "backend-only",
        "recipes": [{"path": "recipes"}],
    })
    assert [r.path for r in schema.recipes] == ["recipes"]


def test_plugin_recipe_roots_resolves_against_plugin_dir():
    manifest = SimpleNamespace(
        id="some-plugin",
        recipes=[{"path": "recipes"}],
        plugin_dir=Path("content/plugins/marketplace/some-plugin"),
    )
    roots = plugin_recipe_roots([manifest])
    assert [r.path for r in roots] == [
        Path("content/plugins/marketplace/some-plugin/recipes").resolve()
    ]


def test_manifest_without_recipes_contributes_no_roots():
    manifest = SimpleNamespace(
        id="some-plugin", recipes=[], plugin_dir=Path("content/plugins/marketplace/some-plugin")
    )
    assert plugin_recipe_roots([manifest]) == []


def test_disabled_plugin_recipe_is_absent(tmp_path):
    catalog = RecipeCatalog(str(tmp_path), plugin_registry=_registry([]))
    ids = {r.id for r in catalog.list_recipes()}
    assert COMFYUI_RECIPE_ID not in ids


def test_plugin_recipe_id_colliding_with_core_reports_error_core_wins(tmp_path):
    _write_recipe(tmp_path / "marketplace", "dup")
    plugin_dir = tmp_path / "plugin"
    _write_recipe(plugin_dir / "recipes", "dup")
    manifest = SimpleNamespace(id="dup-plugin", plugin_dir=plugin_dir, recipes=[{"path": "recipes"}])

    catalog = RecipeCatalog(str(tmp_path), plugin_registry=_registry([manifest]))

    recipes = catalog.list_recipes()
    assert [r.id for r in recipes] == ["dup"]
    assert recipes[0].source_path == str(tmp_path / "marketplace" / "dup.yml")

    assert len(catalog.load_errors) == 1
    plugin_recipe_path = str(plugin_dir / "recipes" / "dup.yml")
    assert plugin_recipe_path in catalog.load_errors
    assert "Duplicate recipe id" in catalog.load_errors[plugin_recipe_path][0]


def test_reload_makes_newly_enabled_plugin_recipe_appear(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_recipe(plugin_dir / "recipes", "plugin-recipe")
    manifest = SimpleNamespace(id="some-plugin", plugin_dir=plugin_dir, recipes=[{"path": "recipes"}])

    class _MutableRegistry:
        def __init__(self):
            self.enabled = []

        def get_enabled_plugins(self):
            return list(self.enabled)

    registry = _MutableRegistry()
    catalog = RecipeCatalog(str(tmp_path / "core"), plugin_registry=registry)
    assert catalog.get_recipe("plugin-recipe") is None

    registry.enabled = [manifest]
    catalog.reload()

    assert catalog.get_recipe("plugin-recipe") is not None


def test_reload_makes_disabled_plugin_recipe_disappear(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_recipe(plugin_dir / "recipes", "plugin-recipe")
    manifest = SimpleNamespace(id="some-plugin", plugin_dir=plugin_dir, recipes=[{"path": "recipes"}])

    class _MutableRegistry:
        def __init__(self, enabled):
            self.enabled = enabled

        def get_enabled_plugins(self):
            return list(self.enabled)

    registry = _MutableRegistry([manifest])
    catalog = RecipeCatalog(str(tmp_path / "core"), plugin_registry=registry)
    assert catalog.get_recipe("plugin-recipe") is not None

    registry.enabled = []
    catalog.reload()

    assert catalog.get_recipe("plugin-recipe") is None


PLUGIN_KIND_RECIPE = """
schema_version: 1
id: "plugin-kind"
version: 1
name: "Plugin Kind Recipe"
engine: "native"
plugins: []
presets:
  - preset_id: "PRESET1"
steps:
  - key: "collections.ensure"
    kind: "collections.ensure"
    title: "Ensure collections"
    params: {}
"""


class _StepKinds:
    def __init__(self, kinds):
        self._kinds = list(kinds)

    def all(self):
        return [SimpleNamespace(kind=k, source="p") for k in self._kinds]


def test_plugin_recipe_is_attributed_to_its_plugin(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_recipe(plugin_dir / "recipes", "plugin-recipe")
    manifest = SimpleNamespace(id="some-plugin", plugin_dir=plugin_dir, recipes=[{"path": "recipes"}])

    catalog = RecipeCatalog(str(tmp_path / "core"), plugin_registry=_registry([manifest]))
    recipe = catalog.get_recipe("plugin-recipe")

    assert recipe.source == "plugin"
    assert recipe.plugin_id == "some-plugin"


def test_core_recipes_carry_their_root_as_source(tmp_path):
    _write_recipe(tmp_path / "marketplace", "shipped")
    _write_recipe(tmp_path / "local", "mine")

    catalog = RecipeCatalog(str(tmp_path))

    assert catalog.get_recipe("shipped").source == "marketplace"
    assert catalog.get_recipe("shipped").plugin_id is None
    assert catalog.get_recipe("mine").source == "local"


def test_a_recipe_using_an_unregistered_step_kind_does_not_load(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir(parents=True)
    (root / "plugin-kind.yml").write_text(PLUGIN_KIND_RECIPE)

    catalog = RecipeCatalog(str(tmp_path))

    assert catalog.get_recipe("plugin-kind") is None
    issues = catalog.load_errors[str(root / "plugin-kind.yml")]
    assert any("unknown step kind" in issue for issue in issues)


def test_a_recipe_using_a_registered_plugin_step_kind_loads(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir(parents=True)
    (root / "plugin-kind.yml").write_text(PLUGIN_KIND_RECIPE)

    catalog = RecipeCatalog(
        str(tmp_path), step_kind_registry=_StepKinds(["collections.ensure"])
    )

    assert catalog.get_recipe("plugin-kind") is not None
    assert catalog.load_errors == {}
