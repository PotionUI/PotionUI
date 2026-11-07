from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.features.generation.dto import GenerationRequest
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.repository import DatabasePresetRepository
from src.features.user_groups.repository import UserGroupRepository
from src.platform.database.rows import now_utc
from src.platform.plugins import runtime_registries
from src.platform.security.user import AccountType, User
from src.plugin_api.generation import submit_generation
from src.plugin_api.presets import PresetDescription, describe_preset, list_presets
from tests.fixtures.seed import seed_user

DIRECT = "01TESTLISTPRESETDIRECT0000"
GROUP = "01TESTLISTPRESETGROUP00000"
OTHER = "01TESTLISTPRESETOTHER00000"
UNINSTALLED = "01TESTLISTPRESETUNINSTALL0"
NO_FILES = "01TESTLISTPRESETNOFILES000"
LOADED = (DIRECT, GROUP, OTHER, UNINSTALLED)

FORM = """name: "custom"
fields:
  - name: "source_image"
    type: "image"
    label: "Source"
    required: true
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _preset(root, folder, preset_id, name, category):
    preset = root / folder
    _write(preset / "preset.yml", (
        f'schema: 1\nid: "{preset_id}"\nname: "{name}"\nversion: "1.0.0"\ncategory: "{category}"\n'
        'engine: "native"\nrequires:\n  min_vram_gb: 8\nmodes:\n  - txt2img\n'
    ))
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    _write(preset / "modes" / "txt2img" / "form.yml", FORM)
    _write(preset / "modes" / "txt2img" / "pipeline.yml", "pipeline: []\n")


def _user(user_id, account_type=AccountType.USER):
    return User(
        id=user_id, username=user_id, email=f"{user_id}@example.test", password_hash="$2b$12$x",
        account_type=account_type, created_at=now_utc(), last_login=None,
    )


class World:
    def __init__(self, db, root):
        for user_id in ("member", "stranger", "admin"):
            seed_user(db, user_id)
        self.member = _user("member")
        self.stranger = _user("stranger")
        self.admin = _user("admin", AccountType.ADMIN)
        _preset(root, "Uninstalled", UNINSTALLED, "Uninstalled", "image")
        _preset(root, "Direct", DIRECT, "Direct", "image")
        _preset(root, "Other", OTHER, "Other", "audio")
        _preset(root, "A-Group", GROUP, "Group", "video")
        self.loader = PresetTemplateLoader([str(root)])
        self.files = FilePresetRepository(self.loader)
        self.presets = DatabasePresetRepository()
        self.groups = UserGroupRepository()
        for preset_id in (DIRECT, GROUP, OTHER, NO_FILES):
            self.presets.install_preset(preset_id)
        self.presets.assign_preset_to_user(DIRECT, "member")
        self.presets.assign_preset_to_user(NO_FILES, "member")
        self.studio = self.groups.create_group("studio")
        self.groups.add_user_to_group(self.studio.id, "member")
        self.groups.assign_preset_to_group(self.studio.id, GROUP)
        self.orchestrator = SimpleNamespace(
            start_generation=AsyncMock(return_value={"generation_id": "g1", "status": "pending", "queue_position": 0})
        )
        self.container = SimpleNamespace(
            file_preset_repository=self.files,
            preset_collaborators=SimpleNamespace(db_repo=self.presets),
            generation_orchestrator=self.orchestrator,
        )

    def route_order(self, preset_ids):
        return [entry["id"] for entry in self.files.list_all_presets() if entry["id"] in preset_ids]


@pytest.fixture
def world(mock_db, tmp_path, monkeypatch):
    built = World(mock_db, tmp_path / "presets")
    monkeypatch.setattr(runtime_registries, "_container", built.container)
    return built


def _ids(user):
    return [preset.id for preset in list_presets(user)]


def test_a_user_gets_the_presets_assigned_directly_and_through_a_group_in_the_order_of_the_presets_route(world):
    assert world.loader.load_errors == {}
    assert world.route_order({DIRECT, GROUP}) == [GROUP, DIRECT]
    assert _ids(world.member) == [GROUP, DIRECT]


def test_a_user_without_assignments_gets_nothing(world):
    assert list_presets(world.stranger) == []


def test_an_admin_gets_every_installed_preset_and_no_other(world):
    assert _ids(world.admin) == world.route_order({DIRECT, GROUP, OTHER})


def test_an_installed_preset_whose_files_are_missing_is_left_out(world):
    assert NO_FILES not in _ids(world.member)
    assert NO_FILES not in _ids(world.admin)


def test_a_preset_leaves_the_list_once_its_assignment_its_group_or_its_install_is_gone(world):
    world.presets.unassign_preset_from_user(DIRECT, "member")
    assert _ids(world.member) == [GROUP]

    world.groups.remove_user_from_group(world.studio.id, "member")
    assert _ids(world.member) == []

    world.presets.assign_preset_to_user(OTHER, "stranger")
    world.presets.uninstall_preset(OTHER)
    assert _ids(world.stranger) == []
    assert OTHER not in _ids(world.admin)


def test_each_entry_is_the_full_description(world):
    listed = list_presets(world.admin)

    assert all(isinstance(preset, PresetDescription) for preset in listed)
    assert listed == [describe_preset(preset.id) for preset in listed]
    group = next(preset for preset in listed if preset.id == GROUP)
    assert (group.name, group.engine, group.category, group.requires) == ("Group", "native", "video", {"min_vram_gb": 8})
    assert group.modes["txt2img"].forms["custom"]["source_image"].required is True


def test_describe_preset_reads_a_loaded_preset_whether_or_not_it_is_installed(world):
    assert describe_preset(UNINSTALLED).name == "Uninstalled"
    assert UNINSTALLED not in _ids(world.admin)


def test_listing_needs_a_user(world):
    with pytest.raises(PermissionError):
        list_presets(None)


async def _accepted(user, preset_id):
    try:
        await submit_generation(user, GenerationRequest(preset_id=preset_id))
    except PermissionError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_user_may_submit_exactly_the_loaded_presets_listed_for_them(world):
    world.presets.assign_preset_to_user(OTHER, "stranger")

    for user in (world.member, world.stranger):
        listed = set(_ids(user))
        assert {preset_id for preset_id in LOADED if await _accepted(user, preset_id)} == listed


@pytest.mark.asyncio
async def test_an_admin_may_submit_every_preset_listed_for_them(world):
    for preset_id in _ids(world.admin):
        assert await _accepted(world.admin, preset_id)
