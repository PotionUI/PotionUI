import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations"

_TABLES = {"organize_rules", "organize_runs", "organize_applications", "organize_handled", "organize_controls"}


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration052Organize(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(temp.name) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("052_organize", self.db)
        with self.db.get_cursor() as cursor:
            for user_id in ("u1", "u2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )

    def tearDown(self):
        Database._instance = None

    def _tables(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {row["name"] for row in cursor.fetchall()}

    def _rule(self, rule_id, user_id="u1", subject="generation"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, created_at, updated_at) "
                "VALUES (?, ?, 'r', ?, 't', '2026-10-05T00:00:00+00:00', '2026-10-05T00:00:00+00:00')",
                (rule_id, user_id, subject),
            )

    def test_creates_every_table(self):
        self.migration.up()

        self.assertTrue(_TABLES <= self._tables())

    def test_up_runs_twice(self):
        self.migration.up()
        self.migration.up()

        self.assertTrue(_TABLES <= self._tables())

    def test_subject_is_checked(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._rule("r1", subject="prompt")

    def test_deleting_a_user_removes_their_rules_runs_and_records(self):
        self.migration.up()
        self._rule("r1")
        self._rule("r2", user_id="u2")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_runs (id, rule_id, rule_name, user_id, subject, kind, started_at) "
                "VALUES ('run1', 'r1', 'r', 'u1', 'generation', 'live', '2026-10-05T00:00:00+00:00')"
            )
            cursor.execute(
                "INSERT INTO organize_applications (id, run_id, rule_id, user_id, item_type, item_id, action_kind, "
                "target_type, target_id, created_at) VALUES ('a1', 'run1', 'r1', 'u1', 'generation', 'g1', "
                "'add_to_collection', 'collection', 'c1', '2026-10-05T00:00:00+00:00')"
            )
            cursor.execute(
                "INSERT INTO organize_handled (rule_id, item_type, item_id, handled_at) "
                "VALUES ('r1', 'generation', 'g1', '2026-10-05T00:00:00+00:00')"
            )
            cursor.execute("DELETE FROM users WHERE id = 'u1'")

        with self.db.get_cursor() as cursor:
            counts = {
                table: cursor.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                for table in ("organize_rules", "organize_runs", "organize_applications", "organize_handled")
            }
        self.assertEqual(counts, {"organize_rules": 1, "organize_runs": 0, "organize_applications": 0, "organize_handled": 0})

    def test_deleting_a_rule_keeps_its_runs_for_activity(self):
        self.migration.up()
        self._rule("r1")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_runs (id, rule_id, rule_name, user_id, subject, kind, started_at) "
                "VALUES ('run1', 'r1', 'r', 'u1', 'generation', 'backfill', '2026-10-05T00:00:00+00:00')"
            )
            cursor.execute("DELETE FROM organize_rules WHERE id = 'r1'")
            remaining = cursor.execute("SELECT COUNT(*) AS n FROM organize_runs").fetchone()["n"]

        self.assertEqual(remaining, 1)

    def test_down_drops_the_tables(self):
        self.migration.up()
        self.migration.down()

        self.assertFalse(_TABLES & self._tables())
