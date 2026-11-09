from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.presets.routes import PresetController, build_router
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

PRESET = "family-variant"

READ_ROUTES = [
    ("get", f"/api/presets/{PRESET}", "get_preset", None),
    ("get", f"/api/presets/{PRESET}/models", "get_preset_models", None),
    ("get", f"/api/presets/{PRESET}/modes", "get_available_modes", None),
    ("get", f"/api/presets/{PRESET}/form", "get_form_schema", None),
    ("get", f"/api/presets/{PRESET}/pipes", "get_pipeline", None),
    ("post", f"/api/presets/{PRESET}/pipes", "get_pipeline", {"form_data": {}}),
]


def _user(user_id, account_type=AccountType.USER):
    return User(id=user_id, username=user_id, email=f"{user_id}@x.test", password_hash="h", account_type=account_type)


def _assignments(per_user):
    repo = Mock()
    repo.get_available_preset_ids_for_user.side_effect = lambda user_id: per_user.get(user_id, [])
    return repo


def _client(user, repo, file_repo=None):
    collaborators = SimpleNamespace(db_repo=repo, file_repo=file_repo or Mock())
    controller = PresetController(collaborators, Mock())
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(preset_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app), controller


def _call(client, method, url, body):
    return client.request(method.upper(), url, json=body) if body is not None else client.request(method.upper(), url)


@pytest.mark.parametrize("method,url,handler,body", READ_ROUTES)
def test_unassigned_user_gets_404_and_the_handler_never_runs(method, url, handler, body):
    client, controller = _client(_user("alice"), _assignments({"alice": ["family-other"]}))
    setattr(controller, handler, AsyncMock(return_value=APIResponse(success=True, data={})))

    response = _call(client, method, url, body)

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "preset_not_found"
    getattr(controller, handler).assert_not_called()


@pytest.mark.parametrize("method,url,handler,body", READ_ROUTES)
@pytest.mark.parametrize("assigned_to", ["direct", "group"])
def test_assigned_user_reaches_the_handler(method, url, handler, body, assigned_to):
    client, controller = _client(_user("alice"), _assignments({"alice": [PRESET]}))
    setattr(controller, handler, AsyncMock(return_value=APIResponse(success=True, data={})))

    response = _call(client, method, url, body)

    assert response.status_code == 200
    getattr(controller, handler).assert_awaited_once()


@pytest.mark.parametrize("method,url,handler,body", READ_ROUTES)
def test_admin_reaches_the_handler_without_an_assignment(method, url, handler, body):
    repo = _assignments({})
    client, controller = _client(_user("root", AccountType.ADMIN), repo)
    setattr(controller, handler, AsyncMock(return_value=APIResponse(success=True, data={})))

    response = _call(client, method, url, body)

    assert response.status_code == 200
    repo.get_available_preset_ids_for_user.assert_not_called()


def _configured():
    file_repo = Mock()
    file_repo.find_preset_by_id.return_value = SimpleNamespace(configuration={})
    return file_repo


def test_configuration_read_is_refused_for_a_regular_user_even_when_assigned():
    repo = _assignments({"alice": [PRESET]})
    repo.get_preset_configuration.return_value = {}
    client, _ = _client(_user("alice"), repo, _configured())

    body = client.get(f"/api/presets/{PRESET}/configuration").json()

    assert body["success"] is False
    assert body["error"] == "permission_denied"
    repo.get_preset_configuration.assert_not_called()


def test_configuration_read_is_allowed_for_an_admin():
    repo = _assignments({})
    repo.get_preset_configuration.return_value = {}
    client, _ = _client(_user("root", AccountType.ADMIN), repo, _configured())

    body = client.get(f"/api/presets/{PRESET}/configuration").json()

    assert body["success"] is True
    assert body["data"]["preset_id"] == PRESET
