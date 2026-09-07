"""workbench_single_result_gallery: seeded SYSTEM row + served to non-admin.

The workbench gallery strip's visibility for a single-output run is
admin-controlled but every user's own workbench needs to read the value, so
this key must be one of the deliberate exceptions to the "SYSTEM settings are
admin-only to read" rule in
`SettingsController.get_settings` (PUBLIC_SYSTEM_SETTING_KEYS).

The migration is loaded FRESH under the patched test DB (via
spec_from_file_location) so its module-level ``db`` binds to the test
database deterministically, independent of session-wide import order - same
pattern as tests/platform/settings/test_model_cache_scope_setting.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.settings.routes import SettingsController
from src.platform.settings.settings import Settings
from src.platform.settings.records import SettingType
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository

import tests.conftest as ct

_MIGRATIONS = Path("src/platform/database/migrations")
_KEY = "workbench_single_result_gallery"


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
        _load("016_workbench_single_result_gallery", f"m016_{id(test_database)}").up()
        yield test_database
    test_database.close()


def _controller():
    repo = SettingRepository()
    return SettingsController(Settings(repo), repo, Mock(), Mock(), Mock())


def _non_admin():
    return User(id="u", username="u", email="u@x.com",
                password_hash="h", account_type=AccountType.USER)


def test_migration_seeds_default_false(seeded_db):
    setting = SettingRepository().get_setting_by_key(_KEY)
    assert setting is not None
    assert setting.type == SettingType.SYSTEM
    assert setting.get_typed_value() is False


def test_migration_is_idempotent(seeded_db):
    # Re-running `up()` must not insert a second row or change the value.
    _load("016_workbench_single_result_gallery", f"m016_rerun_{id(seeded_db)}").up()
    repo = SettingRepository()
    setting = repo.get_setting_by_key(_KEY)
    assert setting.get_typed_value() is False


@pytest.mark.asyncio
async def test_served_to_non_admin_through_get_settings(seeded_db):
    response = await _controller().get_settings(_non_admin())
    assert response.success is True
    assert response.data[_KEY] is False


@pytest.mark.asyncio
async def test_other_system_settings_stay_hidden_from_non_admin(seeded_db):
    # The public allowlist covers this one key only - a regular SYSTEM
    # setting seeded by the same baseline must still be excluded.
    response = await _controller().get_settings(_non_admin())
    assert "model_cache_scope" not in response.data
