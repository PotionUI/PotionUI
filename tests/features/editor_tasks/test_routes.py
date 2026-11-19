from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.editor_tasks.formats import ExpandFormats
from src.features.editor_tasks.routes import build_router
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.features.editor_tasks.test_manager import build
from tests.features.editor_tasks.support import CROP, MASK
from tests.platform.security.test_current_user_keys import FakeResolver

READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
GENERATE_KEY = "pui_" + "g" * 40 + "ZZZZZZ"
OTHER_KEY = "pui_" + "o" * 40 + "ZZZZZZ"
SESSION = "a.session.jwt"


def account(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})
    app = FastAPI()
    app.include_router(
        build_router(
            SimpleNamespace(
                editor_tasks=manager,
                expand_formats=ExpandFormats(lambda: [{"id": "team", "label": "Team", "ratio": "5:2"}], lambda user_id: []),
            )
        )
    )
    resolver = FakeResolver()
    resolver.add(READ_KEY, account("u1"), [S.READ])
    resolver.add(GENERATE_KEY, account("u1"), [S.GENERATE])
    resolver.add(OTHER_KEY, account("u2"), [S.READ])
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: account("u1") if token == SESSION else None
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(resolver)
    current_user._key_failures.reset("testclient")
    with TestClient(app) as test_client:
        yield test_client
    current_user._auth, current_user._key_resolver = previous


def offers(response, task_id="edit_region"):
    tasks = response.json()["data"]["tasks"]
    return next(item for item in tasks if item["id"] == task_id)["offers"]


def test_a_session_gets_its_offers_with_a_null_default(client):
    response = client.get("/api/editor/tasks?editor=image", headers=bearer(SESSION))

    assert response.status_code == 200
    tasks = response.json()["data"]["tasks"]
    assert {"edit_region", "remove_object", "fix_faces", "upscale"} <= {item["id"] for item in tasks}
    assert all(item["default"] is None for item in tasks)
    assert [o["preset_id"] for o in offers(response)] == ["A-CROP", "B-MASK"]


def test_the_expand_task_says_it_grows_the_canvas_and_asks_for_no_region(client):
    response = client.get("/api/editor/tasks?editor=image", headers=bearer(SESSION))

    expand = next(item for item in response.json()["data"]["tasks"] if item["id"] == "expand")
    assert expand["grows"] is True and expand["region"] == "none" and expand["prompt"] == "optional"
    assert expand["fallback"]["task"] == "edit_region"
    assert next(item for item in response.json()["data"]["tasks"] if item["id"] == "edit_region")["grows"] is False


def test_the_formats_route_lists_the_built_in_formats_then_the_house_ones(client):
    response = client.get("/api/editor/expand/formats", headers=bearer(SESSION))

    assert response.status_code == 200
    formats = response.json()["data"]["formats"]
    assert [item["id"] for item in formats] == ["square", "portrait", "story", "landscape", "wide_banner", "house.team"]
    assert formats[2] == {"id": "story", "label": "Story", "ratio": "9:16", "source": "built_in", "group_id": None}
    assert formats[-1]["source"] == "house"


def test_the_formats_route_needs_a_login(client):
    assert client.get("/api/editor/expand/formats").status_code in (401, 403)


def test_the_editor_defaults_to_image(client):
    assert client.get("/api/editor/tasks", headers=bearer(SESSION)).status_code == 200


def test_an_unknown_editor_is_refused(client):
    assert client.get("/api/editor/tasks?editor=hologram", headers=bearer(SESSION)).status_code == 422


def test_a_key_with_read_is_allowed_and_sees_only_its_owners_presets(client):
    response = client.get("/api/editor/tasks", headers=bearer(OTHER_KEY))

    assert response.status_code == 200
    assert [o["preset_id"] for o in offers(response)] == ["B-MASK"]


def test_a_key_without_read_is_refused(client):
    assert client.get("/api/editor/tasks", headers=bearer(GENERATE_KEY)).status_code == 403


def test_an_anonymous_call_is_refused(client):
    assert client.get("/api/editor/tasks").status_code in (401, 403)
