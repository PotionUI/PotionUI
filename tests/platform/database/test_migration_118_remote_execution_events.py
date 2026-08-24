"""Migration 118 creates `remote_execution_events` and adds
`expires_at_ms`/`lease_lapses` to `remote_executions`."""

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

MIGRATION_NAME = "118_add_remote_execution_events"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "platform"
    / "database"
    / "migrations"
    / f"{MIGRATION_NAME}.py"
)


class TestMigration118(unittest.TestCase):
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

    def _insert_execution(self, execution_id="exec-1"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_executions
                    (id, provider, state, idempotency_key, request_digest)
                VALUES (?, 'example-provider', 'pending', ?, ?)
                """,
                (execution_id, f"idem-{execution_id}", "sha256:" + "a" * 64),
            )

    def test_the_migration_is_applied_by_the_runner(self):
        self.assertIn(MIGRATION_NAME, MigrationManager().get_applied_migrations())

    def test_the_events_table_has_the_expected_columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(remote_execution_events)")
            columns = {row["name"] for row in cursor.fetchall()}
        self.assertEqual(
            columns,
            {
                "id", "execution_id", "cursor", "kind", "pipe_id",
                "emitted_at", "received_at", "payload",
            },
        )

    def test_remote_executions_gained_the_new_columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(remote_executions)")
            columns = {row["name"] for row in cursor.fetchall()}
        self.assertIn("expires_at_ms", columns)
        self.assertIn("lease_lapses", columns)

    def test_lease_lapses_defaults_to_zero(self):
        self._insert_execution()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT lease_lapses, expires_at_ms FROM remote_executions WHERE id = 'exec-1'"
            )
            row = cursor.fetchone()
        self.assertEqual(row["lease_lapses"], 0)
        self.assertIsNone(row["expires_at_ms"])

    def test_an_event_is_readable_after_insert(self):
        self._insert_execution()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, pipe_id, emitted_at, payload)
                VALUES ('exec-1', 1, 'running', 'generator', '2026-08-15T00:00:00Z', '{}')
                """
            )
            cursor.execute(
                "SELECT kind, payload FROM remote_execution_events WHERE execution_id = 'exec-1'"
            )
            row = cursor.fetchone()
        self.assertEqual(row["kind"], "running")
        self.assertEqual(row["payload"], "{}")

    def test_execution_id_and_cursor_are_unique_together(self):
        self._insert_execution()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, emitted_at, payload)
                VALUES ('exec-1', 1, 'running', '2026-08-15T00:00:00Z', '{}')
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO remote_execution_events
                        (execution_id, cursor, kind, emitted_at, payload)
                    VALUES ('exec-1', 1, 'staging', '2026-08-15T00:00:01Z', '{}')
                    """
                )

    def test_the_same_cursor_is_fine_under_a_different_execution(self):
        self._insert_execution("exec-1")
        self._insert_execution("exec-2")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, emitted_at, payload)
                VALUES ('exec-1', 1, 'running', '2026-08-15T00:00:00Z', '{}')
                """
            )
            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, emitted_at, payload)
                VALUES ('exec-2', 1, 'running', '2026-08-15T00:00:00Z', '{}')
                """
            )

    def test_deleting_the_execution_cascades_to_its_events(self):
        self._insert_execution()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, emitted_at, payload)
                VALUES ('exec-1', 1, 'running', '2026-08-15T00:00:00Z', '{}')
                """
            )
            cursor.execute("DELETE FROM remote_executions WHERE id = 'exec-1'")
            cursor.execute("SELECT COUNT(*) AS n FROM remote_execution_events")
            self.assertEqual(cursor.fetchone()["n"], 0)

    def test_up_is_idempotent(self):
        module = _load_migration()
        module.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(remote_executions)")
            columns = [row["name"] for row in cursor.fetchall()]
        self.assertEqual(columns.count("expires_at_ms"), 1)
        self.assertEqual(columns.count("lease_lapses"), 1)

    def test_down_drops_the_events_table_and_leaves_the_columns(self):
        module = _load_migration()
        module.down()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='remote_execution_events'"
            )
            self.assertIsNone(cursor.fetchone())
            cursor.execute("PRAGMA table_info(remote_executions)")
            columns = {row["name"] for row in cursor.fetchall()}
        self.assertIn("expires_at_ms", columns)
        self.assertIn("lease_lapses", columns)


def _load_migration():
    spec = importlib.util.spec_from_file_location(MIGRATION_NAME, MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
