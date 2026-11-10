import pytest

from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.operations.access import (
    is_admin_user,
    preset_available_to_user,
    require_preset_access,
)
from src.features.presets.repository import DatabasePresetRepository
from src.platform.security.user import AccountType
from src.platform.util.ids import generate_ulid
from tests.fixtures.persistence_base import PersistenceTestBase
from tests.fixtures.seed import seed_user


class TestPresetAccess(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = DatabasePresetRepository()
        self.preset_db_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO presets (id, preset_id) VALUES (?, ?)", (self.preset_db_id, "family/variant"))
        self.alice = seed_user(self.db, "alice")
        self.bob = seed_user(self.db, "bob")

    def _assign_directly(self, user_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_presets (id, user_id, preset_id) VALUES (?, ?, ?)",
                (generate_ulid(), user_id, self.preset_db_id),
            )

    def _assign_through_group(self, user_id):
        group_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("INSERT INTO user_groups (id, name) VALUES (?, ?)", (group_id, "staff"))
            cursor.execute(
                "INSERT INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
                (generate_ulid(), group_id, user_id),
            )
            cursor.execute(
                "INSERT INTO user_group_presets (id, group_id, preset_id) VALUES (?, ?, ?)",
                (generate_ulid(), group_id, self.preset_db_id),
            )

    def test_unassigned_user_is_refused_as_not_found(self):
        self.assertFalse(preset_available_to_user(self.repo, self.alice, "family/variant", False))
        with pytest.raises(PresetNotFoundException):
            require_preset_access(self.repo, self.alice, "family/variant", False)

    def test_unknown_preset_is_refused_like_an_unassigned_one(self):
        self._assign_directly(self.alice)
        with pytest.raises(PresetNotFoundException):
            require_preset_access(self.repo, self.alice, "family/missing", False)

    def test_direct_assignment_passes(self):
        self._assign_directly(self.alice)
        require_preset_access(self.repo, self.alice, "family/variant", False)
        self.assertFalse(preset_available_to_user(self.repo, self.bob, "family/variant", False))

    def test_group_assignment_passes(self):
        self._assign_through_group(self.alice)
        require_preset_access(self.repo, self.alice, "family/variant", False)
        with pytest.raises(PresetNotFoundException):
            require_preset_access(self.repo, self.bob, "family/variant", False)

    def test_admin_passes_without_assignment(self):
        require_preset_access(self.repo, self.alice, "family/variant", True)
        require_preset_access(self.repo, self.alice, "family/not-installed", True)


def test_is_admin_user_reads_the_account_type():
    class Account:
        def __init__(self, account_type):
            self.account_type = account_type

    assert is_admin_user(Account(AccountType.ADMIN)) is True
    assert is_admin_user(Account(AccountType.USER)) is False
    assert is_admin_user(None) is False
