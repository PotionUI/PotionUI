"""model_cache_scope setting: seeded row (001_baseline) + manager/controller path.

Regression for the missing seed row that made PUT /api/settings/model_cache_scope
return setting_not_found so an admin could never flip it to 'global'.

The migration is loaded FRESH under the patched test DB (via spec_from_file_location)
so its module-level ``db`` binds to the test database deterministically, independent
of session-wide import order.
"""

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


def _load(stem: str, name: str):
    """Load a migration module FRESH so its ``from ... import db`` binds to the
    currently-patched test DB (session-order-independent)."""
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_db():
    """A fresh isolated in-memory DB with the full baseline schema applied, so
    model_cache_scope is deterministically seeded.

    Patches the ``db`` reference in the database module (for the freshly-
    loaded migration); ``SettingRepository`` imports ``db`` at call time, so
    it picks up the same patched reference without a module of its own to
    patch.
    """
    test_database = ct.TestDatabase()
    with patch("src.platform.database.database.db", test_database):
        _load("001_baseline", f"m001_{id(test_database)}").up()
        yield test_database
    test_database.close()


# --- migration seeds the row ----------------------------------------------

def test_migration_seeds_default_preset(seeded_db):
    setting = SettingRepository().get_setting_by_key("model_cache_scope")
    assert setting is not None
    assert setting.value == "preset"
    assert setting.type == SettingType.SYSTEM


# --- manager get / set / invalid-clamp ------------------------------------

def test_manager_get_set_and_invalid_clamp(seeded_db):
    mgr = Settings(SettingRepository())
    assert mgr.get_model_cache_scope() == "preset"          # seeded default
    assert mgr.set_setting("model_cache_scope", "global")
    assert mgr.get_model_cache_scope() == "global"          # flip persists
    # An unrecognised value is clamped to 'preset' on read (validity-at-read,
    # the same pattern as native_attention_backend).
    mgr.set_setting("model_cache_scope", "bogus")
    assert mgr.get_model_cache_scope() == "preset"


# --- controller path (row now exists -> no setting_not_found) --------------

def _controller():
    repo = SettingRepository()
    return SettingsController(Settings(repo), repo, Mock(), Mock(), Mock())


def _admin():
    return User(id="a", username="admin", email="a@x.com",
                password_hash="h", account_type=AccountType.ADMIN)


@pytest.mark.asyncio
async def test_controller_put_succeeds_for_admin(seeded_db):
    resp = await _controller().update_setting_by_key(
        "model_cache_scope", SettingUpdateRequest(value="global"), _admin())
    assert resp.success is True                              # was error='setting_not_found'
    assert Settings(SettingRepository()).get_model_cache_scope() == "global"


@pytest.mark.asyncio
async def test_controller_put_requires_admin(seeded_db):
    from fastapi import HTTPException
    non_admin = User(id="u", username="u", email="u@x.com",
                     password_hash="h", account_type=AccountType.USER)
    # A SYSTEM setting can only be changed by an admin: the call raises and the
    # value is left untouched.
    with pytest.raises(HTTPException):
        await _controller().update_setting_by_key(
            "model_cache_scope", SettingUpdateRequest(value="global"), non_admin)
    assert Settings(SettingRepository()).get_model_cache_scope() == "preset"
