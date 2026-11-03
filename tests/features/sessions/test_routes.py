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
    version INTEGER NOT NULL DEFAULT 1,
    last_origin TEXT,
    UNIQUE(user_id, preset_id, name)
);
"""


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
def controller(repo, registry):
    return SessionController(session_repository=repo, plugin_registry=registry)


def _save(repo, registry, user_id, name):
    request = SaveSessionRequest(preset_id="preset-1", name=name, data={"prompt": name})
    result, _ = operations.save_session(repo, registry, None, None, user_id, request)
    return result["id"]


@pytest.mark.asyncio
async def test_get_missing_and_foreign_sessions_use_distinct_soft_codes(controller, repo, registry):
    mine = _save(repo, registry, "user-1", "A")

    missing = await controller.get_session_by_id("user-1", "nope")
    foreign = await controller.get_session_by_id("user-2", mine)

    assert (missing.success, missing.error) == (False, "session_not_found")
    assert (foreign.success, foreign.error) == (False, "session_access_denied")


@pytest.mark.asyncio
async def test_update_and_delete_map_missing_foreign_and_duplicate_names(controller, repo, registry):
    first = _save(repo, registry, "user-1", "A")
    _save(repo, registry, "user-1", "B")

    missing = await controller.update_session("user-1", "nope", UpdateSessionRequest(name="C", data={}))
    foreign = await controller.update_session("user-2", first, UpdateSessionRequest(name="C", data={}))
    duplicate = await controller.update_session("user-1", first, UpdateSessionRequest(name="B", data={}))
    deleted_missing = await controller.delete_session("user-1", "nope")
    deleted_foreign = await controller.delete_session("user-2", first)

    assert missing.error == "session_not_found"
    assert foreign.error == "session_access_denied"
    assert duplicate.error == "session_name_exists"
    assert deleted_missing.error == "session_not_found"
    assert deleted_foreign.error == "session_access_denied"
    assert not any(r.success for r in (missing, foreign, duplicate, deleted_missing, deleted_foreign))


@pytest.mark.asyncio
async def test_version_lookups_answer_a_real_404_with_their_code(controller, repo, registry):
    mine = _save(repo, registry, "user-1", "A")

    with pytest.raises(HTTPException) as no_session:
        await controller.get_session_version("user-2", mine, 1)
    with pytest.raises(HTTPException) as no_version:
        await controller.get_session_version("user-1", mine, 99)

    assert no_session.value.status_code == 404
    assert no_session.value.detail["error"] == "session_not_found"
    assert no_version.value.status_code == 404
    assert no_version.value.detail["error"] == "session_version_not_found"
    assert controller.version_repository is None

    with pytest.raises(HTTPException) as pin:
        await controller.set_session_pinned("user-2", mine, PinSessionRequest(pinned=True))
    assert pin.value.status_code == 404
