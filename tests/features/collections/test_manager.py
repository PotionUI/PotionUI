import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.collections.repository import CollectionRepository
from src.features.collections.manager import CollectionManager

HISTORY = "history"
LIBRARY = "library"
PROMPTS = "prompts"


class TestCollectionManagerBulkMove(PersistenceTestBase):

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = CollectionRepository()
        self.manager = CollectionManager(self.repo)
        self.test_user_id = self.create_test_user()

    def test_bulk_move_happy_path(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY)
        target = self.repo.create("Target", self.test_user_id, HISTORY)

        result = self.manager.bulk_move_collections([a.id, b.id], target.id, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.repo.get_by_id(a.id, HISTORY).parent_id, target.id)
        self.assertEqual(self.repo.get_by_id(b.id, HISTORY).parent_id, target.id)

    def test_bulk_move_to_root(self):
        parent = self.repo.create("Parent", self.test_user_id, HISTORY)
        a = self.repo.create("A", self.test_user_id, HISTORY, parent_id=parent.id)
        b = self.repo.create("B", self.test_user_id, HISTORY, parent_id=parent.id)

        result = self.manager.bulk_move_collections([a.id, b.id], None, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertIsNone(self.repo.get_by_id(a.id, HISTORY).parent_id)
        self.assertIsNone(self.repo.get_by_id(b.id, HISTORY).parent_id)

    def test_bulk_move_rejects_cycle_and_is_never_applied(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        child = self.repo.create("Child", self.test_user_id, HISTORY, parent_id=a.id)
        other = self.repo.create("Other", self.test_user_id, HISTORY)

        # Moving A under its own child (a cycle) alongside an unrelated valid
        # move: the cycle-forming id must fail without it, and its parent
        # must stay put. The unrelated id is unaffected.
        result = self.manager.bulk_move_collections([a.id, other.id], child.id, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], a.id)
        # Never applied - A's parent is unchanged.
        self.assertIsNone(self.repo.get_by_id(a.id, HISTORY).parent_id)
        self.assertEqual(self.repo.get_by_id(other.id, HISTORY).parent_id, child.id)

    def test_bulk_move_rejects_target_inside_selection(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY)

        # Target is itself one of the ids being moved: B moving into itself
        # is a cycle and is rejected; A moving into B is an unrelated,
        # perfectly ordinary move and succeeds.
        result = self.manager.bulk_move_collections([a.id, b.id], b.id, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], b.id)
        self.assertEqual(self.repo.get_by_id(a.id, HISTORY).parent_id, b.id)
        self.assertIsNone(self.repo.get_by_id(b.id, HISTORY).parent_id)

    def test_bulk_move_ownership_violation(self):
        mine = self.repo.create("Mine", self.test_user_id, HISTORY)
        self.create_test_user(user_id="user_2", username="user2", email="user2@example.com")
        theirs = self.repo.create("Theirs", "user_2", HISTORY)
        target = self.repo.create("Target", self.test_user_id, HISTORY)

        result = self.manager.bulk_move_collections([mine.id, theirs.id], target.id, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], theirs.id)
        self.assertEqual(self.repo.get_by_id(mine.id, HISTORY).parent_id, target.id)
        # Untouched - still owned by user_2, still at root.
        self.assertIsNone(self.repo.get_by_id(theirs.id, HISTORY, user_id="user_2").parent_id)

    def test_bulk_move_partial_failure_reporting(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY)
        target = self.repo.create("Target", self.test_user_id, HISTORY)

        result = self.manager.bulk_move_collections(
            [a.id, "missing-id", b.id], target.id, self.test_user_id, HISTORY
        )

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], "missing-id")
        self.assertIn("reason", result["errors"][0])

    def test_bulk_move_rejects_cross_scope_target(self):
        """A Library collection can never become the parent of a History move."""
        a = self.repo.create("A", self.test_user_id, HISTORY)
        library_target = self.repo.create("Library Target", self.test_user_id, LIBRARY)

        result = self.manager.bulk_move_collections([a.id], library_target.id, self.test_user_id, HISTORY)

        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertIsNone(self.repo.get_by_id(a.id, HISTORY).parent_id)


class TestCollectionManagerScope(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = CollectionRepository()
        self.manager = CollectionManager(self.repo)
        self.test_user_id = self.create_test_user()

    def test_move_rejects_cross_scope_target(self):
        history_folder = self.manager.create_collection("History Folder", self.test_user_id, HISTORY)
        library_folder = self.manager.create_collection("Library Folder", self.test_user_id, LIBRARY)

        with self.assertRaises(ValueError):
            self.manager.move_collection(history_folder.id, library_folder.id, self.test_user_id, HISTORY)

    def test_create_rejects_cross_scope_parent(self):
        library_folder = self.manager.create_collection("Library Folder", self.test_user_id, LIBRARY)

        with self.assertRaises(ValueError):
            self.manager.create_collection(
                "Child", self.test_user_id, HISTORY, parent_id=library_folder.id
            )

    def test_list_collections_is_scope_isolated(self):
        self.manager.create_collection("History Folder", self.test_user_id, HISTORY)
        self.manager.create_collection("Library Folder", self.test_user_id, LIBRARY)

        history = self.manager.list_collections(self.test_user_id, HISTORY)
        library = self.manager.list_collections(self.test_user_id, LIBRARY)

        self.assertEqual([c.name for c in history], ["History Folder"])
        self.assertEqual([c.name for c in library], ["Library Folder"])

    def test_create_collection_in_prompts_scope(self):
        collection = self.manager.create_collection("Saved Prompts", self.test_user_id, PROMPTS)
        self.assertEqual(collection.scope, PROMPTS)

    def test_add_prompt_members_rejects_a_history_scope_collection(self):
        """A caller asking to add prompt members under scope='prompts' can
        never reach a History-scope collection - the lookup that resolves
        ownership is the same one that enforces scope, so a real History
        folder's id simply isn't found under a 'prompts' lookup."""
        history_folder = self.manager.create_collection("History Folder", self.test_user_id, HISTORY)

        with self.assertRaises(ValueError):
            self.manager.add_prompt_members(history_folder.id, ["prompt-1"], self.test_user_id, PROMPTS)

    def test_add_prompt_members_rejects_a_declared_scope_that_does_not_match(self):
        """The converse: a real Prompts-scope collection is also unreachable
        if the caller declares the wrong scope - scope isn't inferred from
        the collection, it's asserted by the caller and checked against it."""
        prompts_folder = self.manager.create_collection("Prompts Folder", self.test_user_id, PROMPTS)

        with self.assertRaises(ValueError):
            self.manager.add_prompt_members(prompts_folder.id, ["prompt-1"], self.test_user_id, LIBRARY)


if __name__ == '__main__':
    unittest.main()
