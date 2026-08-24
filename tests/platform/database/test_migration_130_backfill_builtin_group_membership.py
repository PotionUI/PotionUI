"""Migration 130 backfills ALL_USERS/ALL_ADMINS membership for users created
before `UserRepository._join_builtin_groups` became unconditional - see the
migration's docstring.
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

ALL_USERS_GROUP_ID = "all_users"
ALL_ADMINS_GROUP_ID = "all_admins"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration130(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("007_create_users", self.db).up()
        _load_migration("039_create_user_groups", self.db).up()
        _load_migration("095_seed_default_user_groups", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _insert_user(self, user_id, account_type="USER"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) "
                "VALUES (?, ?, ?, 'hash', ?)",
                (user_id, f"user-{user_id}", f"{user_id}@example.com", account_type),
            )

    def _member_group_ids(self, user_id):
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT group_id FROM user_group_members WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {row["group_id"] for row in rows}

    def test_backfills_all_users_for_regular_user(self):
        self._insert_user("regular-1", "USER")

        _load_migration("130_backfill_builtin_group_membership", self.db).up()

        groups = self._member_group_ids("regular-1")
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertNotIn(ALL_ADMINS_GROUP_ID, groups)

    def test_backfills_both_groups_for_admin_user(self):
        self._insert_user("admin-1", "ADMIN")

        _load_migration("130_backfill_builtin_group_membership", self.db).up()

        groups = self._member_group_ids("admin-1")
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertIn(ALL_ADMINS_GROUP_ID, groups)

    def test_is_idempotent(self):
        self._insert_user("regular-2", "USER")
        migration = _load_migration("130_backfill_builtin_group_membership", self.db)

        migration.up()
        migration.up()  # must not raise UNIQUE(group_id, user_id)

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM user_group_members "
                "WHERE user_id = ? AND group_id = ?",
                ("regular-2", ALL_USERS_GROUP_ID),
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_does_not_touch_existing_membership(self):
        self._insert_user("already-1", "USER")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_group_members (id, group_id, user_id) VALUES ('m1', ?, ?)",
                (ALL_USERS_GROUP_ID, "already-1"),
            )

        _load_migration("130_backfill_builtin_group_membership", self.db).up()

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM user_group_members WHERE user_id = ?",
                ("already-1",),
            ).fetchone()["c"]
        self.assertEqual(count, 1)

class TestMigration130GroupMissing(unittest.TestCase):
    """039_create_user_groups also creates user_group_presets/user_group_llms,
    whose FKs point at presets/llm_configurations - tables this lightweight
    schema never loads. Deleting a user_groups row (as the "missing group"
    case would) forces SQLite to resolve those FK targets and raises "no such
    table" even though no row is affected, so this scenario uses a minimal
    schema that never seeds the group in the first place instead of the
    shared 039/095 setUp above.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("007_create_users", self.db).up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "CREATE TABLE user_groups (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL)"
            )
            cursor.execute("""
                CREATE TABLE user_group_members (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(group_id, user_id)
                )
            """)

    def tearDown(self):
        Database._instance = None

    def test_skips_gracefully_when_all_users_group_missing(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) "
                "VALUES ('orphan-1', 'orphan', 'orphan@example.com', 'hash', 'USER')"
            )

        _load_migration("130_backfill_builtin_group_membership", self.db).up()  # must not raise

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM user_group_members WHERE user_id = 'orphan-1'"
            ).fetchone()["c"]
        self.assertEqual(count, 0)
