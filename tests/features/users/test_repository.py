import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.users.repository import UserRepository
from src.platform.security.user import AccountType

ALL_USERS_GROUP_ID = "all_users"
ALL_ADMINS_GROUP_ID = "all_admins"


class TestUserRepositoryBuiltinGroupMembership(PersistenceTestBase):
    """Every user-creation path must join the built-in ALL_USERS group (and
    ALL_ADMINS for admins) - regression coverage for the bug where only the
    instance-claiming owner was joined."""

    def setUp(self):
        super().setUp()
        self.repo = UserRepository()

    def tearDown(self):
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM user_group_members")
                    cursor.execute("DELETE FROM users")
        except Exception:
            pass
        super().tearDown()

    def _member_group_ids(self, user_id):
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT group_id FROM user_group_members WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {row["group_id"] for row in rows}

    def test_admin_panel_created_user_joins_all_users(self):
        user = self.repo.create(
            username="regular", email="regular@example.com",
            password_hash="hash", account_type=AccountType.USER,
        )

        groups = self._member_group_ids(user.id)
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertNotIn(ALL_ADMINS_GROUP_ID, groups)

    def test_admin_panel_created_admin_joins_both_groups(self):
        user = self.repo.create(
            username="new-admin", email="new-admin@example.com",
            password_hash="hash", account_type=AccountType.ADMIN,
        )

        groups = self._member_group_ids(user.id)
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertIn(ALL_ADMINS_GROUP_ID, groups)

    def test_claiming_owner_joins_both_groups(self):
        user, became_owner = self.repo.create_claiming_instance(
            username="owner", email="owner@example.com", password_hash="hash",
        )

        self.assertTrue(became_owner)
        groups = self._member_group_ids(user.id)
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertIn(ALL_ADMINS_GROUP_ID, groups)

    def test_second_self_registration_joins_all_users_only(self):
        """The regression case: a self-registration that does NOT win the
        instance claim used to get no group membership at all."""
        self.repo.create_claiming_instance(
            username="owner2", email="owner2@example.com", password_hash="hash",
        )
        second_user, became_owner = self.repo.create_claiming_instance(
            username="second", email="second@example.com", password_hash="hash",
        )

        self.assertFalse(became_owner)
        groups = self._member_group_ids(second_user.id)
        self.assertIn(ALL_USERS_GROUP_ID, groups)
        self.assertNotIn(ALL_ADMINS_GROUP_ID, groups)

    def test_missing_builtin_group_does_not_crash_user_creation(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM user_groups WHERE id = ?", (ALL_USERS_GROUP_ID,))

        user = self.repo.create(
            username="orphan", email="orphan@example.com",
            password_hash="hash", account_type=AccountType.USER,
        )  # must not raise

        self.assertEqual(self._member_group_ids(user.id), set())
