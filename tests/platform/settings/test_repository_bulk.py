"""Atomicity tests for SettingRepository.apply_bulk_updates.

A bulk settings update must be all-or-nothing: either every write commits, or a
mid-batch failure rolls the whole batch back so no key is left half-applied.

These run against the real ``Database`` (a migrated temp-file SQLite, one fresh
transactional connection per cursor) rather than the shared in-memory test db,
whose post-migration connection is in autocommit and so cannot exercise a real
rollback.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.plugins.records import Plugin  # noqa: F401 (keep import graph identical to peers)
from src.platform.settings.repository import SettingRepository


class TestApplyBulkUpdates(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = SettingRepository()

    def test_commits_system_and_user_together(self):
        user_id = self.create_test_user()
        models_dir = self.repo.get_setting_by_key("models_dir")
        nsfw = self.repo.get_setting_by_key("nsfw_filter")

        self.repo.apply_bulk_updates(
            system_updates=[(models_dir.id, "brand-new-models")],
            user_updates=[(user_id, nsfw.id, "true")],
        )

        self.assertEqual(self.repo.get_setting_by_key("models_dir").value, "brand-new-models")
        self.assertEqual(self.repo.get_user_setting(user_id, nsfw.id).value, "true")

    def test_rolls_back_whole_batch_on_mid_batch_failure(self):
        """A later write that violates a FK (unknown user_id) must undo the system
        UPDATE that ran first - nothing persists."""
        models_dir = self.repo.get_setting_by_key("models_dir")
        nsfw = self.repo.get_setting_by_key("nsfw_filter")
        original = models_dir.value

        with self.assertRaises(Exception):
            self.repo.apply_bulk_updates(
                system_updates=[(models_dir.id, "should-be-rolled-back")],
                user_updates=[("ghost-user-does-not-exist", nsfw.id, "true")],
            )

        self.assertEqual(self.repo.get_setting_by_key("models_dir").value, original)
