"""The database snapshot: complete, sound, and taken without stopping writers."""

import shutil
import sqlite3
import threading
from unittest.mock import patch

import pytest

from src.features.backup import repository as backup_repository
from src.features.backup.snapshot import (
    applied_migrations,
    integrity_check,
    migration_head,
    snapshot_database,
    table_row_counts,
)

from tests.features.backup.conftest import connect, newest_available_migration


def _uncheckpointed_database(path):
    """A database whose most recent rows are still in the write-ahead log."""
    conn = connect(path)
    with conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.executemany("INSERT INTO notes (body) VALUES (?)", [(f"row-{i}",) for i in range(500)])
    return conn


def test_snapshot_matches_source_row_counts(tmp_path):
    source = tmp_path / "db.sqlite"
    conn = _uncheckpointed_database(source)
    try:
        destination = tmp_path / "snapshot.sqlite"
        snapshot_database(source, destination)
    finally:
        conn.close()

    assert table_row_counts(destination) == table_row_counts(source)
    assert table_row_counts(destination)["notes"] == 500


def test_snapshot_passes_integrity_check(tmp_path):
    source = tmp_path / "db.sqlite"
    conn = _uncheckpointed_database(source)
    try:
        destination = tmp_path / "snapshot.sqlite"
        snapshot_database(source, destination)
    finally:
        conn.close()

    assert integrity_check(destination) == []


def test_snapshot_carries_write_ahead_log_content_a_file_copy_would_miss(tmp_path):
    """The reason `VACUUM INTO` is not negotiable: the rows live in the -wal
    file, and a copy of db.sqlite alone silently loses them."""
    source = tmp_path / "db.sqlite"
    conn = _uncheckpointed_database(source)
    try:
        naive = tmp_path / "naive.sqlite"
        shutil.copy2(source, naive)
        destination = tmp_path / "snapshot.sqlite"
        snapshot_database(source, destination)
    finally:
        conn.close()

    assert table_row_counts(destination)["notes"] == 500
    assert table_row_counts(naive).get("notes", 0) < 500


def test_snapshot_does_not_block_a_concurrent_writer(tmp_path):
    source = tmp_path / "db.sqlite"
    conn = _uncheckpointed_database(source)
    conn.close()

    snapshot_running = threading.Event()
    writer_committed = threading.Event()
    failure = []

    def pause_inside_snapshot():
        if not snapshot_running.is_set():
            snapshot_running.set()
            writer_committed.wait(timeout=20)
        return 0

    class _PausingSqlite:
        def __getattr__(self, name):
            return getattr(sqlite3, name)

        def connect(self, *args, **kwargs):
            handle = sqlite3.connect(*args, **kwargs)
            handle.set_progress_handler(pause_inside_snapshot, 1)
            return handle

    def writer():
        try:
            if not snapshot_running.wait(timeout=20):
                return
            handle = sqlite3.connect(source, timeout=15.0)
            try:
                with handle:
                    handle.execute("INSERT INTO notes (body) VALUES ('concurrent')")
                writer_committed.set()
            finally:
                handle.close()
        except sqlite3.Error as exc:
            failure.append(exc)
            writer_committed.set()

    thread = threading.Thread(target=writer)
    thread.start()
    destination = tmp_path / "snapshot.sqlite"
    try:
        with patch.object(backup_repository, "sqlite3", _PausingSqlite()):
            snapshot_database(source, destination)
    finally:
        writer_committed.set()
        thread.join(timeout=30)

    assert snapshot_running.is_set(), "the snapshot never reached the point where the writer runs"
    assert not failure, f"the concurrent writer failed during the snapshot: {failure}"
    assert writer_committed.is_set(), "the writer never committed, so this proves nothing"
    assert integrity_check(destination) == []
    assert table_row_counts(destination)["notes"] >= 500
    assert table_row_counts(source)["notes"] == 501


def test_snapshot_refuses_to_overwrite(tmp_path):
    source = tmp_path / "db.sqlite"
    _uncheckpointed_database(source).close()
    destination = tmp_path / "snapshot.sqlite"
    destination.write_bytes(b"do not clobber me")

    with pytest.raises(FileExistsError):
        snapshot_database(source, destination)
    assert destination.read_bytes() == b"do not clobber me"


def test_migration_head_is_the_last_applied_migration(tmp_path):
    from tests.features.backup.conftest import build_database

    db_path = tmp_path / "db.sqlite"
    newest = newest_available_migration()
    build_database(db_path, migration=newest)

    assert applied_migrations(db_path) == [newest]
    assert migration_head(db_path) == newest


def test_migration_head_is_none_without_a_database(tmp_path):
    assert migration_head(tmp_path / "absent.sqlite") is None
