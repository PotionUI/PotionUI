"""Migration 112 repairs a database that reached it through 035's *recovery*
branch, not just its normal one.

035 has two branches that both leave `files.file_type` constrained to
`('IMAGE', 'VIDEO', 'AUDIO')`:

- normal: `files` exists with a CHECK missing AUDIO -> renamed to `files_old`,
  a fresh `files` created, `files_old` dropped. Covered by
  `test_migration_112_mesh_file_type.py`'s `TestMigration112OnAConstrainedDatabase`
  and by migration 114's own tests (which drive this branch for real to prove
  its *different* defect - the dangling `files_old` foreign key).
- recovery: `files_old` exists but `files` does not (a prior run of 035 was
  interrupted after the rename but before the table was rebuilt). 035 recovers
  by creating `files` straight from `files_old`'s columns, with the same
  AUDIO-only CHECK, and drops `files_old`. Nothing else in this branch touches
  `ALTER TABLE ... RENAME`, so it does not produce a dangling foreign key -
  that is exclusively the normal branch's defect, which is what migration 114
  exists for. This file only checks 112's CHECK-widening concern, so it never
  creates a table that would need 114's repair.

The existing 112 suite exercises the *shape* the recovery branch leaves behind
via a hand-written CREATE TABLE, not the recovery branch itself. This drives
035's recovery branch for real - `files_old` present, `files` absent, before
`up()` runs - so what 112 repairs here is 035's actual output, not a
lookalike.
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

# The shape a `files_old` left behind by an interrupted 035 run would have:
# the pre-AUDIO schema, unconstrained-or-not does not matter to the recovery
# branch (it never reads files_old's CHECK, only its columns).
FILES_OLD_TABLE = """
CREATE TABLE files_old (
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
    updated_at TIMESTAMP
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


class TestMigration112AfterMigration035RecoveryBranch(unittest.TestCase):
    """The recovery-branch shape: `files_old` present, `files` absent."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = _TempDatabase(Path(self.temp_dir) / "test.sqlite").db

        with self.db.get_connection() as conn:
            # The recovery branch's rebuilt `files` declares a FK to `users`;
            # SQLite checks the referenced table exists at insert time even for
            # a NULL user_id, once `PRAGMA foreign_keys = ON` (which every
            # connection here carries).
            conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.execute(FILES_OLD_TABLE)
            conn.execute(
                "INSERT INTO files_old (id, file_path, file_type, file_size, width, height)"
                " VALUES ('f1', 'gen/0.png', 'IMAGE', 1234, 512, 512)"
            )
            conn.commit()

        _load_migration("035_add_audio_file_type", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _files_sql(self):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
            ).fetchone()
            return row[0] if row else None

    def _insert_mesh(self, file_id='f_mesh'):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO files (id, file_path, file_type, file_size) VALUES (?, ?, ?, ?)",
                (file_id, 'gen/0.glb', 'MESH', 600),
            )
            conn.commit()

    def test_035_actually_took_the_recovery_branch(self):
        """The premise. Without this, the widening test below proves nothing."""
        with self.db.get_connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='files_old'"
            ).fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='files'"
            ).fetchone())

        files_sql = self._files_sql().upper()
        self.assertIn("CHECK", files_sql)
        for value in ("'IMAGE'", "'VIDEO'", "'AUDIO'"):
            self.assertIn(value, files_sql)
        self.assertNotIn("'MESH'", files_sql)

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT file_path, file_type, width FROM files WHERE id='f1'").fetchone()
        self.assertEqual(tuple(row), ('gen/0.png', 'IMAGE', 512))

    def test_mesh_is_rejected_before_112(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_mesh()

    def test_mesh_is_accepted_after_112(self):
        _load_migration("112_add_mesh_file_type", self.db).up()

        self._insert_mesh()

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT file_type FROM files WHERE id='f_mesh'").fetchone()
        self.assertEqual(row[0], 'MESH')

    def test_the_constraint_is_widened_not_dropped(self):
        _load_migration("112_add_mesh_file_type", self.db).up()

        files_sql = self._files_sql().upper()
        self.assertIn("CHECK", files_sql)
        for value in ("'IMAGE'", "'VIDEO'", "'AUDIO'", "'MESH'"):
            self.assertIn(value, files_sql)

        with self.db.get_connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO files (id, file_path, file_type) VALUES ('bad', 'x', 'NONSENSE')"
                )

    def test_the_row_recovered_from_files_old_survives_112_too(self):
        _load_migration("112_add_mesh_file_type", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT file_path, file_type, width, height FROM files WHERE id='f1'"
            ).fetchone()
        self.assertEqual(tuple(row), ('gen/0.png', 'IMAGE', 512, 512))

    def test_no_files_old_is_left_behind_by_either_migration(self):
        """112 must not resurrect the table 035's recovery branch already
        cleaned up, and must not collide with what 114 fixes for a different
        035 defect (a dangling `files_old` foreign key, which this branch
        never produces in the first place)."""
        _load_migration("112_add_mesh_file_type", self.db).up()

        with self.db.get_connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='files_old'"
            ).fetchone())


if __name__ == '__main__':
    unittest.main()
