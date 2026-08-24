"""`preset.ensure` executor against fake PresetManager/UserRepository surfaces."""

from types import SimpleNamespace

from src.features.presets.exceptions import (
    InvalidUsersException,
    PresetAlreadyInstalledException,
    PresetNotInstalledException,
)
from src.features.setup.executors.base import StepContext
from src.features.setup.executors.preset_ensure import PresetEnsureExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus
from src.platform.security.user import AccountType, User


class FakeFileRepo:
    def __init__(self, known_ids=None):
        self.known_ids = known_ids or set()

    def find_preset_by_id(self, preset_id):
        return SimpleNamespace(id=preset_id) if preset_id in self.known_ids else None


class FakePresetManager:
    def __init__(self, known_ids=None, install_raises=None, assign_raises=None):
        self.file_repo = FakeFileRepo(known_ids)
        self.install_raises = install_raises
        self.assign_raises = assign_raises
        self.install_calls = []
        self.assign_calls = []

    def install_preset(self, preset_id, user):
        self.install_calls.append((preset_id, user.id))
        if self.install_raises:
            raise self.install_raises

    def assign_preset_to_users(self, preset_id, user_ids, admin):
        self.assign_calls.append((preset_id, user_ids, admin.id))
        if self.assign_raises:
            raise self.assign_raises


class FakeUserRepository:
    def __init__(self, users=None):
        self.users = users or {}

    def get_by_id(self, user_id):
        return self.users.get(user_id)


def _owner() -> User:
    return User(username="owner", email="owner@example.com", password_hash="x", account_type=AccountType.ADMIN, id="owner-1")


def _context(preset_id="PRESET1", owner_id="owner-1"):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING, created_by=owner_id)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="preset.ensure", kind="preset.ensure", title="Install preset", params={"preset_id": preset_id})
    return StepContext(run=run, recipe=recipe, step=step)


def test_installs_and_assigns_when_all_new():
    preset_manager = FakePresetManager(known_ids={"PRESET1"})
    user_repo = FakeUserRepository({"owner-1": _owner()})
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context())

    assert result.success is True
    assert result.safe_output == {"preset_id": "PRESET1", "assigned_to": "owner-1"}
    assert preset_manager.install_calls == [("PRESET1", "owner-1")]
    assert preset_manager.assign_calls == [("PRESET1", ["owner-1"], "owner-1")]


def test_already_installed_is_not_an_error():
    preset_manager = FakePresetManager(
        known_ids={"PRESET1"}, install_raises=PresetAlreadyInstalledException("PRESET1")
    )
    user_repo = FakeUserRepository({"owner-1": _owner()})
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context())

    assert result.success is True
    assert preset_manager.assign_calls == [("PRESET1", ["owner-1"], "owner-1")]


def test_missing_owner_account_fails_clearly():
    preset_manager = FakePresetManager(known_ids={"PRESET1"})
    user_repo = FakeUserRepository({})  # owner-1 not found
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "OWNER_NOT_FOUND"


def test_preset_missing_on_disk_fails_clearly():
    preset_manager = FakePresetManager(known_ids=set())
    user_repo = FakeUserRepository({"owner-1": _owner()})
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "PRESET_MISSING_ON_DISK"
    assert preset_manager.install_calls == []


def test_assignment_failure_is_reported():
    preset_manager = FakePresetManager(
        known_ids={"PRESET1"}, assign_raises=InvalidUsersException(["owner-1"])
    )
    user_repo = FakeUserRepository({"owner-1": _owner()})
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context())

    assert result.success is False
    assert result.error_code == "PRESET_ASSIGN_FAILED"


def test_missing_preset_id_param_is_misconfiguration():
    preset_manager = FakePresetManager(known_ids={"PRESET1"})
    user_repo = FakeUserRepository({"owner-1": _owner()})
    executor = PresetEnsureExecutor(preset_manager, user_repo)

    result = executor.execute(_context(preset_id=None))

    assert result.success is False
    assert result.error_code == "PRESET_ENSURE_MISCONFIGURED"
