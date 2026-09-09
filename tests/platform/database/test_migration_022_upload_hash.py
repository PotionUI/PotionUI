"""022 adds `content_hash` to `uploads` plus a lookup index - idempotently,
since the migration runner replays nothing but a second `up()` must still be
harmless.
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


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration022UploadHash(unittest.TestCase):

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
        self.migration = _load_migration("022_upload_hash", self.db)

    def tearDown(self):
        Database._instance = None

    def _columns(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}

    def _indexes(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA index_list({table})")
            return {row[1] for row in cursor.fetchall()}

    def test_content_hash_column_lands_on_uploads(self):
        self.assertNotIn("content_hash", self._columns("uploads"))

        self.migration.up()

        self.assertIn("content_hash", self._columns("uploads"))

    def test_index_is_created(self):
        self.migration.up()

        self.assertIn("idx_uploads_user_hash", self._indexes("uploads"))

    def test_existing_rows_read_back_with_a_null_hash(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) "
                "VALUES ('u1', 'user1', 'a.png', 'image')"
            )
            cursor.execute("SELECT content_hash FROM uploads WHERE id = 'u1'")
            self.assertIsNone(cursor.fetchone()["content_hash"])

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before_columns = self._columns("uploads")
        before_indexes = self._indexes("uploads")

        self.migration.up()

        self.assertEqual(self._columns("uploads"), before_columns)
        self.assertEqual(self._indexes("uploads"), before_indexes)


if __name__ == "__main__":
    unittest.main()
