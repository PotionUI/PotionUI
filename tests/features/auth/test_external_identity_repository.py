import io
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

from src.features.auth.repository import ExternalIdentityRepository
from src.features.users.repository import UserRepository
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.security.user import AccountType


class TestExternalIdentityRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
        ]
        for patcher in self._patchers:
            patcher.start()

        self._run_migrations()

        self.repo = ExternalIdentityRepository()
        self.users = UserRepository()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        for leftover in Path(self.temp_dir).iterdir():
            leftover.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _run_migrations(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

    def _make_user(self, username):
        return self.users.create(
            username=username,
            email=f"{username}@example.com",
            password_hash="hash",
            account_type=AccountType.USER,
        )

    def test_table_exists_after_migration(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='external_identities'"
            )
            self.assertIsNotNone(cursor.fetchone())

    def test_get_returns_none_when_unmapped(self):
        self.assertIsNone(self.repo.get("https://idp.example", "sub-1"))

    def test_create_then_get_round_trips(self):
        user = self._make_user("alice")

        created = self.repo.create("https://idp.example", "sub-1", user.id)

        self.assertEqual(created.issuer, "https://idp.example")
        self.assertEqual(created.subject, "sub-1")
        self.assertEqual(created.user_id, user.id)

        fetched = self.repo.get("https://idp.example", "sub-1")
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.user_id, user.id)

    def test_timestamps_are_stored_as_aware_utc(self):
        user = self._make_user("bob")

        created = self.repo.create("https://idp.example", "sub-2", user.id)

        self.assertIsNotNone(created.created_at)
        self.assertIsNotNone(created.last_login_at)
        self.assertEqual(created.created_at.tzinfo, timezone.utc)
        self.assertEqual(created.last_login_at.tzinfo, timezone.utc)

    def test_issuer_and_subject_pair_is_unique(self):
        first = self._make_user("carol")
        second = self._make_user("dave")
        self.repo.create("https://idp.example", "shared-sub", first.id)

        with self.assertRaises(Exception):
            self.repo.create("https://idp.example", "shared-sub", second.id)

    def test_same_subject_under_a_different_issuer_is_a_separate_mapping(self):
        user = self._make_user("erin")

        first = self.repo.create("https://idp-a.example", "sub-9", user.id)
        second = self.repo.create("https://idp-b.example", "sub-9", user.id)

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(
            self.repo.get("https://idp-b.example", "sub-9").id, second.id
        )

    def test_touch_last_login_advances_the_stamp(self):
        user = self._make_user("frank")
        created = self.repo.create("https://idp.example", "sub-3", user.id)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE external_identities SET last_login_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", created.id),
            )

        touched = self.repo.touch_last_login(created.id)

        self.assertGreater(touched.last_login_at.year, 2020)
        self.assertEqual(touched.created_at, created.created_at)

    def test_touch_last_login_returns_none_for_an_unknown_mapping(self):
        self.assertIsNone(self.repo.touch_last_login("does-not-exist"))

    def test_list_for_user_returns_only_that_users_mappings(self):
        user = self._make_user("grace")
        other = self._make_user("heidi")
        self.repo.create("https://idp-a.example", "sub-a", user.id)
        self.repo.create("https://idp-b.example", "sub-b", user.id)
        self.repo.create("https://idp-a.example", "sub-c", other.id)

        mappings = self.repo.list_for_user(user.id)

        self.assertEqual(len(mappings), 2)
        self.assertEqual({m.user_id for m in mappings}, {user.id})

    def test_delete_removes_the_mapping(self):
        user = self._make_user("ivan")
        created = self.repo.create("https://idp.example", "sub-4", user.id)

        self.assertTrue(self.repo.delete(created.id))
        self.assertIsNone(self.repo.get("https://idp.example", "sub-4"))
        self.assertFalse(self.repo.delete(created.id))

    def test_deleting_the_user_cascades_to_the_mapping(self):
        user = self._make_user("judy")
        self.repo.create("https://idp.example", "sub-5", user.id)

        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("DELETE FROM users WHERE id = ?", (user.id,))

        self.assertIsNone(self.repo.get("https://idp.example", "sub-5"))


if __name__ == "__main__":
    unittest.main()
