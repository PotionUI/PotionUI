import asyncio
from unittest.mock import MagicMock, Mock, patch

import pytest

import tests.conftest as ct
from src.features.backends.backend_registry import BackendRegistry
from src.features.settings.dto import SettingUpdateRequest
from src.features.settings.routes import SettingsController
from src.platform.filesystem.storage_settings import StorageSettings
from src.platform.runtime.gpu import GpuMonitor
from src.platform.security.secrets import (
    ENVELOPE_PREFIX,
    SecretCipher,
    configure_secret_cipher,
    generate_key,
)
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.platform.database._support import load_migration

PLAINTEXT = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.fixture
def stack():
    database = ct.TestDatabase()
    configure_secret_cipher(SecretCipher([generate_key()]))
    with patch("src.platform.database.database.db", database):
        load_migration("001_baseline", database).up()
        repository = SettingRepository()
        settings = Settings(repository)
        controller = SettingsController(
            settings=settings,
            setting_repository=repository,
            gpu_monitor=Mock(spec=GpuMonitor),
            backend_registry=MagicMock(spec=BackendRegistry),
        )
        yield controller, settings, database
    configure_secret_cipher(None)
    database.close()


@pytest.fixture
def admin():
    return User(
        id="admin1", username="admin", email="admin@example.com",
        password_hash="hash", account_type=AccountType.ADMIN,
    )


def _raw(database):
    with database.get_connection() as conn:
        return conn.execute("SELECT value FROM settings WHERE key = 's3_secret_key'").fetchone()[0]


def _assert_encrypted_and_readable(database, settings):
    raw = _raw(database)
    assert raw.startswith(ENVELOPE_PREFIX)
    assert PLAINTEXT not in raw
    assert StorageSettings(settings).get_s3_config().secret_key == PLAINTEXT


def test_bulk_update_encrypts_at_rest(stack, admin):
    controller, settings, database = stack
    response = asyncio.run(controller.update_settings({"s3_secret_key": PLAINTEXT}, user=admin))
    assert response.success
    _assert_encrypted_and_readable(database, settings)


def test_single_key_update_encrypts_at_rest(stack, admin):
    controller, settings, database = stack
    response = asyncio.run(
        controller.update_setting_by_key("s3_secret_key", SettingUpdateRequest(value=PLAINTEXT), user=admin)
    )
    assert response.success
    _assert_encrypted_and_readable(database, settings)


def test_set_s3_secret_key_encrypts_at_rest(stack):
    _, settings, database = stack
    assert StorageSettings(settings).set_s3_secret_key(PLAINTEXT)
    _assert_encrypted_and_readable(database, settings)


def test_masked_write_leaves_stored_secret_unchanged(stack, admin):
    controller, settings, database = stack
    asyncio.run(controller.update_settings({"s3_secret_key": PLAINTEXT}, user=admin))
    stored = _raw(database)
    asyncio.run(controller.update_settings({"s3_secret_key": "***"}, user=admin))
    asyncio.run(
        controller.update_setting_by_key("s3_secret_key", SettingUpdateRequest(value="***"), user=admin)
    )
    assert _raw(database) == stored
    assert StorageSettings(settings).get_s3_config().secret_key == PLAINTEXT


def test_clearing_the_secret_stores_empty_string(stack, admin):
    controller, settings, database = stack
    asyncio.run(controller.update_settings({"s3_secret_key": PLAINTEXT}, user=admin))
    asyncio.run(controller.update_settings({"s3_secret_key": ""}, user=admin))
    assert _raw(database) == ""


def test_legacy_plaintext_row_still_reads(stack):
    _, settings, database = stack
    with database.get_connection() as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 's3_secret_key'", (PLAINTEXT,))
        conn.commit()
    assert StorageSettings(settings).get_s3_config().secret_key == PLAINTEXT


@pytest.fixture
def client(stack, admin):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.features.settings.routes import build_router
    from src.platform.security.current_user import get_current_active_user

    controller, settings, database = stack
    container = SimpleNamespace(
        settings=settings,
        setting_repository=controller.setting_repository,
        gpu_monitor=controller.gpu_monitor,
        backend_registry=controller.backend_registry,
        api_key_collaborators=SimpleNamespace(access=Mock()),
    )
    app = FastAPI()
    app.dependency_overrides[get_current_active_user] = lambda: admin
    app.include_router(build_router(container))
    return TestClient(app), settings, database


def test_put_settings_route_encrypts_at_rest(client):
    http, settings, database = client
    response = http.put("/api/settings", json={"s3_secret_key": PLAINTEXT})
    assert response.status_code == 200
    _assert_encrypted_and_readable(database, settings)


def test_put_setting_by_key_route_encrypts_at_rest(client):
    http, settings, database = client
    response = http.put("/api/settings/s3_secret_key", json={"value": PLAINTEXT})
    assert response.status_code == 200
    _assert_encrypted_and_readable(database, settings)
