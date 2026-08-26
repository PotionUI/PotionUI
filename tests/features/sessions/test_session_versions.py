"""
Tests for session history / versions.

`SessionVersionRepository` runs real SQL against a real (in-memory) SQLite
connection so the cascade-delete FK and the pruning `DELETE ... NOT IN`
subquery are actually exercised, not assumed. Both `src.features.sessions.
repository` and `src.features.sessions.version_repository` import
`get_database_connection` by name at import time, so each module's OWN
attribute is patched directly (mirrors `tests/features/stats/
test_generation_stats_repository.py`'s documented reasoning for why patching
`src.platform.database.database.db` would not reach them).
"""
import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

import src.features.sessions.repository as session_repository_module
import src.features.sessions.version_repository as version_repository_module
from src.features.sessions.dto import Session as SessionDTO, SaveSessionRequest, UpdateSessionRequest
from src.features.sessions.manager import SessionManager
from src.features.sessions.routes import SessionController
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import (
    SESSION_VERSION_RETENTION_LIMIT,
    SessionVersionRepository,
)

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
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE(user_id, preset_id, name)
);

CREATE TABLE session_versions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    payload TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
    UNIQUE (session_id, version_number)
);
"""


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    connection.execute("INSERT INTO users (id) VALUES ('user-1')")
    connection.execute("INSERT INTO users (id) VALUES ('user-2')")
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def repos(conn):
    """Real SessionRepository + SessionVersionRepository sharing `conn`."""

    @contextmanager
    def _get_conn():
        yield conn

    with patch.object(session_repository_module, "get_database_connection", _get_conn), \
         patch.object(version_repository_module, "get_database_connection", _get_conn):
        yield SessionRepository(), SessionVersionRepository()


_next_id = iter(f"session-{i}" for i in range(1, 10_000))


def _make_session(session_repo, user_id="user-1", preset_id="preset-1", name="My Session", data=None):
    # The Pydantic `Session` DTO requires `id: str`, and SessionRepository.
    # create() uses whatever id is given verbatim (it only mints a fresh one
    # when the DTO's id is falsy) — so give each fixture-created session a
    # distinct id up front.
    session = SessionDTO(
        id=next(_next_id),
        user_id=user_id,
        preset_id=preset_id,
        name=name,
        data=data if data is not None else {"prompt": "a cat"},
    )
    return session_repo.create(session)


class TestSessionVersionRepository:
    """Direct repository behavior: numbering, pruning, list/get shapes, cascade."""

    def test_create_first_version_is_number_one(self, repos):
        session_repo, version_repo = repos
        session = _make_session(session_repo)

        version = version_repo.create(session.id, {"prompt": "v1"}, "SDXL Portrait")

        assert version.version_number == 1
        assert version.data == {"prompt": "v1"}
        assert version.summary == "SDXL Portrait"

    def test_create_increments_monotonically(self, repos):
        session_repo, version_repo = repos
        session = _make_session(session_repo)

        v1 = version_repo.create(session.id, {"n": 1}, "P")
        v2 = version_repo.create(session.id, {"n": 2}, "P")
        v3 = version_repo.create(session.id, {"n": 3}, "P")

        assert [v1.version_number, v2.version_number, v3.version_number] == [1, 2, 3]

    def test_numbering_is_independent_per_session(self, repos):
        session_repo, version_repo = repos
        session_a = _make_session(session_repo, name="A")
        session_b = _make_session(session_repo, name="B")

        version_repo.create(session_a.id, {}, "P")
        v_b1 = version_repo.create(session_b.id, {}, "P")

        assert v_b1.version_number == 1

    def test_list_for_session_is_newest_first_and_excludes_payload(self, repos):
        session_repo, version_repo = repos
        session = _make_session(session_repo)
        version_repo.create(session.id, {"n": 1}, "P1")
        version_repo.create(session.id, {"n": 2}, "P2")

        versions = version_repo.list_for_session(session.id)

        assert [v.version_number for v in versions] == [2, 1]
        assert all(v.data == {} for v in versions)
        assert versions[0].summary == "P2"

    def test_get_returns_full_payload(self, repos):
        session_repo, version_repo = repos
        session = _make_session(session_repo)
        version_repo.create(session.id, {"prompt": "hello world"}, "P")

        version = version_repo.get(session.id, 1)

        assert version is not None
        assert version.data == {"prompt": "hello world"}

    def test_get_unknown_version_returns_none(self, repos):
        session_repo, version_repo = repos
        session = _make_session(session_repo)

        assert version_repo.get(session.id, 999) is None

    def test_pruning_keeps_only_retention_cap_newest(self, repos, monkeypatch):
        session_repo, version_repo = repos
        monkeypatch.setattr(version_repository_module, "SESSION_VERSION_RETENTION_LIMIT", 3)
        session = _make_session(session_repo)

        for n in range(1, 6):  # 5 saves, cap is 3
            version_repo.create(session.id, {"n": n}, "P")

        versions = version_repo.list_for_session(session.id)

        assert [v.version_number for v in versions] == [5, 4, 3]

    def test_retention_default_constant_is_fifty(self):
        assert SESSION_VERSION_RETENTION_LIMIT == 50

    def test_cascade_delete_removes_versions(self, repos, conn):
        session_repo, version_repo = repos
        session = _make_session(session_repo)
        version_repo.create(session.id, {"n": 1}, "P")
        version_repo.create(session.id, {"n": 2}, "P")

        assert session_repo.delete(session.id) is True

        assert version_repo.list_for_session(session.id) == []
        row = conn.execute("SELECT COUNT(*) FROM session_versions WHERE session_id = ?", (session.id,)).fetchone()
        assert row[0] == 0


class TestSessionManagerVersionHistory:
    """SessionManager wiring: version-on-save, dedup, owner gating."""

    @pytest.fixture
    def mock_plugin_registry(self):
        registry = Mock()
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def mock_file_preset_repository(self):
        repo = Mock()
        preset = Mock()
        preset.name = "SDXL Portrait"
        repo.find_preset_by_id.return_value = preset
        return repo

    @pytest.fixture
    def manager(self, repos, mock_plugin_registry, mock_file_preset_repository):
        session_repo, version_repo = repos
        return SessionManager(
            session_repository=session_repo,
            plugin_registry=mock_plugin_registry,
            session_version_repository=version_repo,
            file_preset_repository=mock_file_preset_repository,
        ), version_repo

    @pytest.fixture
    def controller(self, repos, manager):
        """list_session_versions/get_session_version are pure DB reads and
        live on SessionController (repository-backed), not on SessionManager."""
        session_repo, version_repo = repos
        mgr, _ = manager
        return SessionController(
            session_manager=mgr,
            session_repository=session_repo,
            session_version_repository=version_repo,
        )

    def test_save_new_session_creates_version_one(self, manager):
        mgr, version_repo = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})

        result, _ = mgr.save_session("user-1", request)

        versions = version_repo.list_for_session(result["id"])
        assert [v.version_number for v in versions] == [1]
        assert versions[0].summary == "SDXL Portrait"

    def test_save_again_with_changed_data_creates_version_two(self, manager):
        mgr, version_repo = manager
        first = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", first)

        second = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "b"})
        mgr.save_session("user-1", second)

        versions = version_repo.list_for_session(result["id"])
        assert [v.version_number for v in versions] == [2, 1]

    def test_save_again_with_identical_data_does_not_duplicate_version(self, manager):
        mgr, version_repo = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        # Re-save with byte-identical data (a no-op save, e.g. hitting Save
        # twice without changes) must not append a duplicate version.
        mgr.save_session("user-1", request)

        versions = version_repo.list_for_session(result["id"])
        assert [v.version_number for v in versions] == [1]

    def test_restoring_then_saving_becomes_newest_version(self, manager):
        """Simulates the "go back, then Save" flow: the UI has no
        restore endpoint, it just re-saves the old payload — which must land
        as a new, newest version rather than being deduped against the
        version that's currently in between.
        """
        mgr, version_repo = manager
        v1_request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "v1"})
        result, _ = mgr.save_session("user-1", v1_request)

        v2_request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "v2"})
        mgr.save_session("user-1", v2_request)

        # User goes back to v1's payload and hits Save again.
        mgr.save_session("user-1", v1_request)

        versions = version_repo.list_for_session(result["id"])
        assert [v.version_number for v in versions] == [3, 2, 1]
        assert version_repo.get(result["id"], 3).data == {"prompt": "v1"}

    def test_update_session_by_id_appends_version(self, manager):
        mgr, version_repo = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        mgr.update_session(
            "user-1",
            result["id"],
            UpdateSessionRequest(name="S", data={"prompt": "renamed-data"}),
        )

        versions = version_repo.list_for_session(result["id"])
        assert [v.version_number for v in versions] == [2, 1]

    @pytest.mark.asyncio
    async def test_list_session_versions_shape_has_no_payload(self, manager, controller):
        mgr, _ = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        response = await controller.list_session_versions("user-1", result["id"])

        assert len(response.data) == 1
        assert set(response.data[0].keys()) == {"version_number", "created_at", "summary"}

    @pytest.mark.asyncio
    async def test_get_session_version_shape_has_payload(self, manager, controller):
        mgr, _ = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        response = await controller.get_session_version("user-1", result["id"], 1)

        assert response.data["data"] == {"prompt": "a"}
        assert set(response.data.keys()) == {"version_number", "created_at", "summary", "data"}

    @pytest.mark.asyncio
    async def test_list_session_versions_owner_gating_is_404_style(self, manager, controller):
        from fastapi import HTTPException

        mgr, _ = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        with pytest.raises(HTTPException) as exc_info:
            await controller.list_session_versions("user-2", result["id"])

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_get_session_version_owner_gating_is_404_style(self, manager, controller):
        from fastapi import HTTPException

        mgr, _ = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_session_version("user-2", result["id"], 1)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_get_session_version_unknown_number_raises(self, manager, controller):
        from fastapi import HTTPException

        mgr, _ = manager
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        with pytest.raises(HTTPException) as exc_info:
            await controller.get_session_version("user-1", result["id"], 999)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "session_version_not_found"

    @pytest.mark.asyncio
    async def test_list_session_versions_missing_session_raises(self, controller):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await controller.list_session_versions("user-1", "does-not-exist")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "session_not_found"


class TestSessionManagerWithoutVersionRepository:
    """Backward compatibility: existing callers building a bare SessionManager
    (no version_repository/file_preset_repository) must keep working exactly
    as before — version history is simply a no-op.
    """

    @pytest.fixture
    def mock_plugin_registry(self):
        registry = Mock()
        context = Mock()
        context.data = {}
        registry.execute_hook.return_value = (context, [])
        return registry

    def test_save_without_version_repository_does_not_raise(self, repos, mock_plugin_registry):
        session_repo, _version_repo = repos
        mgr = SessionManager(session_repository=session_repo, plugin_registry=mock_plugin_registry)

        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, message = mgr.save_session("user-1", request)

        assert result["name"] == "S"
        assert "saved successfully" in message

    @pytest.mark.asyncio
    async def test_list_session_versions_without_repository_returns_empty(self, repos, mock_plugin_registry):
        session_repo, _version_repo = repos
        mgr = SessionManager(session_repository=session_repo, plugin_registry=mock_plugin_registry)
        controller = SessionController(session_manager=mgr, session_repository=session_repo)
        request = SaveSessionRequest(preset_id="preset-1", name="S", data={"prompt": "a"})
        result, _ = mgr.save_session("user-1", request)

        response = await controller.list_session_versions("user-1", result["id"])

        assert response.data == []
