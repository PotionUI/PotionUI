"""Migration 135 adds `model_attribute_definitions` and `user_model_attributes`,
copies each model's `triggers` JSON array into `model_metadata['triggers']`
(merging with whatever is already there, never overwriting an existing key),
and drops the `models.triggers` column.
"""

import importlib.util
import json
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


class TestMigration135(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _create_pre_135_schema(self):
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE models (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    triggers TEXT,
                    model_metadata TEXT
                )
            """)
            conn.execute(
                "INSERT INTO users (id, username) VALUES ('user-1', 'alice')"
            )
            conn.execute(
                "INSERT INTO models (id, filename, model_type, triggers, model_metadata) VALUES "
                "('m1', 'a.safetensors', 'lora', '[\"foo\", \"bar\"]', NULL)"
            )
            conn.execute(
                "INSERT INTO models (id, filename, model_type, triggers, model_metadata) VALUES "
                "('m2', 'b.safetensors', 'lora', NULL, '{\"strength\": 0.8}')"
            )
            conn.execute(
                "INSERT INTO models (id, filename, model_type, triggers, model_metadata) VALUES "
                "('m3', 'c.safetensors', 'checkpoint', '[]', NULL)"
            )
            conn.commit()

    def _tables(self, conn):
        return {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def test_creates_definition_and_overlay_tables(self):
        self._create_pre_135_schema()
        _load_migration("135_add_model_attributes", self.db).up()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            self.assertIn("model_attribute_definitions", tables)
            self.assertIn("user_model_attributes", tables)

    def test_migrates_triggers_into_model_metadata_and_drops_column(self):
        self._create_pre_135_schema()
        _load_migration("135_add_model_attributes", self.db).up()

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(models)")}
            self.assertNotIn("triggers", columns)

            m1 = conn.execute("SELECT model_metadata FROM models WHERE id = 'm1'").fetchone()
            self.assertEqual(json.loads(m1["model_metadata"])["triggers"], ["foo", "bar"])

            # m2 had no triggers but did have existing model_metadata - untouched.
            m2 = conn.execute("SELECT model_metadata FROM models WHERE id = 'm2'").fetchone()
            self.assertEqual(json.loads(m2["model_metadata"]), {"strength": 0.8})

            # m3's triggers were an empty array - nothing to migrate.
            m3 = conn.execute("SELECT model_metadata FROM models WHERE id = 'm3'").fetchone()
            self.assertIsNone(m3["model_metadata"])

    def test_up_is_idempotent(self):
        self._create_pre_135_schema()
        migration = _load_migration("135_add_model_attributes", self.db)
        migration.up()
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            m1 = conn.execute("SELECT model_metadata FROM models WHERE id = 'm1'").fetchone()
            self.assertEqual(json.loads(m1["model_metadata"])["triggers"], ["foo", "bar"])

    def test_down_restores_triggers_column(self):
        self._create_pre_135_schema()
        migration = _load_migration("135_add_model_attributes", self.db)
        migration.up()
        migration.down()

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(models)")}
            self.assertIn("triggers", columns)
            tables = self._tables(conn)
            self.assertNotIn("model_attribute_definitions", tables)
            self.assertNotIn("user_model_attributes", tables)

            m1 = conn.execute("SELECT triggers, model_metadata FROM models WHERE id = 'm1'").fetchone()
            self.assertEqual(json.loads(m1["triggers"]), ["foo", "bar"])
            self.assertNotIn("triggers", json.loads(m1["model_metadata"] or "{}"))


if __name__ == '__main__':
    unittest.main()
