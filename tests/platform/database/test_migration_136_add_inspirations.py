"""Migration 136 adds the Inspirations tables."""

import importlib.util
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


class TestMigration136(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _create_pre_136_schema(self):
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE settings (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    description TEXT,
                    type TEXT NOT NULL
                )
            """)
            conn.execute("INSERT INTO users (id, username) VALUES ('user-1', 'alice')")
            conn.commit()

    def _tables(self, conn):
        return {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def test_creates_all_tables(self):
        self._create_pre_136_schema()
        _load_migration("136_add_inspirations", self.db).up()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            for name in (
                "inspirations",
                "inspiration_comments",
                "inspiration_collections",
                "inspiration_collection_items",
                "inspiration_saves",
            ):
                self.assertIn(name, tables)

    def test_up_is_idempotent(self):
        self._create_pre_136_schema()
        migration = _load_migration("136_add_inspirations", self.db)
        migration.up()
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            for name in (
                "inspirations",
                "inspiration_comments",
                "inspiration_collections",
                "inspiration_collection_items",
                "inspiration_saves",
            ):
                self.assertIn(name, tables)

    def test_down_drops_tables(self):
        self._create_pre_136_schema()
        migration = _load_migration("136_add_inspirations", self.db)
        migration.up()
        migration.down()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            for name in (
                "inspirations",
                "inspiration_comments",
                "inspiration_collections",
                "inspiration_collection_items",
                "inspiration_saves",
            ):
                self.assertNotIn(name, tables)

    def test_foreign_keys_cascade(self):
        self._create_pre_136_schema()
        _load_migration("136_add_inspirations", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO inspirations (id, user_id, title, media, params_snapshot) "
                "VALUES ('insp-1', 'user-1', 'My inspiration', '[]', '{}')"
            )
            conn.execute(
                "INSERT INTO inspiration_comments (id, inspiration_id, user_id, body) "
                "VALUES ('c1', 'insp-1', 'user-1', 'nice')"
            )
            conn.execute(
                "INSERT INTO inspiration_collections (id, user_id, name) "
                "VALUES ('col-1', 'user-1', 'Favorites')"
            )
            conn.execute(
                "INSERT INTO inspiration_collection_items (collection_id, inspiration_id) "
                "VALUES ('col-1', 'insp-1')"
            )
            conn.execute(
                "INSERT INTO inspiration_saves (user_id, inspiration_id) "
                "VALUES ('user-1', 'insp-1')"
            )
            conn.commit()

            conn.execute("DELETE FROM inspirations WHERE id = 'insp-1'")
            conn.commit()

            self.assertIsNone(
                conn.execute("SELECT 1 FROM inspiration_comments WHERE id = 'c1'").fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM inspiration_collection_items WHERE inspiration_id = 'insp-1'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM inspiration_saves WHERE inspiration_id = 'insp-1'"
                ).fetchone()
            )
            # The collection itself survives - only its membership is cascaded.
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM inspiration_collections WHERE id = 'col-1'").fetchone()
            )


if __name__ == '__main__':
    unittest.main()
