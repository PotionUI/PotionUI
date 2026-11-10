from unittest.mock import patch

import pytest

from src.features.presets import operations
from src.features.presets.exceptions import (
    InvalidCoverError,
    InvalidDisplayError,
    InvalidModesError,
    MirrorIdUnavailableError,
    MirrorSourceInvalidError,
    MirrorSourceNotInstalledError,
    NotAMirrorError,
    PresetHasMirrorsError,
    PresetRowNotFoundError,
    PresetRowNotInstalledError,
)
from src.features.presets.overlays import MIRROR_SOURCE_MISSING
from tests.features.presets.mirror_world import build_world, png_bytes


@pytest.fixture
def world(mock_db, tmp_path):
    return build_world(mock_db, tmp_path)


def _mirror(world, name="Anime", **body):
    payload = {"name": name, "description": None, "cover": None, "offered_modes": None, "first_mode": None, **body}
    return operations.create_mirror(world.collaborators, "krea2", payload)


def _listed(world, user):
    return {preset["id"]: preset for preset in operations.list_presets(world.collaborators, user, user is world.admin)}


def test_a_mirror_of_an_installed_preset_file_is_a_new_preset_with_the_source_settings(world):
    world.install("krea2")
    world.repo.set_preset_configuration("krea2", {"checkpoint_tags": ["t1"]})
    world.repo.set_preset_form_overrides("krea2", {"txt2img": {"steps": {"default": 30}}})

    detail = _mirror(world, offered_modes=["edit", "txt2img"], first_mode="edit")

    mirror_id = detail["preset_id"]
    assert detail["is_mirror"] is True
    assert detail["source_preset_id"] == "krea2"
    assert detail["source_name"] == "Krea-2"
    assert detail["mirror_state"] == "ok"
    assert detail["file"] == {"name": "Krea-2", "description": None, "cover": "public/cover.png"}
    assert detail["effective"]["name"] == "Anime"
    assert detail["modes"]["offered"] == ["edit", "txt2img"] and detail["modes"]["first"] == "edit"
    assert [m["name"] for m in detail["modes"]["available"]] == ["txt2img", "img2img", "edit"]
    assert (detail["assignment_count"], detail["group_count"]) == (0, 0)
    row = world.repo.get_installed_preset_by_preset_id(mirror_id)
    assert row.configuration == {"checkpoint_tags": ["t1"]}
    assert row.form_overrides == {"txt2img": {"steps": {"default": 30}}}
    assert list(world.loader.get(mirror_id).modes) == ["edit", "txt2img"]
    assert world.loader.get("krea2").name == "Krea-2"


def test_a_new_mirror_is_visible_to_nobody_until_it_is_assigned(world):
    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]
    assert mirror_id not in _listed(world, world.regular)

    world.repo.assign_preset_to_user(mirror_id, "ann")
    assert _listed(world, world.regular)[mirror_id]["name"] == "Anime"
    world.repo.unassign_preset_from_user(mirror_id, "ann")
    assert mirror_id not in _listed(world, world.regular)

    group = world.groups.create_group("artists")
    world.groups.add_user_to_group(group.id, "ann")
    world.groups.assign_preset_to_group(group.id, mirror_id)
    listed = _listed(world, world.regular)
    assert listed[mirror_id]["name"] == "Anime"
    assert "is_mirror" not in listed[mirror_id]
    assert "krea2" not in listed


def test_the_source_must_exist_be_installed_and_be_a_preset_file(world):
    with pytest.raises(PresetRowNotFoundError):
        operations.create_mirror(world.collaborators, "missing", {"name": "X"})
    with pytest.raises(MirrorSourceNotInstalledError):
        _mirror(world)

    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]
    with pytest.raises(MirrorSourceInvalidError):
        operations.create_mirror(world.collaborators, mirror_id, {"name": "Nested"})


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"name": "   "},
        {"name": "x" * 81},
        {"description": "x" * 4001},
        {"cover": "public/nope.png"},
        {"cover": "preset.yml"},
        {"cover": "public/../../escape.png"},
        {"cover": "display/forged.png"},
    ],
)
def test_a_mirror_display_is_validated(world, body):
    world.install("krea2")

    with pytest.raises(InvalidDisplayError) as caught:
        _mirror(world, **{"name": "Anime", **body})

    assert caught.value.code == "invalid_display"
    assert caught.value.extra["errors"]


@pytest.mark.parametrize(
    "offered, first",
    [(["txt2img", "upscale"], None), ([], None), (["txt2img"], "edit"), (None, "upscale"), ("txt2img", None)],
)
def test_offered_modes_are_validated_against_the_source(world, offered, first):
    world.install("krea2")

    with pytest.raises(InvalidModesError):
        _mirror(world, offered_modes=offered, first_mode=first)


def test_changing_the_modes_of_a_mirror_reorders_and_filters_it(world):
    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]

    detail = operations.set_mirror_modes(world.collaborators, mirror_id, {"offered": ["img2img", "edit"], "first": "edit"})

    assert detail["modes"]["offered"] == ["img2img", "edit"]
    assert list(world.loader.get(mirror_id).modes) == ["edit", "img2img"]
    operations.set_mirror_modes(world.collaborators, mirror_id, {"offered": None, "first": None})
    assert list(world.loader.get(mirror_id).modes) == ["txt2img", "img2img", "edit"]


def test_a_preset_file_has_no_mode_choice(world):
    world.install("krea2")

    with pytest.raises(NotAMirrorError):
        operations.set_mirror_modes(world.collaborators, "krea2", {"offered": ["txt2img"], "first": None})
    with pytest.raises(PresetRowNotFoundError):
        operations.set_mirror_modes(world.collaborators, "nothing", {"offered": None, "first": None})


def test_a_generated_id_never_reuses_a_taken_one(world):
    world.install("krea2")
    world.install("flux")

    with patch("src.features.presets.operations.mirrors.generate_ulid", return_value="flux"):
        with pytest.raises(MirrorIdUnavailableError):
            _mirror(world)
    with patch("src.features.presets.operations.mirrors.generate_ulid", side_effect=["krea2", "FRESH"]):
        assert _mirror(world)["preset_id"] == "FRESH"


def test_a_source_with_mirrors_cannot_be_removed(world):
    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]

    with pytest.raises(PresetHasMirrorsError) as caught:
        operations.uninstall_preset(world.collaborators, "krea2", world.admin)

    assert caught.value.status_code == 409
    assert caught.value.extra["mirrors"] == [{"preset_id": mirror_id, "name": "Anime"}]
    assert world.repo.is_preset_installed("krea2")


def test_removing_a_mirror_drops_its_assignments_and_cover_and_keeps_its_name(world):
    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]
    world.repo.assign_preset_to_user(mirror_id, "ann")
    group = world.groups.create_group("artists")
    world.groups.assign_preset_to_group(group.id, mirror_id)
    operations.upload_display_cover(world.collaborators, mirror_id, "cover.png", png_bytes())
    row = world.repo.get_installed_preset_by_preset_id(mirror_id)
    assert world.covers.directory(row.id).is_dir()

    result = operations.uninstall_preset(world.collaborators, mirror_id, world.admin)

    assert result["tombstone"] == {"preset_id": mirror_id, "name": "Anime"}
    assert world.repo.get_installed_preset_by_preset_id(mirror_id) is None
    assert world.repo.get_available_preset_ids_for_user("ann") == []
    assert world.groups.get_group_presets(group.id) == []
    assert not world.covers.directory(row.id).exists()
    assert world.loader.get(mirror_id) is None
    assert world.names().resolve(mirror_id) == "Anime"
    assert world.repo.preset_id_taken(mirror_id)
    operations.uninstall_preset(world.collaborators, "krea2", world.admin)
    assert not world.repo.is_preset_installed("krea2")


def test_a_mirror_whose_source_files_vanish_is_listed_for_admins_and_hidden_from_users(world):
    world.install("krea2")
    mirror_id = _mirror(world)["preset_id"]
    world.repo.assign_preset_to_user(mirror_id, "ann")
    (world.root / "Krea-2" / "std" / "preset.yml").unlink()
    world.loader.reload()

    admin_list = _listed(world, world.admin)

    entry = admin_list[mirror_id]
    assert entry["mirror_state"] == MIRROR_SOURCE_MISSING
    assert entry["name"] == "Anime"
    assert entry["installed"] is True and entry["is_mirror"] is True
    assert entry["modes"] == [] and entry["engine"] is None
    assert mirror_id not in _listed(world, world.regular)
    assert operations.get_admin_detail(world.collaborators, mirror_id)["file"] is None


def test_the_admin_list_marks_renamed_presets_and_counts_mirrors(world):
    world.install("krea2")
    operations.set_display(world.collaborators, "krea2", {"name": "Realistic", "description": None, "cover": None})
    mirror_id = _mirror(world)["preset_id"]

    listed = _listed(world, world.admin)

    assert listed["krea2"]["name"] == "Realistic"
    assert listed["krea2"]["file_name"] == "Krea-2"
    assert listed["krea2"]["display_overridden"] is True
    assert listed["krea2"]["mirror_count"] == 1
    assert listed["krea2"]["is_mirror"] is False
    assert listed["flux"]["display_overridden"] is False and listed["flux"]["installed"] is False
    assert listed[mirror_id]["is_mirror"] is True
    assert listed[mirror_id]["source_preset_id"] == "krea2"
    assert listed[mirror_id]["mirror_state"] == "ok"
    assert listed[mirror_id]["file_name"] == "Krea-2"


def test_renaming_a_preset_file_changes_its_name_everywhere_and_clearing_restores_it(world):
    world.install("krea2")

    detail = operations.set_display(
        world.collaborators, "krea2", {"name": " Realistic ", "description": "Photos", "cover": "public/alt.png"}
    )

    assert detail["display"] == {"name": "Realistic", "description": "Photos", "cover": "public/alt.png"}
    assert detail["file"]["name"] == "Krea-2"
    assert detail["modes"]["offered"] is None and detail["modes"]["first"] is None
    assert world.loader.get("krea2").name == "Realistic"
    assert world.loader.get("krea2").media["cover"] == "public/alt.png"
    assert world.names().resolve("krea2") == "Realistic"

    operations.set_display(world.collaborators, "krea2", {"name": None, "description": None, "cover": None})
    assert world.loader.get("krea2").name == "Krea-2"


def test_display_needs_every_key_and_an_installed_preset(world):
    with pytest.raises(PresetRowNotInstalledError):
        operations.set_display(world.collaborators, "krea2", {"name": "X", "description": None, "cover": None})
    with pytest.raises(PresetRowNotFoundError):
        operations.set_display(world.collaborators, "missing", {"name": "X", "description": None, "cover": None})
    world.install("krea2")
    with pytest.raises(InvalidDisplayError) as caught:
        operations.set_display(world.collaborators, "krea2", {"name": "X"})
    assert len(caught.value.extra["errors"]) == 2


def test_an_uploaded_cover_becomes_the_display_cover_and_replaces_the_previous_upload(world):
    world.install("krea2")

    first = operations.upload_display_cover(world.collaborators, "krea2", "a.png", png_bytes())["display"]["cover"]
    second = operations.upload_display_cover(world.collaborators, "krea2", "b.png", png_bytes())["display"]["cover"]

    row = world.repo.get_installed_preset_by_preset_id("krea2")
    assert second.startswith("display/") and second != first
    assert [p.name for p in world.covers.directory(row.id).iterdir()] == [second.split("/")[1]]
    assert world.loader.get("krea2").media["cover"] == second
    detail = operations.set_display(world.collaborators, "krea2", {"name": None, "description": None, "cover": second})
    assert detail["effective"]["cover"] == second


@pytest.mark.parametrize(
    "filename, content",
    [("cover.gif", b"GIF89a"), ("cover.png", b"not a png"), ("cover.png", b""), ("cover.mp4", b"\0\0\0\x18ftypmp42" + b"0" * 1600 * 1024)],
)
def test_a_cover_upload_is_checked(world, filename, content):
    world.install("krea2")

    with pytest.raises(InvalidCoverError):
        operations.upload_display_cover(world.collaborators, "krea2", filename, content)


def test_removing_a_preset_file_clears_its_display_overrides(world):
    world.install("krea2")
    operations.set_display(world.collaborators, "krea2", {"name": "Realistic", "description": None, "cover": None})
    operations.upload_display_cover(world.collaborators, "krea2", "a.png", png_bytes())
    row = world.repo.get_installed_preset_by_preset_id("krea2")

    operations.uninstall_preset(world.collaborators, "krea2", world.admin)

    assert world.loader.get("krea2").name == "Krea-2"
    assert not world.covers.directory(row.id).exists()
    world.install("krea2")
    assert world.repo.get_installed_preset_by_preset_id("krea2").display_name is None
