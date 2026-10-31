from types import SimpleNamespace

import pytest

from src.features.recipes.derived import build_derived_recipes
from src.features.recipes.mode_plans import ModePlanner
from src.features.recipes.removal import ModeFileRemoval, ModeRemovalError
from src.features.recipes.targets import InstallTargets
from src.platform.runtime.gpu_profile import build_gpu_profile
from src.features.recipes.schema import Recipe, RecipeArtifact
from tests.features.recipes.mode_fixtures import FakeDbRepo, FakeLoader, FakeModelRepo, mode, model_field, preset, rec, video_preset

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
    return SimpleNamespace(
        place=place, removal=removal, repo=repo, db=db, elsewhere=elsewhere, locations=locations,
        catalog=removal.recipe_catalog, loader=loader, models_dir=models_dir, locator=locator,
    )


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


def _kept(preview, filename):
    return [(k["reason_code"], k["reason"]) for k in preview["keep"] if k["filename"] == filename]


def test_files_outside_the_models_folder_and_admin_defaults_are_kept(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    own, own_path = world.place("lora", "water.safetensors", folder=world.elsewhere, root="mine")
    world.place("lora", "restore.safetensors")
    world.db.overrides["OTHER"] = {"m": {"f": {"default": "restore.safetensors"}}}
    world.db.installed.add("OTHER")
    world.loader.presets.append(preset({"m": mode([])}, preset_id="OTHER", name="Other"))

    water = world.removal.preview("modes-vid-1-0", "add_water")
    assert water["delete"] == []
    assert _kept(water, "water.safetensors") == [("outside_models_folder", "You added this yourself, outside the models folder")]
    assert own_path.exists()

    restore = world.removal.preview("modes-vid-1-0", "restore")
    assert _kept(restore, "restore.safetensors") == [("admin_default", "Set as a default file by an admin")]


def test_files_of_another_installed_preset_or_a_hand_written_recipe_are_kept(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    world.place("lora", "cine.safetensors")
    world.place("lora", "water.safetensors")
    other = preset({"m": mode([model_field("lora", "lora", [rec("cine.safetensors", 1)])])}, preset_id="OTHER", name="Other")
    world.loader.presets.append(other)
    world.db.installed.add("OTHER")
    starter = Recipe(
        id="vid-starter", schema_version=1, version=1, name="Vid Starter", engine="native",
        artifacts=[RecipeArtifact(id="w", kind="lora", model_type="lora", filename="water.safetensors")],
    )
    world.catalog.extra.append(starter)

    assert _kept(world.removal.preview("modes-vid-1-0", "cinemagraph"), "cine.safetensors") == [("shared", "Also used by Other")]
    assert _kept(world.removal.preview("modes-vid-1-0", "add_water"), "water.safetensors") == [("other_recipe", "Part of the Vid Starter recipe")]


class Broken:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError("database is locked")

        return fail


@pytest.mark.parametrize("which", ["db", "loader", "unloadable"])
def test_a_lookup_error_refuses_to_remove_anything(world, which):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    cine, cine_path = world.place("lora", "cine.safetensors", size=200)
    world.db.installed.add("OTHER")
    if which == "db":
        world.removal.preset_db_repo = Broken()
    elif which == "loader":
        world.removal.preset_loader = Broken()

    for call in (lambda: world.removal.preview("modes-vid-1-0", "cinemagraph"), lambda: world.removal.remove("modes-vid-1-0", "cinemagraph", [cine.id])):
        with pytest.raises(ModeRemovalError) as excinfo:
            call()
        assert excinfo.value.code == "unavailable"
    assert cine_path.exists()
    assert world.repo.deleted == []


def test_a_planner_that_cannot_read_admin_defaults_refuses_too(world):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    cine, cine_path = world.place("lora", "cine.safetensors")
    world.removal.planner.preset_db_repo = Broken()

    with pytest.raises(ModeRemovalError) as excinfo:
        world.removal.remove("modes-vid-1-0", "cinemagraph", [cine.id])
    assert excinfo.value.code == "unavailable"
    assert cine_path.exists()


def test_the_safety_collaborators_are_required(world):
    with pytest.raises(ValueError):
        ModeFileRemoval(world.catalog, world.removal.planner, world.repo, world.locator, None, preset_db_repo=None, preset_loader=world.loader)
    with pytest.raises(ValueError):
        ModeFileRemoval(world.catalog, world.removal.planner, world.repo, world.locator, None, preset_db_repo=world.db, preset_loader=None)


def _only_cine(world, path, root="home"):
    for model_type, filename in BASE:
        world.place(model_type, filename)
    model = world.repo.add("lora", "cine.safetensors", size=5)
    world.locator.put(model.id, root, path)
    return model


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


def test_a_symlink_in_the_models_folder_pointing_outside_is_kept(world):
    target = world.elsewhere / "real-cine.safetensors"
    target.write_bytes(b"x")
    link = world.models_dir / "cine.safetensors"
    _symlink(link, target)
    model = _only_cine(world, link)

    preview = world.removal.preview("modes-vid-1-0", "cinemagraph")
    assert _kept(preview, "cine.safetensors")[0][0] == "outside_models_folder"
    with pytest.raises(ModeRemovalError):
        world.removal.remove("modes-vid-1-0", "cinemagraph", [model.id])
    assert link.is_symlink() and target.exists()


def test_a_symlink_inside_the_models_folder_is_kept_even_when_it_points_inside(world):
    target = world.models_dir / "stored-cine.safetensors"
    target.write_bytes(b"x")
    link = world.models_dir / "cine.safetensors"
    _symlink(link, target)
    _only_cine(world, link)

    assert _kept(world.removal.preview("modes-vid-1-0", "cinemagraph"), "cine.safetensors")[0][0] == "outside_models_folder"
    assert link.is_symlink() and target.exists()


def test_a_path_that_escapes_the_models_folder_is_kept(world):
    target = world.elsewhere / "cine.safetensors"
    target.write_bytes(b"x")
    model = _only_cine(world, world.models_dir / ".." / "mine" / "cine.safetensors")

    assert _kept(world.removal.preview("modes-vid-1-0", "cinemagraph"), "cine.safetensors")[0][0] == "outside_models_folder"
    with pytest.raises(ModeRemovalError):
        world.removal.remove("modes-vid-1-0", "cinemagraph", [model.id])
    assert target.exists()


def test_a_location_on_another_root_is_kept_even_under_the_models_folder(world):
    path = world.models_dir / "cine.safetensors"
    path.write_bytes(b"x")
    _only_cine(world, path, root="read-only-share")

    assert _kept(world.removal.preview("modes-vid-1-0", "cinemagraph"), "cine.safetensors")[0][0] == "outside_models_folder"
    assert path.exists()


def test_unknown_recipe_or_mode_is_not_found(world):
    with pytest.raises(ModeRemovalError) as excinfo:
        world.removal.preview("nope", "video")
    assert excinfo.value.code == "not_found"
    with pytest.raises(ModeRemovalError):
        world.removal.preview("modes-vid-1-0", "ghost")
