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


def test_a_loaded_preset_is_handed_over_as_its_loader_built_it(world):
    template = api.preset_template(SOLO)

    assert world.loader.load_errors == {}
    assert isinstance(template, PresetTemplate)
    assert template is world.loader.get(SOLO)
    assert Path(template.path).resolve() == (world.root / "Solo").resolve()
    assert template.modes["txt2img"].forms[0].fields[0].name == "source_image"


def test_a_preset_that_is_not_installed_is_handed_over_too_and_an_unknown_one_is_none(world):
    assert api.preset_template(LOOSE) is world.loader.get(LOOSE)
    assert api.preset_template(NONE) is None


def test_a_path_a_preset_names_has_its_variables_filled(world):
    preset_root = Path(api.preset_template(SOLO).path)

    assert api.resolve_preset_path(SOLO, "{{ paths.preset }}/options.yml") == preset_root / "options.yml"
    assert api.resolve_preset_path(SOLO, "{{paths._shared}}/lists/names.yml") == world.shared / "lists" / "names.yml"
    assert api.resolve_preset_path(SOLO, "lists/names.yml") == Path("lists/names.yml")


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
