from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.mcp import operations
from src.features.mcp.operations import MCP_USER_ENABLED_KEY
from src.features.settings import routes as settings_routes
from src.features.settings.routes import ADMIN_CONTROLLED_USER_SETTING_KEYS
from src.features.users.repository import UserRepository
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from tests.conftest import TestDatabase


@pytest.fixture
def stack():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        setting_repository = SettingRepository()
        settings = Settings(setting_repository)
        user_repository = UserRepository()
        container = SimpleNamespace(
            settings=settings,
            setting_repository=setting_repository,
            gpu_monitor=None,
            backend_registry=None,
        )
        yield container, settings, user_repository
    test_database.close()


@pytest.fixture
def switched_off_user(stack):
    _container, settings, user_repository = stack
    user = user_repository.create(username="alice", email="alice@example.com", password_hash="x")
    operations.set_user_enabled(settings, user_repository, user.id, False)
    return user


def _client(container, user):
    app = FastAPI()
    app.include_router(settings_routes.build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_current_admin_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_mcp_user_switch_key_is_admin_controlled():
    assert MCP_USER_ENABLED_KEY in ADMIN_CONTROLLED_USER_SETTING_KEYS


def test_user_cannot_switch_their_own_mcp_access_back_on(stack, switched_off_user):
    container, settings, _users = stack
    response = _client(container, switched_off_user).put(
        f"/api/settings/{MCP_USER_ENABLED_KEY}", json={"value": True}
    )

    assert response.status_code == 403
    assert operations.is_user_enabled(settings, switched_off_user.id) is False


def test_user_cannot_delete_the_override_that_switches_them_off(stack, switched_off_user):
    container, settings, _users = stack
    response = _client(container, switched_off_user).delete(f"/api/settings/user/{MCP_USER_ENABLED_KEY}")

    assert response.status_code == 403
    assert operations.is_user_enabled(settings, switched_off_user.id) is False


def test_bulk_update_naming_the_key_is_refused_whole(stack, switched_off_user):
    container, settings, _users = stack
    response = _client(container, switched_off_user).put(
        "/api/settings", json={MCP_USER_ENABLED_KEY: True, "media_nsfw_filter_mode": "hide"}
    )

    assert response.status_code == 403
    assert operations.is_user_enabled(settings, switched_off_user.id) is False
    assert settings.get_setting("media_nsfw_filter_mode", user_id=switched_off_user.id) in (None, "blur")


def test_ordinary_user_setting_is_still_writable_and_deletable(stack, switched_off_user):
    container, settings, _users = stack
    client = _client(container, switched_off_user)

    assert client.put("/api/settings/media_nsfw_filter_mode", json={"value": "hide"}).status_code == 200
    assert settings.get_setting("media_nsfw_filter_mode", user_id=switched_off_user.id) == "hide"
    assert client.put("/api/settings", json={"media_nsfw_filter_mode": "show"}).status_code == 200
    assert settings.get_setting("media_nsfw_filter_mode", user_id=switched_off_user.id) == "show"
    assert client.delete("/api/settings/user/media_nsfw_filter_mode").status_code == 200


def test_admin_route_still_switches_a_user_off_and_on(stack, switched_off_user):
    _container, settings, user_repository = stack
    operations.set_user_enabled(settings, user_repository, switched_off_user.id, True)
    assert operations.is_user_enabled(settings, switched_off_user.id) is True
    operations.set_user_enabled(settings, user_repository, switched_off_user.id, False)
    assert operations.is_user_enabled(settings, switched_off_user.id) is False
