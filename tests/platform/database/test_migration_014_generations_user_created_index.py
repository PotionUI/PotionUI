"""014 adds the composite index the history list orders by. `001_baseline.py` is
a frozen snapshot that only indexes `generations(user_id)`, so this migration is
what puts the index there on a fresh install as well as an upgrade.
"""

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

_INDEX = "idx_generations_user_created_id"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration014GenerationsUserCreatedIndex(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE applied_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("014_generations_user_created_index", self.db)

    def tearDown(self):
        Database._instance = None

    def _index_sql(self):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (_INDEX,),
            ).fetchone()
        return row[0] if row else None

    def test_baseline_alone_has_no_such_index(self):
        self.assertIsNone(self._index_sql())

    def test_creates_the_index_over_user_created_and_id(self):
        self.migration.up()

        sql = self._index_sql()
        self.assertIsNotNone(sql)
        normalized = " ".join(sql.split()).lower()
        self.assertIn("on generations (user_id, created_at desc, id desc)", normalized)

    def test_index_columns_are_in_key_order(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            columns = [row[2] for row in conn.execute(f"PRAGMA index_info({_INDEX})")]
        self.assertEqual(columns, ["user_id", "created_at", "id"])

    def test_idempotent(self):
        self.migration.up()
        self.migration.up()

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = ?",
                (_INDEX,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_down_removes_it(self):
        self.migration.up()
        self.migration.down()

        self.assertIsNone(self._index_sql())
