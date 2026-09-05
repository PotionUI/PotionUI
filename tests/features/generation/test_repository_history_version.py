"""`GenerationRepository.history_version` - the history change-detection token.

The token has to move for every write that changes what the history list would
render, and stay put when nothing changed. The facade twin is covered here too,
since it is the path the route actually takes.
"""

import asyncio
import os
import sys
from unittest.mock import Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.collections.repository import collection_repo
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
from src.features.tags.repository import tag_repo
from src.platform.util.ids import generate_ulid


class TestHistoryVersionToken(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    def tearDown(self):
        facade = getattr(self, 'facade', None)
        if facade is not None:
            facade.shutdown()
        super().tearDown()

    def _create(self, status="pending"):
        return self.repo.create(Generation(
            id=generate_ulid(),
            preset_id="native/SDXL/realistic",
            form_data={"prompt": "a prompt"},
            user_id=self.user_id,
            status=status,
        ))

    def test_token_is_stable_while_nothing_changes(self):
        self._create()
        first = self.repo.history_version(self.user_id)
        self.assertEqual(first, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_generation_is_created(self):
        before = self.repo.history_version(self.user_id)
        self._create()
        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_generation_reaches_a_terminal_state(self):
        generation = self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.update_status(generation.id, 'completed')

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_generation_is_deleted(self):
        generation = self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.delete(generation.id)

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_is_scoped_to_one_user(self):
        other_user = self.create_test_user("other-user", "otheruser", "other@example.com")
        before = self.repo.history_version(self.user_id)

        self.repo.create(Generation(
            id=generate_ulid(),
            preset_id="native/SDXL/realistic",
            form_data={"prompt": "someone else's"},
            user_id=other_user,
            status="pending",
        ))

        self.assertEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_generation_is_favorited(self):
        generation = self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.set_favorite(generation.id, True, user_id=self.user_id)

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_generation_is_rated(self):
        generation = self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.update_rating(generation.id, 4, user_id=self.user_id)

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_tag_is_added_and_removed(self):
        generation = self._create()
        tag = tag_repo.create_tag("keepers", type="GENERATION", user_id=self.user_id)

        before = self.repo.history_version(self.user_id)
        tag_repo.add_tag_to_generation(generation.id, tag.id)
        after_add = self.repo.history_version(self.user_id)
        self.assertNotEqual(before, after_add)

        tag_repo.remove_tag_from_generation(generation.id, tag.id)
        self.assertNotEqual(after_add, self.repo.history_version(self.user_id))

    def test_token_changes_when_tags_are_replaced_wholesale(self):
        generation = self._create()
        tag = tag_repo.create_tag("wholesale", type="GENERATION", user_id=self.user_id)
        before = self.repo.history_version(self.user_id)

        tag_repo.set_generation_tags(generation.id, [tag.id])

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_collection_membership_changes(self):
        generation = self._create()
        collection = collection_repo.create("Keepers", self.user_id, scope="history")

        before = self.repo.history_version(self.user_id)
        added = collection_repo.add_members(
            collection.id, [generation.id], self.user_id, scope="history"
        )
        self.assertEqual(added, 1)
        after_add = self.repo.history_version(self.user_id)
        self.assertNotEqual(before, after_add)

        collection_repo.remove_members(collection.id, [generation.id])
        self.assertNotEqual(after_add, self.repo.history_version(self.user_id))

    def test_token_changes_when_a_file_is_attached(self):
        generation = self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.add_file(generation.id, File(
            id=generate_ulid(),
            file_path="outputs/test/result.png",
            file_type="IMAGE",
            file_size=1024,
            user_id=self.user_id,
        ))

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_progress_ticks_do_not_move_the_token(self):
        generation = self._create()
        self.repo.update_status(generation.id, 'running')
        before = self.repo.history_version(self.user_id)

        self.repo.update_progress(generation.id, 0.25)
        self.repo.update_progress(generation.id, 0.75)

        # Progress reaches an open tab over its own WebSocket subscription, so
        # bumping here would refetch the whole page on every tick.
        self.assertEqual(before, self.repo.history_version(self.user_id))

    def test_another_users_metadata_change_leaves_this_token_alone(self):
        other_user = self.create_test_user("other-2", "other2", "other2@example.com")
        theirs = self.repo.create(Generation(
            id=generate_ulid(),
            preset_id="native/SDXL/realistic",
            form_data={"prompt": "theirs"},
            user_id=other_user,
            status="pending",
        ))
        self._create()
        before = self.repo.history_version(self.user_id)

        self.repo.set_favorite(theirs.id, True, user_id=other_user)

        self.assertEqual(before, self.repo.history_version(self.user_id))

    def test_token_changes_when_boot_reconciles_interrupted_generations(self):
        self._create(status="running")
        before = self.repo.history_version(self.user_id)

        self.assertEqual(self.repo.reconcile_interrupted_generations(), 1)

        self.assertNotEqual(before, self.repo.history_version(self.user_id))

    def test_facade_twin_returns_the_repository_token(self):
        self.facade = GenerationHistoryFacade(
            generation_repo=self.repo,
            file_service=Mock(),
            plugin_registry=Mock(),
        )
        self._create()

        token = asyncio.run(self.facade.get_history_version_async(self.user_id))

        self.assertEqual(token, self.repo.history_version(self.user_id))
