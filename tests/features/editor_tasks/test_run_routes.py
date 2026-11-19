from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.errors import register_error_handlers
from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.routes import build_runs_router
from src.features.plans.errors import LimitExceeded
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.editor_tasks.run_support import build_manager, make_edit, make_users
from tests.platform.security.test_current_user_keys import FakeResolver

SESSION = "a.session.jwt"
OTHER_SESSION = "b.session.jwt"
READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
CONTENT_KEY = "pui_" + "c" * 40 + "ZZZZZZ"
GENERATE_KEY = "pui_" + "g" * 40 + "ZZZZZZ"


def account(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(build_runs_router(SimpleNamespace(editor_runs=manager, editor_detection=None)))
    resolver = FakeResolver()
    resolver.add(READ_KEY, account("u1"), [S.READ])
    resolver.add(CONTENT_KEY, account("u1"), [S.CONTENT])
    resolver.add(GENERATE_KEY, account("u1"), [S.GENERATE])
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: {SESSION: account("u1"), OTHER_SESSION: account("u2")}.get(token)
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(resolver)
    current_user._key_failures.reset("testclient")
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, manager=manager, fakes=fakes, edit_id=make_edit().id)
    current_user._auth, current_user._key_resolver = previous


def payload(edit_id, **extra):
    data = {
        "edit": {"id": edit_id, "step_id": "s1"},
        "input": {"item_id": "picture"},
        "region": {"item_id": "region"},
        "prompt": "a scarf",
        "setup": {"preset_id": "A-CROP", "mode": "editor:edit", "form_data": {}},
    }
    data.update(extra)
    return data


def start(api, token=SESSION, **extra):
    return api.client.post("/api/editor/tasks/edit_region/runs", headers=bearer(token), json=payload(api.edit_id, **extra))


def test_starting_a_run_answers_202_with_the_run(api):
    response = start(api)

    run = response.json()["data"]["run"]
    assert response.status_code == 202 and run["status"] == "queued" and run["generation_id"] == "g1"


def test_a_client_cannot_set_the_origin_through_the_run_body(api):
    start(api, origin={"kind": "editor", "edit_id": "forged"}, setup={"preset_id": "A-CROP", "mode": "editor:edit", "form_data": {"origin": {"x": 1}}})

    assert api.fakes.origins["g1"]["edit_id"] == api.edit_id
    assert api.fakes.origins["g1"]["run_id"].startswith("er_")
    assert "origin" not in StartEditorRunRequest.model_fields
    assert "origin" not in StartEditorRunRequest.model_validate(payload("ed_1", origin={"kind": "x"})).model_dump()


def test_another_users_run_answers_404(api):
    run = start(api).json()["data"]["run"]

    assert api.client.get(f"/api/editor/runs/{run['id']}", headers=bearer(OTHER_SESSION)).status_code == 404
    assert api.client.delete(f"/api/editor/runs/{run['id']}", headers=bearer(OTHER_SESSION)).status_code == 404
    assert api.client.get(f"/api/editor/runs/{run['id']}", headers=bearer(SESSION)).status_code == 200


def test_another_users_edit_answers_404(api):
    assert start(api, token=OTHER_SESSION).status_code == 404


def test_a_mode_that_does_not_offer_the_task_answers_422(api):
    response = api.client.post(
        "/api/editor/tasks/edit_region/runs", headers=bearer(SESSION),
        json=payload(api.edit_id, setup={"preset_id": "A-CROP", "mode": "other", "form_data": {}}),
    )

    assert response.status_code == 422


def test_delete_cancels_the_run(api):
    run = start(api).json()["data"]["run"]

    response = api.client.delete(f"/api/editor/runs/{run['id']}", headers=bearer(SESSION))

    assert response.json()["data"]["run"]["status"] == "cancelled" and api.fakes.cancelled == ["g1"]


def test_key_scopes_follow_the_generate_and_read_routes(api):
    url = "/api/editor/tasks/edit_region/runs"
    body = payload(api.edit_id)

    assert api.client.post(url, headers=bearer(READ_KEY), json=body).status_code == 403
    assert api.client.post(url, headers=bearer(CONTENT_KEY), json=body).status_code == 403
    assert api.client.post(url, headers=bearer(GENERATE_KEY), json=body).status_code == 202
    run_id = api.client.post(url, headers=bearer(GENERATE_KEY), json=body).json()["data"]["run"]["id"]
    assert api.client.get(f"/api/editor/runs/{run_id}", headers=bearer(READ_KEY)).status_code == 200
    assert api.client.delete(f"/api/editor/runs/{run_id}", headers=bearer(READ_KEY)).status_code == 403
    assert api.client.delete(f"/api/editor/runs/{run_id}", headers=bearer(GENERATE_KEY)).status_code == 200


def test_a_caller_over_their_limit_is_refused_like_a_normal_generation(api):
    api.fakes.submit_error = LimitExceeded(
        [{"message": "Daily limit reached", "code": "x", "kind": "generations", "label": "Generations", "format": "count", "used": 5, "limit": 5, "percent": 100, "resets_at": None}],
        "submit", None,
    )

    response = start(api)

    assert response.status_code == 403 and response.json()["detail"]["error"] == "limit_exceeded"
    assert api.manager.repository.by_generation("g1") is None


def test_a_step_reference_with_odd_characters_or_length_is_refused(api):
    assert start(api, edit={"id": api.edit_id, "step_id": "s1; DROP"}).status_code == 422
    assert start(api, edit={"id": api.edit_id, "step_id": "s" * 65}).status_code == 422
    assert start(api, edit={"id": api.edit_id, "step_id": "s7"}).status_code == 202
