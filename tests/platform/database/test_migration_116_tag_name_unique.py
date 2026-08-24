"""Migration 116 drops the global UNIQUE on `tags.name` that 029 could not.

The broken state is not hand-written: migrations 020 and 029 are run for real,
and the defect they leave behind - a second user unable to create a tag whose
name someone else already used - is asserted before 116 is allowed near it. A
test that only checked the repaired schema would pass just as happily against a
database that never had the constraint.
"""

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
    """Import a migration module with its module-level `db` bound to `database`."""
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration116(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("020_add_tags_system", self.db).up()
        _load_migration("029_add_generation_tags_system", self.db).up()

        self._insert_tag("t1", "cats", "GENERATION", "user_a")

    def tearDown(self):
        Database._instance = None

    def _insert_tag(self, tag_id, name, tag_type, user_id):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tags (id, name, type, user_id) VALUES (?, ?, ?, ?)",
                (tag_id, name, tag_type, user_id),
            )
            conn.commit()

    def _indexes(self):
        with self.db.get_connection() as conn:
            return {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='tags' AND sql IS NOT NULL"
                )
            }

    def test_020_and_029_leave_the_name_globally_unique(self):
        """The premise. Without this the repair tests prove nothing."""
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_tag("t2", "cats", "GENERATION", "user_b")

    def test_two_users_can_hold_the_same_tag_name_afterwards(self):
        _load_migration("116_drop_global_tag_name_unique", self.db).up()

        self._insert_tag("t2", "cats", "GENERATION", "user_b")
        self._insert_tag("t3", "cats", "UPLOAD", "user_a")

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT id FROM tags WHERE name = 'cats'").fetchall()
        self.assertEqual({row[0] for row in rows}, {"t1", "t2", "t3"})

    def test_the_compound_uniqueness_rule_still_bites(self):
        """029's index becomes the operative rule - it must not be lost too."""
        _load_migration("116_drop_global_tag_name_unique", self.db).up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_tag("t2", "cats", "GENERATION", "user_a")

    def test_rows_and_indexes_survive_the_rebuild(self):
        before = self._indexes()

        _load_migration("116_drop_global_tag_name_unique", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, name, type, user_id FROM tags WHERE id = 't1'"
            ).fetchone()
        self.assertEqual(tuple(row), ("t1", "cats", "GENERATION", "user_a"))
        self.assertEqual(self._indexes(), before)
        self.assertIn("idx_tags_name_type_user", before)

    def test_junction_foreign_keys_still_point_at_tags(self):
        """The rename must not rewrite REFERENCES on the junction tables - that
        is the damage migration 114 had to repair after 035 did exactly this."""
        _load_migration("116_drop_global_tag_name_unique", self.db).up()

        with self.db.get_connection() as conn:
            for table in ("model_tags", "generation_tags"):
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()[0]
                self.assertIn("REFERENCES tags(id)", sql.replace('"', ''))

    def test_running_twice_is_a_no_op(self):
        migration = _load_migration("116_drop_global_tag_name_unique", self.db)
        migration.up()
        migration.up()

        self._insert_tag("t2", "cats", "GENERATION", "user_b")
        with self.db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main()
