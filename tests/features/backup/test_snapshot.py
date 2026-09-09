"""The database snapshot: complete, sound, and taken without stopping writers."""

import shutil
import sqlite3
import threading
import time

import pytest

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

    stop = threading.Event()
    written = []
    failure = []

    def writer():
        handle = sqlite3.connect(source, timeout=30.0)
        handle.execute("PRAGMA busy_timeout = 30000").close()
        try:
            while not stop.is_set():
                with handle:
                    handle.execute("INSERT INTO notes (body) VALUES ('concurrent')")
                written.append(1)
                time.sleep(0.001)
        except sqlite3.Error as exc:
            failure.append(exc)
        finally:
            handle.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        destination = tmp_path / "snapshot.sqlite"
        snapshot_database(source, destination)
    finally:
        stop.set()
        thread.join(timeout=30)

    assert not failure, f"the concurrent writer failed during the snapshot: {failure}"
    assert written, "the writer never committed, so this proves nothing"
    assert integrity_check(destination) == []
    assert table_row_counts(destination)["notes"] >= 500


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
