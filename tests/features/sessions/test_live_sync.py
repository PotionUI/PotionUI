import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.features.sessions.repository as session_repository_module
import src.features.sessions.routes as session_routes
from src.features.sessions import operations
from src.features.sessions.dto import Session, UpdateSessionRequest
from src.features.sessions.live import WriteOrigin
from src.features.sessions.repository import SessionRepository
from src.features.sessions.routes import SessionController, build_router
from src.platform.security.user import AccountType, User

_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    preset_id TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pinned INTEGER NOT NULL DEFAULT 0,
    pinned_at TIMESTAMP,
    version INTEGER NOT NULL DEFAULT 1,
    last_origin TEXT,
    UNIQUE(user_id, preset_id, name)
);
"""

JWT = "browser-jwt"
MCP = "pui_mcp_secret"


def _user(user_id="user-1"):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


class _Notifier:
    def __init__(self):
        self.sent = []

    def schedule_send(self, user_id, message):
        self.sent.append((user_id, message))


@pytest.fixture
def repo():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.commit()

    @contextmanager
    def _get_conn():
        yield connection

    with patch.object(session_repository_module, "get_database_connection", _get_conn):
        yield SessionRepository()
    connection.close()


@pytest.fixture
def registry():
    plugin_registry = Mock()
    context = Mock()
    context.data = {}
    plugin_registry.execute_hook.return_value = (context, [])
    return plugin_registry


@pytest.fixture
def notifier():
    return _Notifier()


@pytest.fixture
def controller(repo, registry, notifier):
    owner = (_user(), SimpleNamespace(name="Claude Code"))
    return SessionController(
        session_repository=repo,
        plugin_registry=registry,
        notifier=notifier,
        mcp_owner_resolver=lambda token: owner if token == MCP else None,
    )


@pytest.fixture
def client(controller):
    auth = SimpleNamespace(get_user_from_token=lambda token: _user() if token == JWT else None)
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(session_controller=controller)))
    with patch.object(session_routes, "get_auth", lambda: auth):
        yield TestClient(app, raise_server_exceptions=False)


def _headers(token=JWT, client_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if client_id:
        headers["X-Potion-Client"] = client_id
    return headers


def _create(client, name="Monica", data=None):
    response = client.post(
        "/api/sessions/save",
        json={"preset_id": "krea2", "name": name, "data": data or {"txt2img": {"prompt": "a"}}},
        headers=_headers(),
    )
    assert response.status_code == 200
    return response.json()["data"]


def _put(client, session_id, data, base_version=None, token=JWT, client_id=None):
    body = {"name": "Monica", "data": data}
    if base_version is not None:
        body["base_version"] = base_version
    return client.put(f"/api/sessions/{session_id}", json=body, headers=_headers(token, client_id))


def test_a_new_session_starts_at_version_one(client):
    assert _create(client)["version"] == 1


def test_a_write_on_the_current_version_bumps_it(client):
    created = _create(client)

    response = _put(client, created["id"], {"txt2img": {"prompt": "b"}}, base_version=1)

    assert response.status_code == 200
    assert response.json()["data"]["version"] == 2


def test_a_stale_write_is_refused_with_the_current_session(client):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "from claude"}}, token=MCP)

    response = _put(client, created["id"], {"txt2img": {"prompt": "stale tab"}}, base_version=1, client_id="w1:tab-a")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "session_version_conflict"
    assert detail["session"]["version"] == 2
    assert detail["session"]["data"] == {"txt2img": {"prompt": "from claude"}}
    assert detail["origin"] == {"kind": "mcp", "label": "Claude Code"}
    assert detail["client_id"] is None


def test_a_stale_write_never_reaches_the_stored_session(client, repo):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "kept"}})

    _put(client, created["id"], {"txt2img": {"prompt": "lost"}}, base_version=1)

    stored = repo.get_by_id(created["id"])
    assert stored.data == {"txt2img": {"prompt": "kept"}}
    assert stored.version == 2


def test_a_write_without_a_base_stays_last_writer_wins(client):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "b"}}, base_version=1)

    response = _put(client, created["id"], {"txt2img": {"prompt": "old script"}})

    assert response.status_code == 200
    assert response.json()["data"]["version"] == 3
    assert response.json()["data"]["data"] == {"txt2img": {"prompt": "old script"}}


def test_save_by_name_refuses_a_stale_base_too(client):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "b"}})

    response = client.post(
        "/api/sessions/save",
        json={"preset_id": "krea2", "name": "Monica", "data": {"prompt": "c"}, "mode": "txt2img", "base_version": 1},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["session"]["version"] == 2


def _pushes(notifier):
    return [message for _user_id, message in notifier.sent if message["type"] == "session_changed"]


def test_every_write_path_announces_the_new_version(client, notifier):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "b"}}, base_version=1)
    _put(client, created["id"], {"txt2img": {"prompt": "c"}})
    client.post(
        "/api/sessions/save",
        json={"preset_id": "krea2", "name": "Monica", "data": {"prompt": "d"}, "mode": "txt2img"},
        headers=_headers(),
    )

    assert [(m["session_id"], m["version"]) for m in _pushes(notifier)] == [
        (created["id"], 1),
        (created["id"], 2),
        (created["id"], 3),
        (created["id"], 4),
    ]
    assert {user_id for user_id, _ in notifier.sent} == {"user-1"}


def test_a_refused_write_announces_nothing(client, notifier):
    created = _create(client)
    _put(client, created["id"], {"txt2img": {"prompt": "b"}})
    before = len(_pushes(notifier))

    _put(client, created["id"], {"txt2img": {"prompt": "stale"}}, base_version=1)

    assert len(_pushes(notifier)) == before


def test_the_origin_names_the_mcp_token(client, notifier):
    created = _create(client)

    _put(client, created["id"], {"txt2img": {"prompt": "b"}}, token=MCP)

    push = _pushes(notifier)[-1]
    assert push["origin"] == {"kind": "mcp", "label": "Claude Code"}
    assert push["client_id"] is None


def test_a_browser_tab_write_carries_its_client_id_for_the_echo(client, notifier):
    created = _create(client)

    _put(client, created["id"], {"txt2img": {"prompt": "b"}}, client_id="w1:tab-a")

    push = _pushes(notifier)[-1]
    assert push["origin"] == {"kind": "window", "label": "another window"}
    assert push["client_id"] == "w1:tab-a"


def test_a_write_with_no_client_id_is_labelled_api(client, notifier):
    created = _create(client)

    _put(client, created["id"], {"txt2img": {"prompt": "b"}})

    assert _pushes(notifier)[-1]["origin"] == {"kind": "api", "label": "API"}


def test_a_malformed_client_id_is_not_echoed(client, notifier):
    created = _create(client)

    _put(client, created["id"], {"txt2img": {"prompt": "b"}}, client_id="bad id with spaces")

    push = _pushes(notifier)[-1]
    assert push["client_id"] is None
    assert push["origin"]["kind"] == "api"


def test_an_unknown_bearer_is_unauthorized(client):
    created = _create(client)

    assert _put(client, created["id"], {"txt2img": {"prompt": "b"}}, token="nope").status_code == 401


def test_an_mcp_token_reads_the_session_with_its_version(client):
    created = _create(client)

    response = client.get(f"/api/sessions/{created['id']}", headers=_headers(MCP))

    assert response.status_code == 200
    assert response.json()["data"]["version"] == 1


def test_the_repository_refuses_a_swap_against_a_moved_version(repo):
    session = repo.create(Session(id="s1", user_id="user-1", preset_id="p", name="n", data={"a": 1}))
    assert repo.update(session.model_copy(update={"data": {"a": 2}}), 1, None).version == 2

    assert repo.update(session.model_copy(update={"data": {"a": 3}}), 1, None) is None
    assert repo.get_by_id("s1").data == {"a": 2}


def test_a_baseless_write_retries_when_another_writer_lands_in_between(repo, registry):
    session = repo.create(Session(id="s1", user_id="user-1", preset_id="p", name="n", data={}))
    real_update = repo.update
    interleaved = []

    def update(candidate, expected_version, origin=None):
        if not interleaved:
            interleaved.append(True)
            real_update(session.model_copy(update={"data": {"img2img": {"prompt": "other"}}}), expected_version, None)
        return real_update(candidate, expected_version, origin)

    repo.update = update
    result = operations.update_session(
        repo, registry, None, None, "user-1", "s1",
        UpdateSessionRequest(name="n", data={"prompt": "mine"}, mode="txt2img"),
        WriteOrigin("api", "API"),
    )

    assert result["version"] == 3
    assert result["data"] == {"img2img": {"prompt": "other"}, "txt2img": {"prompt": "mine"}}
