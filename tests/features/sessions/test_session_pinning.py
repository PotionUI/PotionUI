import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

import src.features.sessions.repository as session_repository_module
from src.features.sessions import operations
from src.features.sessions.dto import PinSessionRequest, SaveSessionRequest, UpdateSessionRequest
from src.features.sessions.repository import SessionRepository
from src.features.sessions.routes import SessionController

_SCHEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY);

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
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE(user_id, preset_id, name)
);
"""


@pytest.fixture
def repo():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.execute("INSERT INTO users (id) VALUES ('user-1')")
    connection.execute("INSERT INTO users (id) VALUES ('user-2')")
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
def controller(repo, registry):
    return SessionController(session_repository=repo, plugin_registry=registry)


def _save(repo, registry, user_id, name):
    request = SaveSessionRequest(preset_id="preset-1", name=name, data={"prompt": name})
    result, _ = operations.save_session(repo, registry, None, None, user_id, request)
    return result["id"]


def _names(repo, user_id="user-1"):
    return [s.name for s in repo.get_by_user_and_preset(user_id, "preset-1")]


@pytest.mark.asyncio
async def test_new_sessions_are_unpinned_in_list_and_detail(controller, repo, registry):
    session_id = _save(repo, registry, "user-1", "A")

    listed = await controller.get_sessions_for_preset("user-1", "preset-1")
    detail = await controller.get_session_by_id("user-1", session_id)

    assert listed.data[0]["pinned"] is False
    assert detail.data["pinned"] is False


@pytest.mark.asyncio
async def test_pin_then_unpin_round_trips_through_the_controller(controller, repo, registry):
    session_id = _save(repo, registry, "user-1", "A")

    pinned = await controller.set_session_pinned("user-1", session_id, PinSessionRequest(pinned=True))
    detail = await controller.get_session_by_id("user-1", session_id)
    unpinned = await controller.set_session_pinned("user-1", session_id, PinSessionRequest(pinned=False))

    assert pinned.data["pinned"] is True
    assert detail.data["pinned"] is True
    assert unpinned.data["pinned"] is False
    assert repo.get_by_id(session_id).pinned_at is None


@pytest.mark.asyncio
async def test_pinning_does_not_touch_updated_at(controller, repo, registry):
    session_id = _save(repo, registry, "user-1", "A")
    before = repo.get_by_id(session_id).updated_at

    await controller.set_session_pinned("user-1", session_id, PinSessionRequest(pinned=True))

    assert repo.get_by_id(session_id).updated_at == before


@pytest.mark.asyncio
async def test_another_users_session_cannot_be_pinned_and_looks_missing(controller, repo, registry):
    session_id = _save(repo, registry, "user-1", "A")

    with pytest.raises(HTTPException) as foreign:
        await controller.set_session_pinned("user-2", session_id, PinSessionRequest(pinned=True))
    with pytest.raises(HTTPException) as missing:
        await controller.set_session_pinned("user-1", "nope", PinSessionRequest(pinned=True))

    assert foreign.value.status_code == 404
    assert missing.value.status_code == 404
    assert foreign.value.detail == missing.value.detail
    assert repo.get_by_id(session_id).pinned is False


def test_list_puts_pinned_first_then_the_rest_by_updated(repo, registry):
    ids = {name: _save(repo, registry, "user-1", name) for name in ["A", "B", "C", "D"]}
    repo.set_pinned(ids["A"], True)
    repo.set_pinned(ids["C"], True)

    names = _names(repo)

    assert names[:2] == ["C", "A"]
    assert set(names[2:]) == {"B", "D"}


def test_saving_a_pinned_session_keeps_it_pinned(repo, registry):
    session_id = _save(repo, registry, "user-1", "A")
    repo.set_pinned(session_id, True)

    updated = operations.update_session(
        repo, registry, None, None, "user-1", session_id,
        UpdateSessionRequest(name="A", data={"prompt": "edited"}),
    )
    resaved = _save(repo, registry, "user-1", "A")

    assert updated["pinned"] is True
    assert resaved == session_id
    assert repo.get_by_id(session_id).pinned is True
