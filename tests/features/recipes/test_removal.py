from types import SimpleNamespace

import pytest

from src.features.recipes.derived import build_derived_recipes
from src.features.recipes.mode_plans import ModePlanner
from src.features.recipes.removal import ModeFileRemoval, ModeRemovalError
from src.features.recipes.targets import InstallTargets
from src.platform.runtime.gpu_profile import build_gpu_profile
from tests.features.recipes.mode_fixtures import FakeDbRepo, FakeLoader, FakeModelRepo, video_preset

BASE = [("diffusion_model", "dit-int8.safetensors"), ("text_encoder", "te-int8.safetensors"), ("vae", "vae.safetensors")]


class Catalog:
    def __init__(self, recipes, extra=()):
        self.recipes = dict(recipes)
        self.extra = list(extra)

    def get_recipe(self, recipe_id, version=None):
        return self.recipes.get(recipe_id)

    def list_recipes(self):
        return list(self.recipes.values()) + self.extra


class Locator:
    def __init__(self):
        self.by_model = {}

    def put(self, model_id, root_id, path):
        self.by_model.setdefault(model_id, []).append(
            SimpleNamespace(id=f"loc-{model_id}-{len(self.by_model.get(model_id, []))}", root_id=root_id, path=path, status="present")
        )

    def locations(self, model_id):
        return [loc for loc in self.by_model.get(model_id, []) if loc.path.exists()]


class Locations:
    def __init__(self):
        self.deleted = []

    def delete(self, location_id):
        self.deleted.append(location_id)


@pytest.fixture
def world(tmp_path):
    models_dir = tmp_path / "models"
    elsewhere = tmp_path / "mine"
    models_dir.mkdir()
    elsewhere.mkdir()
    repo = FakeModelRepo()
    locator = Locator()

    def place(model_type, filename, folder=models_dir, root="home", size=10):
        model = repo.add(model_type, filename, size=size)
        path = folder / filename
        path.write_bytes(b"x" * size)
        locator.put(model.id, root, path)
        return model, path

    db = FakeDbRepo(overrides={"VID": {"cinemagraph": {"effect_lora": {"default": "cine.safetensors", "editable": False}}}})
    loader = FakeLoader(video_preset())
    roots = SimpleNamespace(write_dir=lambda t: SimpleNamespace(path=models_dir, root_id="home"))
    targets = InstallTargets(
        model_roots=roots,
        gpu_profile_provider=lambda *a: build_gpu_profile((8, 9), 24.0),
        disk_usage=lambda p: SimpleNamespace(free=10 ** 12, total=10 ** 13, used=0),
    )
    planner = ModePlanner(loader, repo, preset_db_repo=db, targets=targets)
    recipes, _ = build_derived_recipes(loader.presets)
    locations = Locations()
    removal = ModeFileRemoval(Catalog(recipes), planner, repo, locator, roots, preset_db_repo=db, preset_loader=loader, locations_repository=locations)
    return SimpleNamespace(place=place, removal=removal, repo=repo, db=db, elsewhere=elsewhere, locations=locations, catalog=removal.recipe_catalog)


def test_preview_deletes_only_files_this_mode_alone_uses(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    cine, cine_path = world.place("lora", "cine.safetensors", size=200)

    preview = world.removal.preview("modes-vid-1-0", "cinemagraph")

    assert [d["filename"] for d in preview["delete"]] == ["cine.safetensors"]
    assert preview["frees_bytes"] == 200
    assert {k["reason_code"] for k in preview["keep"]} == {"shared"}
    assert preview["keep"][0]["modes"] == ["video"]
    assert preview["clears_defaults"] == [{"field": "effect_lora"}]


def test_remove_deletes_the_file_drops_the_index_row_and_clears_the_default(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    cine, cine_path = world.place("lora", "cine.safetensors", size=200)

    result = world.removal.remove("modes-vid-1-0", "cinemagraph", [cine.id])

    assert not cine_path.exists()
    assert world.repo.deleted == [cine.id]
    assert result["freed_bytes"] == 200
    assert world.db.overrides["VID"]["cinemagraph"]["effect_lora"] == {"editable": False}
    with pytest.raises(ModeRemovalError) as excinfo:
        world.removal.remove("modes-vid-1-0", "cinemagraph", [cine.id])
    assert excinfo.value.code == "conflict"


def test_files_outside_the_models_folder_and_admin_defaults_are_kept(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    own, own_path = world.place("lora", "water.safetensors", folder=world.elsewhere, root="mine")
    world.db.overrides["VID"]["video"] = {"model": {"default": "dit-int8.safetensors"}}
    world.place("lora", "restore.safetensors")
    world.db.overrides["OTHER"] = {"m": {"f": {"default": "restore.safetensors"}}}
    world.db.installed.add("OTHER")

    water = world.removal.preview("modes-vid-1-0", "add_water")
    assert water["delete"] == []
    assert water["keep"][-1]["reason_code"] == "outside_models_folder"
    assert own_path.exists()

    restore = world.removal.preview("modes-vid-1-0", "restore")
    assert [k["reason_code"] for k in restore["keep"] if k["filename"] == "restore.safetensors"] == ["admin_default"]


def test_unknown_recipe_or_mode_is_not_found(world):
    with pytest.raises(ModeRemovalError) as excinfo:
        world.removal.preview("nope", "video")
    assert excinfo.value.code == "not_found"
    with pytest.raises(ModeRemovalError):
        world.removal.preview("modes-vid-1-0", "ghost")
