"""Router-level authorization tests for the plugins API.

Plugin management (list/detail/enable/disable/delete/scan) and plugin settings
(which can hold credentials) are admin-only. The routes the normal UI needs for
every authenticated user - pages, sidebar, quick-actions, widgets, frontend
hooks/extensions - stay open. Settings routes also no longer accept a
caller-supplied user_id.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.plugins.routes import PluginController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


def _make_client(user):
    manager = Mock()
    # Open-route return values still served by the manager (consult live
    # registry state, not just the DB - see PluginManager).
    manager.get_active_quick_actions.return_value = []
    manager.get_active_sidebar_widgets.return_value = []
    manager.get_frontend_extensions.return_value = {"renderers": [], "contributions": []}

    repository = Mock()
    # Open-route return values now read straight from the repository.
    repository.get_all_active_pages.return_value = []
    repository.get_hooks_by_type.return_value = []

    registry = Mock()
    registry.get_plugin.return_value = None

    container = SimpleNamespace(
        plugin_controller=PluginController(
            plugin_manager=manager, plugin_repository=repository, plugin_registry=registry
        )
    )

    app = FastAPI()
    app.include_router(build_router(container))

    async def _fake_active_user():
        return user

    app.dependency_overrides[get_current_active_user] = _fake_active_user
    return TestClient(app), manager, repository


# Admin-gated management + settings routes.
GATED = [
    ("get", "/api/plugins", None),
    ("get", "/api/plugins/p1", None),
    ("post", "/api/plugins/p1/enable", None),
    ("post", "/api/plugins/p1/disable", None),
    ("delete", "/api/plugins/p1", None),
    ("post", "/api/plugins/scan", None),
    ("get", "/api/plugins/p1/settings", None),
    ("put", "/api/plugins/p1/settings", {"settings": {"k": "v"}}),
]

# Routes the normal UI needs for every authenticated user.
OPEN = [
    "/api/plugins/pages",
    "/api/plugins/sidebar",
    "/api/plugins/quick-actions",
    "/api/plugins/frontend-extensions",
    "/api/plugins/sidebar-widgets",
    "/api/plugins/hooks/frontend",
]


@pytest.mark.parametrize("method,path,body", GATED)
def test_management_routes_denied_for_regular_user(method, path, body):
    client, _, _ = _make_client(_user(AccountType.USER))
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code == 403


@pytest.mark.parametrize("path", OPEN)
def test_read_routes_open_to_regular_user(path):
    client, _, _ = _make_client(_user(AccountType.USER))
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_settings_routes_ignore_caller_supplied_user_id():
    """A user_id query param must not reach the repository - the authenticated
    principal (global/None here) is the only identity used."""
    from src.features.plugins.records import Plugin

    client, _, repository = _make_client(_user(AccountType.ADMIN))
    repository.get_plugin_by_id.return_value = Plugin(
        id="p1", name="P1", version="1.0.0", type="full-stack",
        enabled=True, manifest_path="/content/plugins/local/p1/manifest.yml",
    )
    repository.get_plugin_settings.return_value = []

    response = client.get("/api/plugins/p1/settings?user_id=someone-else")

    assert response.status_code == 200
    # The route calls the repository with user_id=None regardless of the query.
    repository.get_plugin_settings.assert_called_once_with("p1", None)
