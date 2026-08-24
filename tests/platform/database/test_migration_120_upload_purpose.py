"""Migration 120 adds `uploads.purpose`, backfilling every existing row to
`'user_upload'` (the column default) since the distinction did not exist
before - see the migration's docstring.
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


class TestMigration120(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("007_create_users", self.db).up()
        _load_migration("087_add_uploads_table", self.db).up()

        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user_1', 'user_1', 'user_1@example.com', 'h')"
            )
            conn.commit()

    def tearDown(self):
        Database._instance = None

    def _insert_upload(self, upload_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO uploads (id, user_id, filename, media_type) "
                "VALUES (?, 'user_1', ?, 'image')",
                (upload_id, f"{upload_id}.png"),
            )
            conn.commit()

    def _purpose_of(self, upload_id):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT purpose FROM uploads WHERE id = ?", (upload_id,)
            ).fetchone()
        return row["purpose"] if row else None

    def test_adds_the_purpose_column(self):
        _load_migration("120_add_upload_purpose", self.db).up()

        with self.db.get_connection() as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(uploads)").fetchall()]
        self.assertIn("purpose", columns)

    def test_existing_row_backfills_to_user_upload(self):
        self._insert_upload("upload_1")

        _load_migration("120_add_upload_purpose", self.db).up()

        self.assertEqual(self._purpose_of("upload_1"), "user_upload")

    def test_purpose_is_not_null_for_every_row(self):
        self._insert_upload("upload_1")
        self._insert_upload("upload_2")

        _load_migration("120_add_upload_purpose", self.db).up()

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT purpose FROM uploads").fetchall()
        self.assertTrue(all(r["purpose"] for r in rows))

    def test_running_twice_is_a_no_op(self):
        self._insert_upload("upload_1")
        migration = _load_migration("120_add_upload_purpose", self.db)

        migration.up()
        migration.up()

        self.assertEqual(self._purpose_of("upload_1"), "user_upload")


if __name__ == '__main__':
    unittest.main()
