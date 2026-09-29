from datetime import datetime
from unittest.mock import Mock

import pytest

from src.features.content_safety.constants import RESTRICTED_GROUP_ID
from src.features.user_groups import operations
from src.features.user_groups.dto import GroupCreate, GroupUpdate
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import PluginRegistry
from src.platform.security.user import AccountType, User
from tests.fixtures.persistence_base import PersistenceTestBase


def admin():
    return User(
        id="admin-1", username="admin", email="a@example.com", password_hash="h",
        account_type=AccountType.ADMIN, created_at=datetime.utcnow(), last_login=None,
    )


def plugins():
    registry = Mock(spec=PluginRegistry)
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


class TestGroupContentPolicy(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = UserGroupRepository()

    def test_the_restricted_group_is_seeded_as_a_blocked_system_group(self):
        group = self.repo.get_group_by_id(RESTRICTED_GROUP_ID)

        assert group.name == "Restricted content"
        assert group.is_system is True
        assert group.content_policy == "blocked"

    def test_the_restricted_group_shows_up_in_the_admin_group_listing(self):
        listing = operations.get_all_groups(self.repo, admin())

        by_id = {group.id: group for group in listing}
        assert by_id[RESTRICTED_GROUP_ID].content_policy == "blocked"
        assert by_id[RESTRICTED_GROUP_ID].restricted is True
        assert by_id["all_users"].content_policy is None
        assert by_id["all_users"].restricted is False

    def test_a_group_can_be_created_with_a_policy(self):
        created = operations.create_group(
            self.repo, plugins(), GroupCreate(name="Trusted", content_policy="allowed"), admin()
        )

        assert created.content_policy == "allowed"
        assert self.repo.get_group_by_id(created.id).content_policy == "allowed"

    def test_the_policy_can_be_changed_and_cleared(self):
        created = operations.create_group(self.repo, plugins(), GroupCreate(name="Mid"), admin())

        set_to = operations.update_group(
            self.repo, plugins(), created.id, GroupUpdate(content_policy="blur"), admin()
        )
        cleared = operations.update_group(
            self.repo, plugins(), created.id, GroupUpdate(content_policy=""), admin()
        )

        assert set_to.content_policy == "blur"
        assert cleared.content_policy is None

    def test_updating_only_the_name_keeps_the_policy(self):
        created = operations.create_group(
            self.repo, plugins(), GroupCreate(name="Keep", content_policy="blocked"), admin()
        )

        renamed = operations.update_group(
            self.repo, plugins(), created.id, GroupUpdate(name="Kept"), admin()
        )

        assert renamed.content_policy == "blocked"

    def test_the_restricted_groups_policy_cannot_be_changed(self):
        with pytest.raises(ValueError):
            operations.update_group(
                self.repo, plugins(), RESTRICTED_GROUP_ID, GroupUpdate(content_policy="allowed"), admin()
            )

        assert self.repo.get_group_by_id(RESTRICTED_GROUP_ID).content_policy == "blocked"

    def test_the_restricted_group_cannot_be_deleted(self):
        with pytest.raises(operations.SystemGroupProtectedError):
            operations.delete_group(self.repo, plugins(), RESTRICTED_GROUP_ID, admin())

    def test_an_unknown_policy_value_is_rejected_by_the_request_model(self):
        with pytest.raises(ValueError):
            GroupUpdate(content_policy="sometimes")

    def test_membership_feeds_the_user_group_lookup_with_policies(self):
        user_id = self.create_test_user("kid", "kid", "kid@example.com")
        self.repo.add_user_to_group(RESTRICTED_GROUP_ID, user_id)

        groups = self.repo.get_user_groups(user_id)

        assert [(g.id, g.content_policy) for g in groups] == [(RESTRICTED_GROUP_ID, "blocked")]
