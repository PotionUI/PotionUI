"""015 adds the `history_revisions` counter the history version endpoint reads.

`001_baseline.py` is a frozen snapshot with no such table, so this migration is
what puts it there on a fresh install as well as an upgrade.
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

_TABLE = "history_revisions"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration015HistoryRevisions(unittest.TestCase):

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
        self.migration = _load_migration("015_history_revisions", self.db)

    def tearDown(self):
        Database._instance = None

    def _table_sql(self):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_TABLE,),
            ).fetchone()
        return row[0] if row else None

    def test_baseline_alone_has_no_such_table(self):
        self.assertIsNone(self._table_sql())

    def test_creates_the_table_keyed_by_user(self):
        self.migration.up()

        self.assertIsNotNone(self._table_sql())
        with self.db.get_connection() as conn:
            columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({_TABLE})")}
        self.assertEqual(set(columns), {"user_id", "revision", "updated_at"})
        self.assertEqual(columns["user_id"][5], 1)  # user_id is the primary key

    def test_upsert_counts_up_per_user(self):
        self.migration.up()

        upsert = """
            INSERT INTO history_revisions (user_id, revision, updated_at)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE
            SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
        """
        with self.db.get_connection() as conn:
            conn.execute(upsert, ("user-a",))
            conn.execute(upsert, ("user-a",))
            conn.execute(upsert, ("user-b",))
            conn.commit()
            rows = dict(conn.execute("SELECT user_id, revision FROM history_revisions"))

        self.assertEqual(rows, {"user-a": 2, "user-b": 1})

    def test_idempotent(self):
        self.migration.up()
        self.migration.up()

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_TABLE,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_down_removes_it(self):
        self.migration.up()
        self.migration.down()

        self.assertIsNone(self._table_sql())
