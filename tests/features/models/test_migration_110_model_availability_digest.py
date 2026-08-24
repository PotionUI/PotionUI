"""Migration 110: model_availability.digest + model_hash_cache.

Loaded FRESH under a patched test DB (via spec_from_file_location, same pattern as
tests/platform/settings/test_model_cache_scope_setting.py) so its module-level ``db``
binds to the test database deterministically, independent of session-wide import order.

Runs against a minimal stand-in `model_availability` table (same columns migration
074 creates) rather than the full migration chain - 074's own `up()` rebuilds `models`
and needs a `backends` table for its foreign key, which is irrelevant to what migration
110 actually does (add a column, create a cache table). The full-chain path is already
covered end-to-end by tests/features/models/test_availability_repository.py, which runs
every migration via PersistenceTestBase and exercises the new column through the repo.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

import tests.conftest as ct

_MIGRATIONS = Path("src/platform/database/migrations")


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_with_availability_table():
    """A fresh in-memory DB with a bare-bones `model_availability` table, patched in
    so the freshly-loaded migration 110 module's `from ... import db` binds here."""
    test_database = ct.TestDatabase()
    with test_database.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE model_availability (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                backend_id TEXT NOT NULL,
                ref TEXT NOT NULL,
                size INTEGER,
                confidence TEXT NOT NULL DEFAULT 'reported',
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(model_id, backend_id)
            )
        """)
    with patch("src.platform.database.database.db", test_database):
        yield test_database
    test_database.close()


def _columns(db, table: str) -> list:
    with db.get_cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cursor.fetchall()]


def _tables(db) -> list:
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cursor.fetchall()]


def test_up_adds_digest_column_to_model_availability(db_with_availability_table):
    migration = _load("110_model_availability_digest", f"m110_{id(db_with_availability_table)}")

    migration.up()

    assert "digest" in _columns(db_with_availability_table, "model_availability")


def test_up_creates_model_hash_cache_with_expected_columns(db_with_availability_table):
    migration = _load("110_model_availability_digest", f"m110b_{id(db_with_availability_table)}")

    migration.up()

    assert "model_hash_cache" in _tables(db_with_availability_table)
    columns = _columns(db_with_availability_table, "model_hash_cache")
    assert {"path", "size", "mtime_ns", "sha256", "hashed_at"} <= set(columns)


def test_model_hash_cache_path_is_the_primary_key(db_with_availability_table):
    """Two rows for the same path must collide, not accumulate - `put()` relies on
    this for its ON CONFLICT(path) upsert."""
    migration = _load("110_model_availability_digest", f"m110c_{id(db_with_availability_table)}")
    migration.up()

    with db_with_availability_table.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO model_hash_cache (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
            ("/models/a.safetensors", 100, 1, "a" * 64),
        )
        with pytest.raises(Exception):
            cursor.execute(
                "INSERT INTO model_hash_cache (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
                ("/models/a.safetensors", 200, 2, "b" * 64),
            )


def test_up_is_idempotent(db_with_availability_table):
    """Re-running up() (e.g. a re-applied migration in dev) must not error on the
    column or table already existing."""
    migration_a = _load("110_model_availability_digest", f"m110d_{id(db_with_availability_table)}")
    migration_a.up()

    migration_b = _load("110_model_availability_digest", f"m110e_{id(db_with_availability_table)}")
    migration_b.up()  # must not raise

    assert "digest" in _columns(db_with_availability_table, "model_availability")


def test_down_drops_model_hash_cache_but_leaves_the_column(db_with_availability_table):
    migration = _load("110_model_availability_digest", f"m110f_{id(db_with_availability_table)}")
    migration.up()

    migration.down()

    assert "model_hash_cache" not in _tables(db_with_availability_table)
    # SQLite has no cheap DROP COLUMN; the column is deliberately left in place -
    # see the migration's own docstring and 104_add_file_is_derived.py.
    assert "digest" in _columns(db_with_availability_table, "model_availability")
