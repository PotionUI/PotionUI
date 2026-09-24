from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.presets.routes import PresetController
from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.preset_links import RecipePresetLinks
from src.platform.security.user import AccountType

RECIPE = """
schema_version: 1
id: "{recipe_id}"
version: 1
name: "{name}"
engine: "native"
category: "utility"
plugins: []
artifacts:
  - id: "ckpt"
    kind: "checkpoint"
    model_type: "checkpoint"
    filename: "{recipe_id}.safetensors"
    size_bytes: {size}
presets:
{presets}
steps:
  - key: "backend.ensure"
    kind: "backend.ensure"
    title: "Ensure backend"
    params:
      engine: "native"
"""


def _write(root: Path, recipe_id: str, preset_ids, name=None, size=100) -> None:
    root.mkdir(parents=True, exist_ok=True)
    presets = "\n".join(f'  - preset_id: "{p}"' for p in preset_ids) or "  []"
    (root / f"{recipe_id}.yml").write_text(
        RECIPE.format(recipe_id=recipe_id, name=name or recipe_id.title(), size=size, presets=presets)
    )


def _user(account_type):
    return SimpleNamespace(id="u", account_type=account_type)


ADMIN = _user(AccountType.ADMIN)
USER = _user(AccountType.USER)


class _Runner:
    def __init__(self, completed=()):
        self.completed = set(completed)

    def get_latest_completed_run(self, recipe_id):
        return SimpleNamespace(completed_at=None) if recipe_id in self.completed else None


def _plugin_registry(plugin_dir: Path):
    manifest = SimpleNamespace(id="shipper", recipes=[{"path": "recipes"}], plugin_dir=plugin_dir)
    return SimpleNamespace(get_enabled_plugins=lambda: [manifest])


@pytest.fixture
def catalog(tmp_path):
    _write(tmp_path / "recipes" / "marketplace", "alpha", ["P1"], name="Alpha Setup", size=10)
    _write(tmp_path / "recipes" / "local", "beta", ["P1", "P2"], name="Beta Setup", size=20)
    _write(tmp_path / "recipes" / "marketplace", "gamma", [], name="Gamma")
    plugin_dir = tmp_path / "plugin"
    _write(plugin_dir / "recipes", "plugged", ["P3"], name="Plugin Setup", size=30)
    return RecipeCatalog(str(tmp_path / "recipes"), plugin_registry=_plugin_registry(plugin_dir))


def test_index_lists_every_recipe_for_a_preset(catalog):
    assert [r.id for r in catalog.recipes_for_preset("P1")] == ["alpha", "beta"]
    assert [r.id for r in catalog.recipes_for_preset("P2")] == ["beta"]


def test_index_covers_plugin_shipped_recipes(catalog):
    recipes = catalog.recipes_for_preset("P3")
    assert [r.id for r in recipes] == ["plugged"]
    assert recipes[0].plugin_id == "shipper"


def test_preset_without_recipe_has_none(catalog):
    assert catalog.recipes_for_preset("NOPE") == []
    assert "NOPE" not in catalog.preset_recipe_index()


def test_index_refreshes_on_reload(catalog, tmp_path):
    assert catalog.recipes_for_preset("P4") == []
    _write(tmp_path / "recipes" / "local", "delta", ["P4"])
    catalog.reload()
    assert [r.id for r in catalog.recipes_for_preset("P4")] == ["delta"]


def test_admin_sees_linked_recipes_with_readiness_and_size(catalog):
    links = RecipePresetLinks(catalog, _Runner(completed={"alpha"}))

    assert links.recipes_for_preset("P1", ADMIN) == [
        {"id": "alpha", "name": "Alpha Setup", "readiness": "installed", "total_download_bytes": 10},
        {"id": "beta", "name": "Beta Setup", "readiness": "available", "total_download_bytes": 20},
    ]
    by_preset = links.recipes_by_preset(ADMIN)
    assert [r["id"] for r in by_preset["P3"]] == ["plugged"]
    assert set(by_preset) == {"P1", "P2", "P3"}


def test_non_admin_sees_no_recipes(catalog):
    links = RecipePresetLinks(catalog, _Runner())

    assert links.recipes_for_preset("P1", USER) == []
    assert links.recipes_by_preset(USER) == {}
    assert links.recipes_for_preset("P1", None) == []


def _controller(catalog, collaborators):
    return PresetController(collaborators, Mock(), recipe_links=RecipePresetLinks(catalog, _Runner()))


@pytest.fixture
def forwarded_operations(monkeypatch):
    from src.features.presets import routes as routes_module

    class _Forwarder:
        def __getattr__(self, name):
            return lambda collaborators, *args, **kwargs: getattr(collaborators, name)(*args, **kwargs)

    monkeypatch.setattr(routes_module, "operations", _Forwarder())


@pytest.mark.asyncio
async def test_preset_list_carries_recipes(catalog, forwarded_operations):
    collaborators = Mock()
    collaborators.list_presets.return_value = [{"id": "P1"}, {"id": "P9"}]

    result = await _controller(catalog, collaborators).list_presets(ADMIN, True)

    assert [r["id"] for r in result.data[0]["recipes"]] == ["alpha", "beta"]
    assert result.data[1]["recipes"] == []


@pytest.mark.asyncio
async def test_preset_detail_carries_recipes_only_for_admins(catalog, forwarded_operations):
    collaborators = Mock()
    collaborators.get_preset.side_effect = lambda preset_id: {"id": preset_id}
    controller = _controller(catalog, collaborators)

    admin = await controller.get_preset("P2", ADMIN)
    user = await controller.get_preset("P2", USER)

    assert admin.data["recipes"] == [
        {"id": "beta", "name": "Beta Setup", "readiness": "available", "total_download_bytes": 20}
    ]
    assert user.data["recipes"] == []
