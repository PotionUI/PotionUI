import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.repository import DatabasePresetRepository
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import runtime_registries
from src.plugin_api import presets as api
from src.plugin_api.presets import PresetInstallState, PresetTemplate
from tests.fixtures.seed import seed_user

SOLO = "01TESTPRESETTEMPLATESOLO00"
GROUPED = "01TESTPRESETTEMPLATEGROUP0"
IDLE = "01TESTPRESETTEMPLATEIDLE00"
LOOSE = "01TESTPRESETTEMPLATELOOSE0"
NONE = "01TESTPRESETTEMPLATENONE00"

FORM = """name: "custom"
fields:
  - name: "source_image"
    type: "image"
    label: "Source"
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _preset(root, folder, preset_id):
    preset = root / folder
    _write(preset / "preset.yml", (
        f'schema: 1\nid: "{preset_id}"\nname: "{folder}"\nversion: "1.0.0"\ncategory: "image"\n'
        'engine: "native"\nmodes:\n  - txt2img\n'
    ))
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    _write(preset / "modes" / "txt2img" / "form.yml", FORM)
    _write(preset / "modes" / "txt2img" / "pipeline.yml", "pipeline: []\n")


@pytest.fixture
def world(mock_db, tmp_path, monkeypatch):
    seed_user(mock_db, "member")
    root = tmp_path / "presets"
    shared = tmp_path / "shared"
    for folder, preset_id in (("Solo", SOLO), ("Grouped", GROUPED), ("Idle", IDLE), ("Loose", LOOSE)):
        _preset(root, folder, preset_id)
    loader = PresetTemplateLoader([str(root)], shared_path=shared)
    presets = DatabasePresetRepository()
    groups = UserGroupRepository()
    for preset_id in (SOLO, GROUPED, IDLE):
        presets.install_preset(preset_id)
    presets.assign_preset_to_user(SOLO, "member")
    groups.assign_preset_to_group(groups.create_group("studio").id, GROUPED)
    container = SimpleNamespace(
        file_preset_repository=FilePresetRepository(loader),
        preset_collaborators=SimpleNamespace(db_repo=presets, group_repo=groups),
    )
    monkeypatch.setattr(runtime_registries, "_container", container)
    return SimpleNamespace(loader=loader, root=root, shared=shared, presets=presets)


def test_a_loaded_preset_is_handed_over_as_a_copy_of_what_its_loader_built(world):
    template = api.preset_template(SOLO)

    assert world.loader.load_errors == {}
    assert isinstance(template, PresetTemplate)
    assert template == world.loader.get(SOLO)
    assert template is not world.loader.get(SOLO)
    assert Path(template.path).resolve() == (world.root / "Solo").resolve()
    assert template.modes["txt2img"].forms[0].fields[0].name == "source_image"


def test_changing_the_template_handed_over_leaves_the_loaded_preset_as_it_was(world):
    before = copy.deepcopy(world.loader.get(SOLO))
    template = api.preset_template(SOLO)

    template.vars = {"injected": True}
    template.requires = {"min_vram_gb": 99}
    template.modes["txt2img"].forms[0].fields[0].name = "renamed"
    template.modes["txt2img"].forms[0].fields[0].configuration = {"multi": True}
    template.modes["img2img"] = template.modes.pop("txt2img")

    assert world.loader.get(SOLO) == before
    assert api.preset_template(SOLO) == before
    assert api.describe_preset(SOLO).modes.keys() == {"txt2img"}


def test_changing_a_nested_value_of_the_template_handed_over_leaves_the_loaded_preset_as_it_was(world):
    loaded = world.loader.get(SOLO)
    loaded.vars = {"video_director": {"modes": {"director": {"max_keyframes": 2}}}}
    loaded.requires = {"min_vram_gb": 12}
    template = api.preset_template(SOLO)

    template.vars["video_director"]["modes"]["director"]["max_keyframes"] = 50
    template.requires["min_vram_gb"] = 1

    assert world.loader.get(SOLO).vars == {"video_director": {"modes": {"director": {"max_keyframes": 2}}}}
    assert world.loader.get(SOLO).requires == {"min_vram_gb": 12}


def test_a_preset_that_is_not_installed_is_handed_over_too_and_an_unknown_one_is_none(world):
    assert api.preset_template(LOOSE) == world.loader.get(LOOSE)
    assert api.preset_template(LOOSE) is not world.loader.get(LOOSE)
    assert api.preset_template(NONE) is None


def _link_folder(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_a_path_a_preset_names_has_its_variables_filled_and_comes_back_resolved(world):
    preset_root = Path(api.preset_template(SOLO).path).resolve()
    shared = world.shared.resolve()

    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}/options.yml") == preset_root / "options.yml"
    assert api.resolve_preset_path(SOLO, "{{paths._shared}}/lists/names.yml") == shared / "lists" / "names.yml"
    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}/lists/../options.yml") == preset_root / "options.yml"
    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}") == preset_root
    assert api.resolve_preset_path(SOLO, str(preset_root / "options.yml")) == preset_root / "options.yml"


@pytest.mark.parametrize(
    "reference",
    [
        "{{ paths.preset }}/../Grouped/preset.yml",
        "{{ paths.preset }}/../../outside.txt",
        "{{ paths.preset }}/lists/../../../outside.txt",
        "{{ paths._shared }}/../outside.txt",
        "{{ paths.preset }}/../Solo-evil/options.yml",
        "lists/names.yml",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "C:outside.txt",
        "\\\\server\\share\\options.yml",
        "//server/share/options.yml",
        "{{ paths.preset }}/options\x00.yml",
    ],
)
def test_a_path_that_leaves_the_preset_and_the_shared_folder_is_none(world, reference):
    assert api.resolve_preset_path(SOLO, reference) is None


def test_an_absolute_path_outside_both_folders_is_none(world, tmp_path):
    assert api.resolve_preset_path(SOLO, str(tmp_path / "outside.txt")) is None
    assert api.resolve_preset_path(SOLO, str(world.root / "Grouped" / "preset.yml")) is None


def test_a_link_inside_the_preset_is_followed_before_the_path_is_checked(world, tmp_path):
    preset_root = Path(api.preset_template(SOLO).path).resolve()
    (tmp_path / "outside").mkdir()
    _link_folder(preset_root / "escape", tmp_path / "outside")
    _link_folder(preset_root / "inner", preset_root / "modes")

    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}/escape/secret.txt") is None
    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}/inner/txt2img/form.yml") == (
        preset_root / "modes" / "txt2img" / "form.yml"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="backslashes and case only fold into the path on Windows")
def test_on_windows_backslashes_and_a_different_case_are_checked_as_the_same_folder(world):
    preset_root = Path(api.preset_template(SOLO).path).resolve()

    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}\\..\\..\\outside.txt") is None
    assert api.resolve_preset_path(SOLO, str(preset_root).upper() + "\\options.yml") == preset_root / "options.yml"
    assert api.resolve_preset_path(SOLO, "\\\\?\\" + str(preset_root) + "\\..\\outside.txt") is None


def test_a_path_of_a_preset_that_is_not_loaded_is_none(world):
    assert api.resolve_preset_path(NONE, "{{ paths.preset }}/options.yml") is None


def test_a_preset_is_assigned_through_a_user_or_a_group(world):
    assert api.preset_install_state(SOLO) == PresetInstallState(installed=True, assigned=True)
    assert api.preset_install_state(GROUPED) == PresetInstallState(installed=True, assigned=True)
    assert api.preset_install_state(IDLE) == PresetInstallState(installed=True, assigned=False)


def test_a_preset_that_is_not_installed_is_assigned_to_no_one(world):
    assert api.preset_install_state(LOOSE) == PresetInstallState(installed=False, assigned=False)
    assert api.preset_install_state(NONE) == PresetInstallState(installed=False, assigned=False)


def test_the_install_state_follows_the_assignments(world):
    world.presets.unassign_preset_from_user(SOLO, "member")
    world.presets.assign_preset_to_user(IDLE, "member")

    assert api.preset_install_state(SOLO) == PresetInstallState(installed=True, assigned=False)
    assert api.preset_install_state(IDLE) == PresetInstallState(installed=True, assigned=True)

    world.presets.uninstall_preset(IDLE)
    assert api.preset_install_state(IDLE) == PresetInstallState(installed=False, assigned=False)
