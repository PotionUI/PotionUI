"""Migration 117 creates `generation_run_reports`, keyed 1:1 on `generation_id`
with `ON DELETE CASCADE` - unlike `generation_stats` (091), which deliberately
drops its FK, a run report has no meaning once its generation is gone.
"""

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration117(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("002_create_generations", self.db).up()
        self._insert_generation("gen-1")

    def tearDown(self):
        Database._instance = None

    def _insert_generation(self, generation_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generations (id, preset_name, form_data) VALUES (?, ?, ?)",
                (generation_id, "preset", "{}"),
            )
            conn.commit()

    def test_creates_the_table(self):
        _load_migration("117_add_generation_run_reports", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='generation_run_reports'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_report_is_readable_after_insert(self):
        _load_migration("117_add_generation_run_reports", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                ("gen-1", '{"schema_version": 1}'),
            )
            conn.commit()
            row = conn.execute(
                "SELECT report FROM generation_run_reports WHERE generation_id = ?", ("gen-1",)
            ).fetchone()
        self.assertEqual(row[0], '{"schema_version": 1}')

    def test_generation_id_is_unique(self):
        _load_migration("117_add_generation_run_reports", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                ("gen-1", "{}"),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                    ("gen-1", "{}"),
                )

    def test_deleting_the_generation_cascades(self):
        _load_migration("117_add_generation_run_reports", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                ("gen-1", "{}"),
            )
            conn.commit()
            conn.execute("DELETE FROM generations WHERE id = ?", ("gen-1",))
            conn.commit()
            row = conn.execute(
                "SELECT 1 FROM generation_run_reports WHERE generation_id = ?", ("gen-1",)
            ).fetchone()
        self.assertIsNone(row)

    def test_a_report_for_an_unknown_generation_is_rejected(self):
        _load_migration("117_add_generation_run_reports", self.db).up()

        with self.db.get_connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                    ("no-such-generation", "{}"),
                )

    def test_running_twice_is_a_no_op(self):
        migration = _load_migration("117_add_generation_run_reports", self.db)
        migration.up()
        migration.up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generation_run_reports (generation_id, report) VALUES (?, ?)",
                ("gen-1", "{}"),
            )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM generation_run_reports").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
