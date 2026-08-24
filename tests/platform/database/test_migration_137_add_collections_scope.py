"""Migration 137 adds `collections.scope` and splits every pre-existing
collection by its membership content: generations-only (or empty) collections
become 'history', uploads-only collections become 'library', and a collection
holding both is split into a 'history' original plus a same-named 'library'
clone that takes over its upload memberships. Cross-scope parent/child pairs
are re-rooted to the collections root rather than left spanning scopes.
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


class TestMigration137(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _create_pre_137_schema(self):
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    parent_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE collection_generations (
                    collection_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (collection_id, generation_id)
                )
            """)
            conn.execute("""
                CREATE TABLE collection_uploads (
                    collection_id TEXT NOT NULL,
                    upload_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (collection_id, upload_id)
                )
            """)
            conn.execute("INSERT INTO users (id, username) VALUES ('user-1', 'alice')")
            conn.commit()

    def _collection(self, conn, cid, name, parent_id=None, user_id="user-1"):
        conn.execute(
            "INSERT INTO collections (id, name, user_id, parent_id) VALUES (?, ?, ?, ?)",
            (cid, name, user_id, parent_id),
        )

    def _generation_member(self, conn, cid, gen_id):
        conn.execute(
            "INSERT INTO collection_generations (collection_id, generation_id) VALUES (?, ?)",
            (cid, gen_id),
        )

    def _upload_member(self, conn, cid, upload_id):
        conn.execute(
            "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)",
            (cid, upload_id),
        )

    def _rows(self, conn):
        return {
            row["id"]: dict(row)
            for row in conn.execute("SELECT id, name, parent_id, scope FROM collections")
        }

    # --- The four membership cases ---

    def test_generations_only_becomes_history(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Album")
            self._generation_member(conn, "c1", "gen-1")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            self.assertEqual(self._rows(conn)["c1"]["scope"], "history")

    def test_no_memberships_becomes_history(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Empty")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            self.assertEqual(self._rows(conn)["c1"]["scope"], "history")

    def test_uploads_only_becomes_library(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Pics")
            self._upload_member(conn, "c1", "up-1")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            self.assertEqual(self._rows(conn)["c1"]["scope"], "library")

    def test_mixed_membership_clones_without_duplicating(self):
        """A collection with both kinds of membership splits into a 'history'
        original and a same-named 'library' clone - and the upload row is
        *moved* to the clone, not duplicated onto both."""
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Mixed")
            self._generation_member(conn, "c1", "gen-1")
            self._upload_member(conn, "c1", "up-1")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            rows = self._rows(conn)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows["c1"]["scope"], "history")
            self.assertEqual(rows["c1"]["name"], "Mixed")

            clone_id = next(cid for cid in rows if cid != "c1")
            self.assertEqual(rows[clone_id]["scope"], "library")
            self.assertEqual(rows[clone_id]["name"], "Mixed")

            # Membership moved, not duplicated: exactly one collection_uploads
            # row total, pointing at the clone.
            upload_rows = conn.execute(
                "SELECT collection_id FROM collection_uploads WHERE upload_id = 'up-1'"
            ).fetchall()
            self.assertEqual(len(upload_rows), 1)
            self.assertEqual(upload_rows[0]["collection_id"], clone_id)

            # The generation membership stays on the original, untouched.
            gen_rows = conn.execute(
                "SELECT collection_id FROM collection_generations WHERE generation_id = 'gen-1'"
            ).fetchall()
            self.assertEqual(len(gen_rows), 1)
            self.assertEqual(gen_rows[0]["collection_id"], "c1")

    # --- Parent/nesting re-rooting ---

    def test_cross_scope_child_is_rerooted(self):
        """A history parent with a library-only child: the child is detached
        to root rather than left hanging off a foreign-scope tree."""
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "parent", "History Parent")
            self._generation_member(conn, "parent", "gen-1")
            self._collection(conn, "child", "Library Child", parent_id="parent")
            self._upload_member(conn, "child", "up-1")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            rows = self._rows(conn)
            self.assertEqual(rows["parent"]["scope"], "history")
            self.assertEqual(rows["child"]["scope"], "library")
            self.assertIsNone(rows["child"]["parent_id"])

    def test_same_scope_child_keeps_its_parent(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "parent", "History Parent")
            self._generation_member(conn, "parent", "gen-1")
            self._collection(conn, "child", "History Child", parent_id="parent")
            self._generation_member(conn, "child", "gen-2")
            conn.commit()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            rows = self._rows(conn)
            self.assertEqual(rows["child"]["parent_id"], "parent")

    # --- Idempotency / down() ---

    # --- collection_prompts junction ---

    def test_creates_collection_prompts_table(self):
        self._create_pre_137_schema()

        _load_migration("137_add_collections_scope", self.db).up()

        with self.db.get_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn("collection_prompts", tables)

    def test_collection_prompts_survives_a_second_up(self):
        self._create_pre_137_schema()
        migration = _load_migration("137_add_collections_scope", self.db)
        migration.up()

        with self.db.get_connection() as conn:
            # collection_prompts' FK targets `prompts`, and this connection
            # has foreign_keys enforcement on - a bare row is enough to
            # satisfy it.
            conn.execute("CREATE TABLE prompts (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO prompts (id) VALUES ('prompt-1')")
            conn.execute(
                "INSERT INTO collections (id, name, user_id, scope) VALUES ('cp1', 'Prompts Folder', 'user-1', 'prompts')"
            )
            conn.execute(
                "INSERT INTO collection_prompts (collection_id, prompt_id) VALUES ('cp1', 'prompt-1')"
            )
            conn.commit()

        migration.up()  # must not raise, must not drop/recreate the table

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT prompt_id FROM collection_prompts WHERE collection_id = 'cp1'"
            ).fetchone()
            self.assertEqual(row["prompt_id"], "prompt-1")

    def test_down_drops_collection_prompts(self):
        self._create_pre_137_schema()
        migration = _load_migration("137_add_collections_scope", self.db)
        migration.up()
        migration.down()

        with self.db.get_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertNotIn("collection_prompts", tables)

    def test_up_is_idempotent(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Mixed")
            self._generation_member(conn, "c1", "gen-1")
            self._upload_member(conn, "c1", "up-1")
            conn.commit()

        migration = _load_migration("137_add_collections_scope", self.db)
        migration.up()
        with self.db.get_connection() as conn:
            count_after_first = len(self._rows(conn))
        migration.up()  # must not raise, must not clone again

        with self.db.get_connection() as conn:
            self.assertEqual(len(self._rows(conn)), count_after_first)

    def test_down_merges_clone_back_into_history_sibling(self):
        self._create_pre_137_schema()
        with self.db.get_connection() as conn:
            self._collection(conn, "c1", "Mixed")
            self._generation_member(conn, "c1", "gen-1")
            self._upload_member(conn, "c1", "up-1")
            conn.commit()

        migration = _load_migration("137_add_collections_scope", self.db)
        migration.up()
        migration.down()

        with self.db.get_connection() as conn:
            rows = self._rows(conn)
            self.assertEqual(list(rows.keys()), ["c1"])
            upload_rows = conn.execute(
                "SELECT collection_id FROM collection_uploads WHERE upload_id = 'up-1'"
            ).fetchall()
            self.assertEqual(upload_rows[0]["collection_id"], "c1")


if __name__ == '__main__':
    unittest.main()
