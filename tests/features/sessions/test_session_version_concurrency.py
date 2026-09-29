import logging
import sqlite3
import threading
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

import src.features.sessions.repository as session_repository_module
import src.features.sessions.version_repository as version_repository_module
from src.features.sessions.dto import Session as SessionDTO, SaveSessionRequest
from src.features.sessions import operations
from src.features.sessions.repository import SessionRepository
from src.features.sessions.version_repository import SessionVersionRepository

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
def db_path(tmp_path):
    path = tmp_path / "scratch.sqlite"
    setup = sqlite3.connect(str(path))
    setup.executescript(_SCHEMA)
    setup.execute("INSERT INTO users (id) VALUES ('user-1')")
    setup.commit()
    setup.close()
    return path


@pytest.fixture
def repos(db_path, monkeypatch):
    @contextmanager
    def _get_conn():
        conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL").close()
        conn.execute("PRAGMA foreign_keys = ON").close()
        conn.execute("PRAGMA busy_timeout = 30000").close()
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(session_repository_module, "get_database_connection", _get_conn)
    monkeypatch.setattr(version_repository_module, "get_database_connection", _get_conn)
    return SessionRepository(), SessionVersionRepository()


def _run_concurrently(*targets):
    threads = [threading.Thread(target=t, daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads)


class TestSessionVersionConcurrency:
    def test_two_concurrent_saves_with_different_data_both_land(self, repos):
        session_repo, version_repo = repos
        session = session_repo.create(SessionDTO(id="s-1", user_id="user-1", preset_id="p-1", name="S1"))

        errors = []
        results = []
        lock = threading.Lock()

        def _save(n):
            try:
                version = version_repo.create_if_changed(session.id, {"n": n}, "P")
                with lock:
                    results.append(version)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        _run_concurrently(lambda: _save(1), lambda: _save(2))

        assert errors == []
        landed = sorted(v.version_number for v in results if v is not None)
        assert landed == [1, 2]

    def test_two_concurrent_saves_with_identical_data_dedupe_to_one(self, repos):
        session_repo, version_repo = repos
        session = session_repo.create(SessionDTO(id="s-2", user_id="user-1", preset_id="p-1", name="S2"))

        errors = []
        results = []
        lock = threading.Lock()

        def _save():
            try:
                version = version_repo.create_if_changed(session.id, {"n": 1}, "P")
                with lock:
                    results.append(version)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        _run_concurrently(_save, _save)

        assert errors == []
        landed = [v for v in results if v is not None]
        assert len(landed) == 1
        versions = version_repo.list_for_session(session.id)
        assert [v.version_number for v in versions] == [1]

    def test_insert_failure_leaves_no_partial_version_and_releases_the_lock(self, repos):
        session_repo, version_repo = repos
        session = session_repo.create(SessionDTO(id="s-3", user_id="user-1", preset_id="p-1", name="S3"))

        real_insert = version_repository_module.SessionVersionRepository._insert_version

        def _insert_then_blow_up(cursor, session_id, data, summary):
            real_insert(cursor, session_id, data, summary)
            raise RuntimeError("boom after insert")

        with patch.object(
            version_repository_module.SessionVersionRepository,
            "_insert_version",
            staticmethod(_insert_then_blow_up),
        ):
            with pytest.raises(RuntimeError, match="boom after insert"):
                version_repo.create_if_changed(session.id, {"n": 1}, "P")

        assert version_repo.list_for_session(session.id) == []

        recovered = version_repo.create_if_changed(session.id, {"n": 1}, "P")

        assert recovered.version_number == 1

    def test_concurrent_saves_through_operations_never_swallow_an_integrity_error(self, repos, caplog):
        session_repo, version_repo = repos
        plugin_registry = Mock()
        context = Mock()
        context.data = {}
        plugin_registry.execute_hook.return_value = (context, [])

        first = SaveSessionRequest(preset_id="p-1", name="S3", data={"prompt": "a"})
        result, _ = operations.save_session(session_repo, plugin_registry, version_repo, None, "user-1", first)

        errors = []
        lock = threading.Lock()

        def _update(n):
            try:
                operations.save_session(
                    session_repo,
                    plugin_registry,
                    version_repo,
                    None,
                    "user-1",
                    SaveSessionRequest(preset_id="p-1", name="S3", data={"prompt": f"update-{n}"}),
                )
            except Exception as exc:
                with lock:
                    errors.append(exc)

        with caplog.at_level(logging.ERROR):
            _run_concurrently(lambda: _update(1), lambda: _update(2))

        assert errors == []
        assert "Failed to record session version" not in caplog.text

        versions = version_repo.list_for_session(result["id"])
        assert sorted(v.version_number for v in versions) == [1, 2, 3]
