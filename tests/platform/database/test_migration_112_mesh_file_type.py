"""Migration 112 widens the files.file_type CHECK constraint where one exists.

Two branches, both exercised here, because only one of them ever runs on a real
install and a test that covers only the other proves nothing:

- On a database built by the current migration chain there is no CHECK on
  file_type at all (migration 010 creates the column plain), so 112 must be a
  no-op and 'MESH' must already insert.
- On a database whose `files` came from migration 035's recovery branch the
  column is constrained to IMAGE/VIDEO/AUDIO and 'MESH' is rejected. 112 must
  widen it without losing rows, columns, or the generation_files foreign key.
"""

import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationManager

MIGRATION_NAME = "112_add_mesh_file_type"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations" / f"{MIGRATION_NAME}.py"
)

# The shape migration 035's recovery branch leaves behind, plus the columns a
# database reaching 112 will also have picked up along the way.
CONSTRAINED_FILES_TABLE = """
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('IMAGE', 'VIDEO', 'AUDIO')),
    file_size INTEGER,
    pipe_name TEXT,
    is_final BOOLEAN DEFAULT FALSE,
    user_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hash TEXT,
    filename TEXT,
    mime_type TEXT,
    thumbnail_small TEXT,
    thumbnail_medium TEXT,
    thumbnail_large TEXT,
    width INTEGER,
    height INTEGER,
    duration_seconds REAL,
    fps REAL,
    is_derived INTEGER NOT NULL DEFAULT 0
)
"""


def _load_migration(database):
    """Import the migration module with its `db` bound to `database`."""
    spec = importlib.util.spec_from_file_location(MIGRATION_NAME, MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MIGRATION_NAME] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class _TempDatabase:
    """A Database pointed at a throwaway file."""

    def __init__(self, path):
        Database._instance = None
        self.db = Database()
        self.db.db_path = path
        self.db._initialized = True


class TestMigration112OnTheRealChain(unittest.TestCase):
    """The branch that actually runs on every install: nothing to do."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"
        self.db = _TempDatabase(self.temp_db_path).db

        with patch('src.platform.database.migration_runner.db', self.db), \
             patch('src.platform.database.database.db', self.db):
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                MigrationManager().run_migrations()
            finally:
                sys.stdout = old_stdout

    def tearDown(self):
        Database._instance = None

    def _files_sql(self):
        with self.db.get_connection() as conn:
            return conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
            ).fetchone()[0]

    def test_chain_leaves_file_type_unconstrained(self):
        """The premise 112 is insurance against does not hold on a real chain."""
        self.assertNotIn("CHECK", self._files_sql().upper())

    def test_mesh_inserts_after_the_full_chain(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, file_size) VALUES (?, ?, ?, ?)",
                ('f_mesh', 'generations/2026-01-01/gen/0.glb', 'MESH', 600),
            )

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT file_type FROM files WHERE id='f_mesh'").fetchone()
        self.assertEqual(row[0], 'MESH')

    def test_112_is_a_no_op_here(self):
        """It must not rebuild a table that has nothing wrong with it.

        Comparing the table SQL is not enough - a faithful rebuild produces
        byte-identical SQL. `schema_version` is the counter SQLite bumps on any
        schema change, so it sees the rebuild the SQL text cannot.
        """
        with self.db.get_connection() as conn:
            before_sql = self._files_sql()
            before_version = conn.execute("PRAGMA schema_version").fetchone()[0]

        _load_migration(self.db).up()

        with self.db.get_connection() as conn:
            after_version = conn.execute("PRAGMA schema_version").fetchone()[0]

        self.assertEqual(self._files_sql(), before_sql)
        self.assertEqual(after_version, before_version, "112 rebuilt an unconstrained files table")


class TestMigration112OnAConstrainedDatabase(unittest.TestCase):
    """The branch 112 exists for: a files table left constrained by 035."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"
        self.db = _TempDatabase(self.temp_db_path).db

        with self.db.get_connection() as conn:
            conn.execute(CONSTRAINED_FILES_TABLE)
            conn.execute("""
                CREATE TABLE generation_files (
                    id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "INSERT INTO files (id, file_path, file_type, file_size, width, height)"
                " VALUES ('f1', 'gen/0.png', 'IMAGE', 1234, 512, 512)"
            )
            conn.execute(
                "INSERT INTO generation_files (id, generation_id, file_id)"
                " VALUES ('gf1', 'gen1', 'f1')"
            )
            conn.execute("CREATE INDEX idx_files_file_type ON files (file_type)")
            # An index the migration cannot know about, over a column a
            # hardcoded rebuild list would not mention.
            conn.execute("CREATE INDEX idx_files_is_derived ON files (is_derived)")
            conn.commit()

    def tearDown(self):
        Database._instance = None

    def _files_sql(self):
        with self.db.get_connection() as conn:
            return conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
            ).fetchone()[0]

    def _insert_mesh(self, file_id='f_mesh'):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO files (id, file_path, file_type, file_size) VALUES (?, ?, ?, ?)",
                (file_id, 'gen/0.glb', 'MESH', 600),
            )
            conn.commit()

    def test_mesh_is_rejected_before_the_migration(self):
        """Without this, the widening test could pass against no constraint."""
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_mesh()

    def test_mesh_is_accepted_after_the_migration(self):
        _load_migration(self.db).up()

        self._insert_mesh()

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT file_type FROM files WHERE id='f_mesh'").fetchone()
        self.assertEqual(row[0], 'MESH')

    def test_the_constraint_is_widened_not_dropped(self):
        """A migration that simply removed the CHECK would pass the test above."""
        _load_migration(self.db).up()

        table_sql = self._files_sql().upper()
        self.assertIn("CHECK", table_sql)
        for value in ("'IMAGE'", "'VIDEO'", "'AUDIO'", "'MESH'"):
            self.assertIn(value, table_sql)

        with self.db.get_connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO files (id, file_path, file_type) VALUES ('bad', 'x', 'NONSENSE')"
                )

    def test_existing_rows_and_columns_survive(self):
        with self.db.get_connection() as conn:
            before_columns = [row[1] for row in conn.execute("PRAGMA table_info(files)")]

        _load_migration(self.db).up()

        with self.db.get_connection() as conn:
            after_columns = [row[1] for row in conn.execute("PRAGMA table_info(files)")]
            row = conn.execute(
                "SELECT file_path, file_type, file_size, width, height FROM files WHERE id='f1'"
            ).fetchone()

        self.assertEqual(after_columns, before_columns)
        self.assertEqual(tuple(row), ('gen/0.png', 'IMAGE', 1234, 512, 512))

    def test_the_junction_table_still_references_files(self):
        """`ALTER TABLE ... RENAME` rewrites other tables' REFERENCES clauses
        unless foreign keys are off across the swap - this catches a rebuild
        that leaves generation_files pointing at files_old."""
        _load_migration(self.db).up()

        with self.db.get_connection() as conn:
            junction_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='generation_files'"
            ).fetchone()[0]
            self.assertNotIn("files_old", junction_sql)
            self.assertIn("REFERENCES files(id)", junction_sql)

            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='files_old'"
            ).fetchone())

    def test_indexes_survive_the_rebuild(self):
        """Including one the migration has no hardcoded knowledge of."""
        _load_migration(self.db).up()

        with self.db.get_connection() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='files'"
                )
            }

        self.assertIn('idx_files_file_type', names)
        self.assertIn('idx_files_is_derived', names)

    def test_running_twice_changes_nothing(self):
        module = _load_migration(self.db)
        module.up()
        after_first = self._files_sql()

        module.up()

        self.assertEqual(self._files_sql(), after_first)

    def test_down_narrows_the_constraint_again(self):
        module = _load_migration(self.db)
        module.up()
        self._insert_mesh()

        module.down()

        table_sql = self._files_sql().upper()
        self.assertNotIn("'MESH'", table_sql)
        with self.db.get_connection() as conn:
            self.assertIsNone(
                conn.execute("SELECT id FROM files WHERE file_type='MESH'").fetchone()
            )
            self.assertIsNotNone(
                conn.execute("SELECT id FROM files WHERE id='f1'").fetchone()
            )


if __name__ == '__main__':
    unittest.main()
