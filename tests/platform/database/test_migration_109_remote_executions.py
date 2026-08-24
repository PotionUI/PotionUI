"""Migration 109 brings up remote_executions with the constraints it promises."""

import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationManager

MIGRATION_NAME = "109_add_remote_executions"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "platform"
    / "database"
    / "migrations"
    / f"{MIGRATION_NAME}.py"
)

EXPECTED_COLUMNS = {
    "id",
    "generation_id",
    "provider",
    "backend_id",
    "state",
    "protocol_version",
    "idempotency_key",
    "request_digest",
    "provider_job_id",
    "worker_id",
    "event_cursor",
    "lease_owner",
    "lease_expires_at_ms",
    "lease_epoch",
    "attempt",
    "error_code",
    "error_message",
    "metadata",
    "created_at",
    "updated_at",
    "dispatched_at",
    "started_at",
    "completed_at",
    "expires_at_ms",
    "lease_lapses",
}


class TestMigration109(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        manager = MigrationManager()
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            manager.run_migrations()
        finally:
            sys.stdout = old_stdout

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        for leftover in Path(self.temp_dir).iterdir():
            leftover.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(remote_executions)")
            return {row["name"]: row for row in cursor.fetchall()}

    def _indexes(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='remote_executions'"
            )
            return {row["name"]: row["sql"] for row in cursor.fetchall()}

    def _insert(self, **overrides):
        row = {
            "id": "exec-1",
            "provider": "example-provider",
            "state": "pending",
            "idempotency_key": "idem-1",
            "request_digest": "sha256:" + "a" * 64,
        }
        row.update(overrides)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO remote_executions ({columns}) VALUES ({placeholders})",
                tuple(row.values()),
            )

    def test_the_migration_is_applied_by_the_runner(self):
        self.assertIn(MIGRATION_NAME, MigrationManager().get_applied_migrations())

    def test_the_table_has_every_column_the_record_reads(self):
        self.assertEqual(set(self._columns()), EXPECTED_COLUMNS)

    def test_counters_default_so_an_insert_need_not_name_them(self):
        self._insert()
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM remote_executions WHERE id = 'exec-1'")
            row = cursor.fetchone()

        self.assertEqual(row["event_cursor"], 0)
        self.assertEqual(row["lease_epoch"], 0)
        self.assertEqual(row["attempt"], 0)
        self.assertEqual(row["protocol_version"], 1)
        self.assertIsNotNone(row["created_at"])
        self.assertIsNone(row["lease_expires_at_ms"])

    def test_idempotency_key_is_unique(self):
        self._insert(id="a", idempotency_key="same")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(id="b", idempotency_key="same")

    def test_a_provider_job_id_may_be_claimed_once(self):
        self._insert(id="a", idempotency_key="a", provider_job_id="job-1")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(id="b", idempotency_key="b", provider_job_id="job-1")

    def test_the_same_job_id_under_a_different_provider_is_fine(self):
        self._insert(id="a", idempotency_key="a", provider_job_id="job-1")
        self._insert(
            id="b", idempotency_key="b", provider="other", provider_job_id="job-1"
        )

    def test_the_provider_job_index_is_partial_so_undispatched_rows_coexist(self):
        """A plain unique index would be satisfied by SQLite's distinct NULLs, but
        the partial predicate is what documents the intent - assert it is there."""
        sql = self._indexes()["idx_remote_executions_provider_job"]
        self.assertIn("provider_job_id IS NOT NULL", sql)

        for n in range(3):
            self._insert(id=f"row-{n}", idempotency_key=f"key-{n}")

    def test_the_queried_columns_are_indexed(self):
        indexes = self._indexes()
        for name in (
            "idx_remote_executions_idempotency_key",
            "idx_remote_executions_provider_job",
            "idx_remote_executions_state",
            "idx_remote_executions_lease",
            "idx_remote_executions_generation_id",
        ):
            self.assertIn(name, indexes)

    def test_lease_deadlines_are_stored_as_numbers_not_timestamps(self):
        """String deadlines would compare wrong against SQLite's CURRENT_TIMESTAMP."""
        self.assertEqual(self._columns()["lease_expires_at_ms"]["type"], "INTEGER")

    def test_deleting_a_generation_takes_its_executions_with_it(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, form_data, status) "
                "VALUES ('gen-1', '{}', 'completed')"
            )
        self._insert(generation_id="gen-1")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = 'gen-1'")
            cursor.execute("SELECT COUNT(*) AS n FROM remote_executions")
            self.assertEqual(cursor.fetchone()["n"], 0)

    def test_up_is_idempotent(self):
        module = _load_migration()
        module.up()
        self.assertEqual(set(self._columns()), EXPECTED_COLUMNS)

    def test_down_drops_the_table(self):
        module = _load_migration()
        module.down()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='remote_executions'"
            )
            self.assertIsNone(cursor.fetchone())


def _load_migration():
    spec = importlib.util.spec_from_file_location(MIGRATION_NAME, MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
