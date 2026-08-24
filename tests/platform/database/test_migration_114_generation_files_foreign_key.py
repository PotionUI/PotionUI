"""Migration 114 repairs the foreign key migration 035 left pointing at `files_old`.

The broken state is not hand-written here: migration 035's own `up()` is run
against a database with the shape it rebuilds (a CHECK constraint on file_type
without AUDIO), and the damage it does to `generation_files` is asserted before
114 is allowed anywhere near it. A test that only checked the repaired schema
would pass just as happily against a database that was never broken.

Both branches are covered, because only one of them runs on a real install:

- On a database built by the current migration chain, 035's rebuild path never
  fires (migration 010 creates file_type with no CHECK), so nothing references
  `files_old` and 114 must not touch a single table.
- On a database that did go through 035's rebuild, `generation_files` declares
  `REFERENCES "files_old"(id)` against a table 035 then dropped, and every
  insert into it fails. 114 must repoint it at `files` without losing rows,
  columns, or indexes.
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

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)

MIGRATION_NAME = "114_repair_generation_files_foreign_key"

# The pre-035 shape: file_type constrained to IMAGE/VIDEO, which is exactly what
# makes 035 take its rebuild path.
CONSTRAINED_FILES_TABLE = """
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('IMAGE', 'VIDEO')),
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
    updated_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""

JUNCTION_TABLE = """
CREATE TABLE generation_files (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(generation_id, file_id)
)
"""


def _load_migration(stem, database):
    """Import a migration module with its module-level `db` bound to `database`."""
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
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


class TestMigration114OnADatabaseBrokenBy035(unittest.TestCase):
    """The state 114 exists for, produced by running 035 for real."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = _TempDatabase(Path(self.temp_dir) / "test.sqlite").db

        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE generations (id TEXT PRIMARY KEY)")
            conn.execute(CONSTRAINED_FILES_TABLE)
            conn.execute(JUNCTION_TABLE)
            conn.execute(
                "CREATE INDEX idx_generation_files_generation_id"
                " ON generation_files (generation_id)"
            )
            conn.execute("INSERT INTO generations (id) VALUES ('gen1')")
            conn.execute(
                "INSERT INTO files (id, file_path, file_type, file_size)"
                " VALUES ('f1', 'generations/gen1/0.png', 'IMAGE', 1234)"
            )
            conn.execute(
                "INSERT INTO files (id, file_path, file_type, file_size)"
                " VALUES ('f2', 'generations/gen1/1.png', 'IMAGE', 5678)"
            )
            conn.execute(
                "INSERT INTO generation_files (id, generation_id, file_id)"
                " VALUES ('gf1', 'gen1', 'f1')"
            )
            conn.commit()

        _load_migration("035_add_audio_file_type", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _junction_sql(self):
        with self.db.get_connection() as conn:
            return conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='generation_files'"
            ).fetchone()[0]

    def _record_a_generated_file(self, row_id='gf2', file_id='f2'):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                (row_id, 'gen1', file_id),
            )
            conn.commit()

    def test_035_leaves_the_junction_table_pointing_at_a_dropped_table(self):
        """The premise. Without this the repair tests prove nothing."""
        self.assertIn('files_old', self._junction_sql())

        with self.db.get_connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='files_old'"
            ).fetchone())

    def test_recording_a_generated_file_fails_before_the_repair(self):
        """What an operator actually sees: a finished generation cannot record
        its own output rows."""
        with self.assertRaises(sqlite3.OperationalError) as caught:
            self._record_a_generated_file()

        self.assertIn('files_old', str(caught.exception))

    def test_the_foreign_key_points_at_files_after_the_repair(self):
        _load_migration(MIGRATION_NAME, self.db).up()

        junction_sql = self._junction_sql()
        self.assertNotIn('files_old', junction_sql)
        self.assertIn('REFERENCES files(id)', junction_sql)

    def test_recording_a_generated_file_works_after_the_repair(self):
        _load_migration(MIGRATION_NAME, self.db).up()

        self._record_a_generated_file()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT generation_id, file_id FROM generation_files WHERE id='gf2'"
            ).fetchone()
        self.assertEqual(tuple(row), ('gen1', 'f2'))

    def test_the_repaired_foreign_key_is_enforced_not_just_declared(self):
        """A rebuild that dropped the constraint entirely would pass every test
        above. The parent must still reject an orphan and still cascade."""
        _load_migration(MIGRATION_NAME, self.db).up()

        with self.assertRaises(sqlite3.IntegrityError):
            self._record_a_generated_file(row_id='orphan', file_id='does_not_exist')

        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM files WHERE id='f1'")
            conn.commit()
            self.assertIsNone(conn.execute(
                "SELECT id FROM generation_files WHERE id='gf1'"
            ).fetchone())

    def test_existing_rows_and_columns_survive(self):
        with self.db.get_connection() as conn:
            before_columns = [row[1] for row in conn.execute("PRAGMA table_info(generation_files)")]

        _load_migration(MIGRATION_NAME, self.db).up()

        with self.db.get_connection() as conn:
            after_columns = [row[1] for row in conn.execute("PRAGMA table_info(generation_files)")]
            row = conn.execute(
                "SELECT generation_id, file_id FROM generation_files WHERE id='gf1'"
            ).fetchone()

        self.assertEqual(after_columns, before_columns)
        self.assertEqual(tuple(row), ('gen1', 'f1'))

    def test_indexes_survive_the_rebuild(self):
        _load_migration(MIGRATION_NAME, self.db).up()

        with self.db.get_connection() as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                    " AND tbl_name='generation_files'"
                )
            }

        self.assertIn('idx_generation_files_generation_id', names)

    def test_no_scratch_table_is_left_behind(self):
        _load_migration(MIGRATION_NAME, self.db).up()

        with self.db.get_connection() as conn:
            leftovers = conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%_fk_repair_old'"
            ).fetchall()

        self.assertEqual(leftovers, [])

    def test_running_twice_changes_nothing(self):
        module = _load_migration(MIGRATION_NAME, self.db)
        module.up()
        after_first = self._junction_sql()

        module.up()

        self.assertEqual(self._junction_sql(), after_first)


class TestMigration114OnTheRealChain(unittest.TestCase):
    """The branch that runs on every healthy install: nothing to do."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = _TempDatabase(Path(self.temp_dir) / "test.sqlite").db

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

    def test_the_chain_never_produced_a_files_old_reference(self):
        """The premise 114 is insurance against does not hold on a real chain."""
        with self.db.get_connection() as conn:
            offenders = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND sql IS NOT NULL AND sql LIKE '%files_old%'"
            ).fetchall()

        self.assertEqual(offenders, [])

    def test_114_is_a_no_op_here(self):
        """It must not rebuild tables that have nothing wrong with them.

        Comparing schema SQL is not enough - a faithful rebuild produces
        identical SQL. `schema_version` is the counter SQLite bumps on any
        schema change, so it sees a rebuild the SQL text cannot.
        """
        with self.db.get_connection() as conn:
            before_version = conn.execute("PRAGMA schema_version").fetchone()[0]

        _load_migration(MIGRATION_NAME, self.db).up()

        with self.db.get_connection() as conn:
            after_version = conn.execute("PRAGMA schema_version").fetchone()[0]

        self.assertEqual(after_version, before_version, "114 rebuilt a healthy table")


if __name__ == '__main__':
    unittest.main()
