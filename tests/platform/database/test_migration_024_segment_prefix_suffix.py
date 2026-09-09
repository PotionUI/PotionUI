"""024 adds `prefix`/`suffix` to the three RichSegment child tables -
idempotently, since the migration runner replays nothing but a second
`up()` must still be harmless.
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

_TABLES = ("prompt_segments", "saved_segments", "segment_template_segments")


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration024SegmentPrefixSuffix(unittest.TestCase):

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
        self.migration = _load_migration("024_segment_prefix_suffix", self.db)

    def tearDown(self):
        Database._instance = None

    def _columns(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}

    def test_prefix_and_suffix_columns_land_on_all_three_tables(self):
        for table in _TABLES:
            self.assertNotIn("prefix", self._columns(table))
            self.assertNotIn("suffix", self._columns(table))

        self.migration.up()

        for table in _TABLES:
            self.assertIn("prefix", self._columns(table))
            self.assertIn("suffix", self._columns(table))

    def test_existing_rows_read_back_with_null_prefix_and_suffix(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO segment_categories (id, user_id, name) "
                "VALUES ('cat1', 'user1', 'Category')"
            )
            cursor.execute(
                "INSERT INTO saved_segments (id, user_id, category_id, name) "
                "VALUES ('s1', 'user1', 'cat1', 'Segment')"
            )
            cursor.execute(
                "SELECT prefix, suffix FROM saved_segments WHERE id = 's1'"
            )
            row = cursor.fetchone()
            self.assertIsNone(row["prefix"])
            self.assertIsNone(row["suffix"])

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before = {table: self._columns(table) for table in _TABLES}

        self.migration.up()

        for table in _TABLES:
            self.assertEqual(self._columns(table), before[table])


if __name__ == "__main__":
    unittest.main()
