"""Tests for collections holding saved prompts.

Prompts live in the 'prompts' scope tree, separate from 'history'
(generations) and 'library' (uploads) - migration 137's `collection_prompts`
junction mirrors `collection_uploads`, so these assertions mirror
test_upload_members.py: item_count comes from the right junction, a caller
cannot file a prompt they do not own, and a prompt cannot land in the wrong
scope's tree.
"""

import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.collections.repository import CollectionRepository
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import PromptRepository
from src.features.segments.dto import RichSegment

import src.features.prompt_database.repository as prompt_repository_module

HISTORY = "history"
LIBRARY = "library"
PROMPTS = "prompts"


class TestCollectionPromptMembers(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        # PromptRepository binds `db` at import time (see PersistenceTestBase's
        # own redirect list) - it isn't on that list, so it's redirected here.
        prompt_repository_module.db = self.db

        self.repo = CollectionRepository()
        self.prompts = PromptRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _prompt(self, text="a cinematic shot", user_id=None) -> Prompt:
        return self.prompts.create(Prompt(
            user_id=user_id or self.user_id,
            segments=[RichSegment(content=text)],
        ))

    def test_add_and_list_prompt_members(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        prompt = self._prompt()

        added = self.repo.add_prompt_members(collection.id, [prompt.id], self.user_id, PROMPTS)

        self.assertEqual(added, 1)
        self.assertEqual([c.id for c in self.repo.get_for_prompt(prompt.id)], [collection.id])

    def test_add_is_idempotent(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        prompt = self._prompt()

        self.repo.add_prompt_members(collection.id, [prompt.id], self.user_id, PROMPTS)
        second = self.repo.add_prompt_members(collection.id, [prompt.id], self.user_id, PROMPTS)

        self.assertEqual(second, 0)
        self.assertEqual(self.repo.get_by_id(collection.id, PROMPTS).item_count, 1)

    def test_add_refuses_a_prompt_owned_by_someone_else(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        theirs = self._prompt(user_id=self.other_user_id)

        added = self.repo.add_prompt_members(collection.id, [theirs.id], self.user_id, PROMPTS)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_prompt(theirs.id), [])

    def test_add_refuses_a_collection_owned_by_someone_else(self):
        theirs = self.repo.create("Their Folder", self.other_user_id, PROMPTS)
        mine = self._prompt()

        added = self.repo.add_prompt_members(theirs.id, [mine.id], self.user_id, PROMPTS)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_prompt(mine.id), [])

    def test_add_refuses_wrong_scope(self):
        """A History-scope collection cannot accept prompt members."""
        history_folder = self.repo.create("History Folder", self.user_id, HISTORY)
        prompt = self._prompt()

        added = self.repo.add_prompt_members(history_folder.id, [prompt.id], self.user_id, PROMPTS)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_prompt(prompt.id), [])

    def test_remove_prompt_members(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        prompt = self._prompt()
        self.repo.add_prompt_members(collection.id, [prompt.id], self.user_id, PROMPTS)

        removed = self.repo.remove_prompt_members(collection.id, [prompt.id])

        self.assertEqual(removed, 1)
        self.assertEqual(self.repo.get_by_id(collection.id, PROMPTS).item_count, 0)

    def test_item_count_of_prompts_only(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        self.repo.add_prompt_members(
            collection.id,
            [self._prompt("a").id, self._prompt("b").id, self._prompt("c").id],
            self.user_id,
            PROMPTS,
        )

        self.assertEqual(self.repo.get_by_id(collection.id, PROMPTS).item_count, 3)
        listed = {c.id: c for c in self.repo.list(self.user_id, PROMPTS)}
        self.assertEqual(listed[collection.id].item_count, 3)

    def test_deleting_the_collection_drops_prompt_memberships(self):
        collection = self.repo.create("Folder", self.user_id, PROMPTS)
        prompt = self._prompt()
        self.repo.add_prompt_members(collection.id, [prompt.id], self.user_id, PROMPTS)

        self.repo.delete(collection.id, self.user_id, PROMPTS)

        self.assertEqual(self.repo.get_for_prompt(prompt.id), [])
        # The prompt itself is untouched - a folder is a grouping, not an owner.
        self.assertIsNotNone(self.prompts.get_by_id(prompt.id, self.user_id))

    def test_list_is_scope_isolated_from_history_and_library(self):
        self.repo.create("History Folder", self.user_id, HISTORY)
        self.repo.create("Library Folder", self.user_id, LIBRARY)
        self.repo.create("Prompts Folder", self.user_id, PROMPTS)

        prompts_only = self.repo.list(self.user_id, PROMPTS)

        self.assertEqual([c.name for c in prompts_only], ["Prompts Folder"])


if __name__ == '__main__':
    unittest.main()
