from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.errors import register_error_handlers
from src.features.editor_tasks.routes import build_router
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.editor_tasks.test_defaults import World
from tests.platform.security.test_current_user_keys import FakeResolver

SESSION = "a.session.jwt"
OTHER_SESSION = "b.session.jwt"
READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
CONTENT_KEY = "pui_" + "c" * 40 + "ZZZZZZ"
GENERATE_KEY = "pui_" + "g" * 40 + "ZZZZZZ"
URL = "/api/editor/tasks/edit_region/default"
BODY = {"preset_id": "A-CROP", "mode": "edit", "variant": None, "setup": {"kind": "defaults"}}


def account(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api(mock_db, tmp_path):
    world = World(tmp_path)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(build_router(SimpleNamespace(editor_tasks=world.manager)))
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
        yield SimpleNamespace(client=client, world=world)
    current_user._auth, current_user._key_resolver = previous


def test_put_answers_the_task_entry_with_the_default(api):
    response = api.client.put(URL, headers=bearer(SESSION), json=BODY)

    assert response.status_code == 200
    task = response.json()["data"]["task"]
    assert task["id"] == "edit_region" and task["offers"]
    assert task["default"]["setup"] == {"kind": "defaults", "id": None, "name": None}
    assert task["default"]["health"] == {"ok": True}


def test_the_listing_carries_the_default_and_delete_clears_it(api):
    api.client.put(URL, headers=bearer(SESSION), json=BODY)

    listed = api.client.get("/api/editor/tasks", headers=bearer(SESSION)).json()["data"]["tasks"]
    assert next(t for t in listed if t["id"] == "edit_region")["default"]["preset_id"] == "A-CROP"
    assert next(t for t in listed if t["id"] == "remove_object")["default"] is None

    response = api.client.delete(URL, headers=bearer(SESSION))

    assert response.status_code == 200 and response.json()["data"]["task"]["default"] is None
    assert api.world.stored() == {}


def test_a_refusal_carries_its_code_and_writes_nothing(api):
    response = api.client.put(URL, headers=bearer(SESSION), json={**BODY, "setup": {"kind": "session", "id": "ghost"}})

    assert response.status_code == 404
    assert "default_session_not_found" in response.text
    assert api.world.stored() == {}


def test_a_mode_that_does_not_offer_the_task_answers_422(api):
    assert api.client.put(URL, headers=bearer(SESSION), json={**BODY, "mode": "other"}).status_code == 422


def test_a_malformed_setup_answers_422(api):
    assert api.client.put(URL, headers=bearer(SESSION), json={**BODY, "setup": {"kind": "session"}}).status_code == 422


def test_an_unassigned_preset_answers_404_for_that_user(api):
    assert api.client.put(URL, headers=bearer(OTHER_SESSION), json=BODY).status_code == 404


def test_an_unknown_task_answers_404(api):
    assert api.client.put("/api/editor/tasks/nope/default", headers=bearer(SESSION), json=BODY).status_code == 404
    assert api.client.delete("/api/editor/tasks/nope/default", headers=bearer(SESSION)).status_code == 404


def test_another_users_default_is_not_cleared_by_their_delete(api):
    api.client.put(URL, headers=bearer(SESSION), json=BODY)

    api.client.delete(URL, headers=bearer(OTHER_SESSION))

    assert "edit_region" in api.world.stored()


def test_setting_a_default_needs_the_content_scope_and_reading_needs_read(api):
    assert api.client.put(URL, headers=bearer(READ_KEY), json=BODY).status_code == 403
    assert api.client.put(URL, headers=bearer(GENERATE_KEY), json=BODY).status_code == 403
    assert api.client.delete(URL, headers=bearer(READ_KEY)).status_code == 403
    assert api.world.stored() == {}

    assert api.client.put(URL, headers=bearer(CONTENT_KEY), json=BODY).status_code == 200
    assert api.client.get("/api/editor/tasks", headers=bearer(READ_KEY)).status_code == 200
    assert api.client.delete(URL, headers=bearer(CONTENT_KEY)).status_code == 200
