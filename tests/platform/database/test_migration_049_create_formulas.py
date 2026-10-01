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


class TestMigration049CreateFormulas(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(temp.name) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("049_create_formulas", self.db)
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = ON")
            for user_id in ("u1", "u2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )

    def tearDown(self):
        Database._instance = None

    def _insert(self, formula_id, owner="u1", preset="p", mode="video", name="Turbo"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO formulas (id, owner_id, preset_id, mode, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')",
                (formula_id, owner, preset, mode, name),
            )

    def _tables(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {row["name"] for row in cursor.fetchall()}

    def test_creates_the_table_with_the_expected_columns(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(formulas)")
            columns = {row["name"] for row in cursor.fetchall()}
        assert columns == {
            "id", "owner_id", "preset_id", "mode", "variant", "name", "note", "groups",
            "values_json", "signatures", "preset_version", "created_at", "updated_at",
        }

    def test_the_same_name_twice_in_one_scope_is_rejected(self):
        self.migration.up()
        self._insert("f1")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert("f2")

    def test_the_name_may_repeat_across_owners_presets_and_modes(self):
        self.migration.up()
        self._insert("f1")

        self._insert("f2", owner="u2")
        self._insert("f3", preset="q")
        self._insert("f4", mode="refs")

    def test_deleting_a_user_removes_their_formulas(self):
        self.migration.up()
        self._insert("f1")
        self._insert("f2", owner="u2")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = 'u1'")

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT id FROM formulas")
            assert [row["id"] for row in cursor.fetchall()] == ["f2"]

    def test_up_twice_is_harmless(self):
        self.migration.up()
        self._insert("f1")

        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert("f2")

    def test_down_drops_the_table(self):
        self.migration.up()

        self.migration.down()

        assert "formulas" not in self._tables()
