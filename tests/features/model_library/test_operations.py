import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.model_library import operations


class TestBulkMoveCollections(PersistenceTestBase):

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = ModelCollectionRepository()
        self.test_user_id = self.create_test_user()

    def test_bulk_move_happy_path(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id)
        target = self.repo.create("Target", self.test_user_id)

        result = operations.bulk_move_collections(self.repo, [a.id, b.id], target.id, self.test_user_id)

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.repo.get_by_id(a.id).parent_id, target.id)
        self.assertEqual(self.repo.get_by_id(b.id).parent_id, target.id)

    def test_bulk_move_to_root(self):
        parent = self.repo.create("Parent", self.test_user_id)
        a = self.repo.create("A", self.test_user_id, parent_id=parent.id)
        b = self.repo.create("B", self.test_user_id, parent_id=parent.id)

        result = operations.bulk_move_collections(self.repo, [a.id, b.id], None, self.test_user_id)

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertIsNone(self.repo.get_by_id(a.id).parent_id)
        self.assertIsNone(self.repo.get_by_id(b.id).parent_id)

    def test_bulk_move_rejects_cycle_and_is_never_applied(self):
        a = self.repo.create("A", self.test_user_id)
        child = self.repo.create("Child", self.test_user_id, parent_id=a.id)
        other = self.repo.create("Other", self.test_user_id)

        # Moving A under its own child (a cycle) alongside an unrelated valid
        # move: the cycle-forming id must fail without it, and its parent
        # must stay put. The unrelated id is unaffected.
        result = operations.bulk_move_collections(self.repo, [a.id, other.id], child.id, self.test_user_id)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], a.id)
        self.assertIsNone(self.repo.get_by_id(a.id).parent_id)
        self.assertEqual(self.repo.get_by_id(other.id).parent_id, child.id)

    def test_bulk_move_rejects_target_inside_selection(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id)

        # Target is itself one of the ids being moved: B moving into itself
        # is a cycle and is rejected; A moving into B is an unrelated,
        # perfectly ordinary move and succeeds.
        result = operations.bulk_move_collections(self.repo, [a.id, b.id], b.id, self.test_user_id)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], b.id)
        self.assertEqual(self.repo.get_by_id(a.id).parent_id, b.id)
        self.assertIsNone(self.repo.get_by_id(b.id).parent_id)

    def test_bulk_move_ownership_violation(self):
        mine = self.repo.create("Mine", self.test_user_id)
        self.create_test_user(user_id="user_2", username="user2", email="user2@example.com")
        theirs = self.repo.create("Theirs", "user_2")
        target = self.repo.create("Target", self.test_user_id)

        result = operations.bulk_move_collections(self.repo, [mine.id, theirs.id], target.id, self.test_user_id)

        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], theirs.id)
        self.assertEqual(self.repo.get_by_id(mine.id).parent_id, target.id)
        self.assertIsNone(self.repo.get_by_id(theirs.id, user_id="user_2").parent_id)

    def test_bulk_move_partial_failure_reporting(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id)
        target = self.repo.create("Target", self.test_user_id)

        result = operations.bulk_move_collections(
            self.repo, [a.id, "missing-id", b.id], target.id, self.test_user_id
        )

        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["errors"][0]["id"], "missing-id")
        self.assertIn("reason", result["errors"][0])


if __name__ == '__main__':
    unittest.main()
