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


class TestMigration046ModelPresetScopes(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(temp.name) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("046_model_preset_scopes", self.db)

    def tearDown(self):
        Database._instance = None

    def _tables(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {row["name"] for row in cursor.fetchall()}

    def _add_model(self, model_id="m1"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO models (id, filename, model_type) VALUES (?, ?, 'cloud')", (model_id, f"fake~{model_id}")
            )

    def _scope(self, model_id, preset_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_preset_scopes (model_id, preset_id, created_at) VALUES (?, ?, 't')",
                (model_id, preset_id),
            )

    def _count(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_preset_scopes")
            return cursor.fetchone()["n"]

    def test_creates_the_scope_table_with_its_columns(self):
        self.migration.up()

        assert "model_preset_scopes" in self._tables()
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(model_preset_scopes)")
            assert {row["name"] for row in cursor.fetchall()} == {"model_id", "preset_id", "created_at"}

    def test_a_model_lists_a_preset_once(self):
        self.migration.up()
        self._add_model()
        self._scope("m1", "p1")

        with self.assertRaises(sqlite3.IntegrityError):
            self._scope("m1", "p1")

    def test_a_model_may_name_several_presets_and_a_preset_several_models(self):
        self.migration.up()
        self._add_model("m1")
        self._add_model("m2")
        for model_id, preset_id in (("m1", "p1"), ("m1", "p2"), ("m2", "p1")):
            self._scope(model_id, preset_id)

        assert self._count() == 3

    def test_a_scope_needs_an_existing_model(self):
        self.migration.up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._scope("ghost", "p1")

    def test_deleting_a_model_removes_its_scope_rows(self):
        self.migration.up()
        self._add_model("m1")
        self._add_model("m2")
        self._scope("m1", "p1")
        self._scope("m2", "p1")

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM models WHERE id = 'm1'")

        assert self._count() == 1

    def test_up_twice_keeps_the_rows(self):
        self.migration.up()
        self._add_model()
        self._scope("m1", "p1")

        self.migration.up()

        assert self._count() == 1

    def test_down_drops_the_table(self):
        self.migration.up()

        self.migration.down()

        assert "model_preset_scopes" not in self._tables()
