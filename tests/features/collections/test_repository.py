import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation
from src.features.collections.repository import CollectionRepository
from src.features.generation.repository import GenerationRepository
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    # Mock for testing
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))

HISTORY = "history"
LIBRARY = "library"


class TestCollectionRepository(PersistenceTestBase):

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = CollectionRepository()
        self.gen_repo = GenerationRepository()
        self.test_user_id = self.create_test_user()

    def _make_generation(self) -> str:
        """Create a persisted generation and return its id."""
        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data={"prompt": "test prompt"},
            user_id=self.test_user_id,
            status="completed",
            preset_version="1.0",
        )
        self.gen_repo.create(generation)
        return generation.id

    # --- Nesting (parent_id / move) ---

    def test_create_with_parent(self):
        parent = self.repo.create("Parent", self.test_user_id, HISTORY)
        child = self.repo.create("Child", self.test_user_id, HISTORY, parent_id=parent.id)
        self.assertEqual(child.parent_id, parent.id)
        # round-trips via list
        by_id = {c.id: c for c in self.repo.list(self.test_user_id, HISTORY)}
        self.assertEqual(by_id[child.id].parent_id, parent.id)
        self.assertIsNone(by_id[parent.id].parent_id)

    def test_move_reparents(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY)
        c = self.repo.create("C", self.test_user_id, HISTORY, parent_id=a.id)
        self.assertTrue(self.repo.move(c.id, b.id, self.test_user_id, HISTORY))
        self.assertEqual(self.repo.get_by_id(c.id, HISTORY).parent_id, b.id)
        # move to root
        self.assertTrue(self.repo.move(c.id, None, self.test_user_id, HISTORY))
        self.assertIsNone(self.repo.get_by_id(c.id, HISTORY).parent_id)

    def test_move_rejects_self_parent(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        with self.assertRaises(ValueError):
            self.repo.move(a.id, a.id, self.test_user_id, HISTORY)

    def test_move_rejects_descendant_cycle(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY, parent_id=a.id)
        c = self.repo.create("C", self.test_user_id, HISTORY, parent_id=b.id)
        # Moving A under C (its grandchild) would create a cycle
        with self.assertRaises(ValueError):
            self.repo.move(a.id, c.id, self.test_user_id, HISTORY)

    def test_delete_cascades_subtree(self):
        a = self.repo.create("A", self.test_user_id, HISTORY)
        b = self.repo.create("B", self.test_user_id, HISTORY, parent_id=a.id)
        c = self.repo.create("C", self.test_user_id, HISTORY, parent_id=b.id)
        self.assertTrue(self.repo.delete(a.id, self.test_user_id, HISTORY))
        remaining = {col.id for col in self.repo.list(self.test_user_id, HISTORY)}
        self.assertNotIn(a.id, remaining)
        self.assertNotIn(b.id, remaining)
        self.assertNotIn(c.id, remaining)

    def test_create_collection(self):
        """Test creating a collection"""
        collection = self.repo.create("My Album", self.test_user_id, HISTORY)

        self.assertIsNotNone(collection)
        self.assertIsNotNone(collection.id)
        self.assertEqual(collection.name, "My Album")
        self.assertEqual(collection.user_id, self.test_user_id)
        self.assertEqual(collection.scope, HISTORY)
        self.assertEqual(collection.item_count, 0)
        self.assertIsNotNone(collection.created_at)

    def test_get_by_id(self):
        """Test retrieving a collection by id (scoped and unscoped)"""
        created = self.repo.create("Album", self.test_user_id, HISTORY)

        fetched = self.repo.get_by_id(created.id, HISTORY)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.item_count, 0)

        # Scoped to the owner
        scoped = self.repo.get_by_id(created.id, HISTORY, user_id=self.test_user_id)
        self.assertIsNotNone(scoped)

        # Wrong owner returns None
        other = self.repo.get_by_id(created.id, HISTORY, user_id="other_user")
        self.assertIsNone(other)

    def test_get_by_id_rejects_wrong_scope(self):
        """A collection created in one scope is invisible to a lookup in the other."""
        created = self.repo.create("Album", self.test_user_id, HISTORY)

        self.assertIsNone(self.repo.get_by_id(created.id, LIBRARY))
        self.assertIsNotNone(self.repo.get_by_id(created.id, HISTORY))

    def test_list_with_counts(self):
        """Test listing collections includes a generation count"""
        c1 = self.repo.create("Album One", self.test_user_id, HISTORY)
        c2 = self.repo.create("Album Two", self.test_user_id, HISTORY)

        gen_a = self._make_generation()
        gen_b = self._make_generation()
        self.repo.add_members(c1.id, [gen_a, gen_b], self.test_user_id, HISTORY)

        collections = self.repo.list(self.test_user_id, HISTORY)
        self.assertEqual(len(collections), 2)

        counts = {c.id: c.item_count for c in collections}
        self.assertEqual(counts[c1.id], 2)
        self.assertEqual(counts[c2.id], 0)

    def test_list_scoped_to_user(self):
        """Test list only returns the requesting user's collections"""
        self.repo.create("Mine", self.test_user_id, HISTORY)
        self.create_test_user(user_id="user_2", username="user2", email="user2@example.com")
        self.repo.create("Theirs", "user_2", HISTORY)

        mine = self.repo.list(self.test_user_id, HISTORY)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].name, "Mine")

    def test_list_scoped_to_scope(self):
        """History and Library collections never appear in each other's list."""
        self.repo.create("History Folder", self.test_user_id, HISTORY)
        self.repo.create("Library Folder", self.test_user_id, LIBRARY)

        history_names = {c.name for c in self.repo.list(self.test_user_id, HISTORY)}
        library_names = {c.name for c in self.repo.list(self.test_user_id, LIBRARY)}

        self.assertEqual(history_names, {"History Folder"})
        self.assertEqual(library_names, {"Library Folder"})

    def test_rename(self):
        """Test renaming a collection"""
        created = self.repo.create("Old Name", self.test_user_id, HISTORY)

        ok = self.repo.rename(created.id, "New Name", self.test_user_id, HISTORY)
        self.assertTrue(ok)
        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).name, "New Name")

        # Wrong owner cannot rename
        self.assertFalse(self.repo.rename(created.id, "Hacked", "other_user", HISTORY))

    def test_rename_rejects_wrong_scope(self):
        created = self.repo.create("Old Name", self.test_user_id, HISTORY)
        self.assertFalse(self.repo.rename(created.id, "Hacked", self.test_user_id, LIBRARY))
        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).name, "Old Name")

    def test_delete(self):
        """Test deleting a collection cascades memberships"""
        created = self.repo.create("Doomed", self.test_user_id, HISTORY)
        gen = self._make_generation()
        self.repo.add_members(created.id, [gen], self.test_user_id, HISTORY)

        ok = self.repo.delete(created.id, self.test_user_id, HISTORY)
        self.assertTrue(ok)
        self.assertIsNone(self.repo.get_by_id(created.id, HISTORY))
        # Membership rows are gone too (cascade)
        self.assertEqual(self.repo.get_for_generation(gen), [])

        # Deleting again returns False
        self.assertFalse(self.repo.delete(created.id, self.test_user_id, HISTORY))

    def test_add_members_dedup(self):
        """Test add_members ignores duplicates"""
        created = self.repo.create("Album", self.test_user_id, HISTORY)
        gen = self._make_generation()

        added_first = self.repo.add_members(created.id, [gen], self.test_user_id, HISTORY)
        self.assertEqual(added_first, 1)

        # Adding the same generation again inserts nothing
        added_second = self.repo.add_members(created.id, [gen], self.test_user_id, HISTORY)
        self.assertEqual(added_second, 0)

        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).item_count, 1)

    def test_add_members_wrong_owner(self):
        """Test add_members refuses collections the user does not own"""
        created = self.repo.create("Album", self.test_user_id, HISTORY)
        gen = self._make_generation()

        added = self.repo.add_members(created.id, [gen], "other_user", HISTORY)
        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).item_count, 0)

    def test_add_members_rejects_wrong_scope(self):
        created = self.repo.create("Album", self.test_user_id, HISTORY)
        gen = self._make_generation()

        added = self.repo.add_members(created.id, [gen], self.test_user_id, LIBRARY)
        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).item_count, 0)

    def test_remove_members(self):
        """Test remove_members deletes memberships"""
        created = self.repo.create("Album", self.test_user_id, HISTORY)
        gen_a = self._make_generation()
        gen_b = self._make_generation()
        self.repo.add_members(created.id, [gen_a, gen_b], self.test_user_id, HISTORY)

        removed = self.repo.remove_members(created.id, [gen_a])
        self.assertEqual(removed, 1)
        self.assertEqual(self.repo.get_by_id(created.id, HISTORY).item_count, 1)

    def test_get_for_generation(self):
        """Test listing collections that contain a generation"""
        c1 = self.repo.create("A", self.test_user_id, HISTORY)
        c2 = self.repo.create("B", self.test_user_id, HISTORY)
        gen = self._make_generation()

        self.repo.add_members(c1.id, [gen], self.test_user_id, HISTORY)
        self.repo.add_members(c2.id, [gen], self.test_user_id, HISTORY)

        collections = self.repo.get_for_generation(gen)
        self.assertEqual({c.id for c in collections}, {c1.id, c2.id})

    def test_generation_get_all_filters_by_collection(self):
        """Test GenerationRepository.get_all filters by collection_id"""
        collection = self.repo.create("Filtered", self.test_user_id, HISTORY)
        gen_in = self._make_generation()
        gen_out = self._make_generation()

        self.repo.add_members(collection.id, [gen_in], self.test_user_id, HISTORY)

        results = self.gen_repo.get_all(user_id=self.test_user_id, collection_id=collection.id)
        result_ids = {g.id for g in results}

        self.assertIn(gen_in, result_ids)
        self.assertNotIn(gen_out, result_ids)
        self.assertEqual(len(results), 1)


if __name__ == '__main__':
    unittest.main()
