from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.settings.routes import SettingsController
from src.features.settings.dto import SettingUpdateRequest
from src.platform.settings.settings import Settings
from src.platform.settings.records import SettingType
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository

import tests.conftest as ct

_MIGRATIONS = Path("src/platform/database/migrations")
_KEY = "prompt_segment_pinned_actions"


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_db():
    test_database = ct.TestDatabase()
    with patch("src.platform.database.database.db", test_database):
        _load("001_baseline", f"m001_{id(test_database)}").up()
        _load("032_prompt_segment_pinned_actions", f"m032_{id(test_database)}").up()
        yield test_database
    test_database.close()


def _controller():
    repo = SettingRepository()
    return SettingsController(Settings(repo), repo, Mock(), Mock(), Mock())


def _user(user_id: str = "u1") -> User:
    from src.platform.database.database import db
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT OR IGNORE INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, user_id, f"{user_id}@x.com", "h", "USER"),
        )
    return User(id=user_id, username=user_id, email=f"{user_id}@x.com",
                password_hash="h", account_type=AccountType.USER)


def test_migration_seeds_default_empty_list(seeded_db):
    setting = SettingRepository().get_setting_by_key(_KEY)
    assert setting is not None
    assert setting.type == SettingType.USER
    assert setting.get_typed_value() == []


def test_migration_is_idempotent(seeded_db):
    _load("032_prompt_segment_pinned_actions", f"m032_rerun_{id(seeded_db)}").up()
    setting = SettingRepository().get_setting_by_key(_KEY)
    assert setting.get_typed_value() == []


def test_migration_down_removes_the_setting(seeded_db):
    _load("032_prompt_segment_pinned_actions", f"m032_down_{id(seeded_db)}").down()
    assert SettingRepository().get_setting_by_key(_KEY) is None


@pytest.mark.asyncio
async def test_get_defaults_to_empty_list_for_any_user(seeded_db):
    response = await _controller().get_setting_by_key(_KEY, _user())
    assert response.success is True
    assert response.data["value"] == []


@pytest.mark.asyncio
async def test_put_persists_pinned_ids_for_the_calling_user(seeded_db):
    controller = _controller()
    response = await controller.update_setting_by_key(
        _KEY, SettingUpdateRequest(value=["duplicate", "toggleDisabled"]), _user("u1")
    )
    assert response.success is True

    read_back = await controller.get_setting_by_key(_KEY, _user("u1"))
    assert read_back.data["value"] == ["duplicate", "toggleDisabled"]


@pytest.mark.asyncio
async def test_pinned_ids_are_isolated_per_user(seeded_db):
    controller = _controller()
    await controller.update_setting_by_key(
        _KEY, SettingUpdateRequest(value=["editDetails"]), _user("u1")
    )

    other_user_response = await controller.get_setting_by_key(_KEY, _user("u2"))
    assert other_user_response.data["value"] == []
