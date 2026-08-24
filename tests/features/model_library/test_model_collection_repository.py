import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.records import Model
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.models.repository import ModelRepository
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    # Mock for testing
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestModelCollectionRepository(PersistenceTestBase):

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = ModelCollectionRepository()
        self.model_repo = ModelRepository()
        self.test_user_id = self.create_test_user()

        # PersistenceTestBase only repatches the module-level `db` reference for
        # file_repository/generation_repository/collection_repository - these
        # repositories import `db` the same way, so without repatching here they
        # end up bound to whatever `db` instance was live the first time the
        # module was imported, instead of this test's fresh temp database.
        import src.features.model_library.repository.model_collection_repository as model_collection_repository_module
        model_collection_repository_module.db = self.db

        import src.features.models.repository as model_repository_module
        model_repository_module.db = self.db

        # ModelRepository.create()/get_by_id() also reaches into tag_repository
        # (get_model_tags), so it needs the same repatching.
        import src.features.tags.repository as tag_repository_module
        tag_repository_module.db = self.db

    def _make_model(self, filename: str = "test_model.safetensors") -> str:
        """Create a persisted model and return its id."""
        model = Model(
            id=generate_ulid(),
            filename=filename,
            file_path=f"/models/checkpoints/{filename}",
            file_size=1024,
            model_type="checkpoint",
        )
        created = self.model_repo.create(model)
        return created.id

    # --- Nesting (parent_id / move) ---

    def test_create_with_parent(self):
        parent = self.repo.create("Parent", self.test_user_id)
        child = self.repo.create("Child", self.test_user_id, parent_id=parent.id)
        self.assertEqual(child.parent_id, parent.id)
        by_id = {c.id: c for c in self.repo.list(self.test_user_id)}
        self.assertEqual(by_id[child.id].parent_id, parent.id)
        self.assertIsNone(by_id[parent.id].parent_id)

    def test_move_reparents(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id)
        c = self.repo.create("C", self.test_user_id, parent_id=a.id)
        self.assertTrue(self.repo.move(c.id, b.id, self.test_user_id))
        self.assertEqual(self.repo.get_by_id(c.id).parent_id, b.id)
        # move to root
        self.assertTrue(self.repo.move(c.id, None, self.test_user_id))
        self.assertIsNone(self.repo.get_by_id(c.id).parent_id)

    def test_move_rejects_self_parent(self):
        a = self.repo.create("A", self.test_user_id)
        with self.assertRaises(ValueError):
            self.repo.move(a.id, a.id, self.test_user_id)

    def test_move_rejects_descendant_cycle(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id, parent_id=a.id)
        c = self.repo.create("C", self.test_user_id, parent_id=b.id)
        # Moving A under C (its grandchild) would create a cycle
        with self.assertRaises(ValueError):
            self.repo.move(a.id, c.id, self.test_user_id)

    def test_delete_cascades_subtree(self):
        a = self.repo.create("A", self.test_user_id)
        b = self.repo.create("B", self.test_user_id, parent_id=a.id)
        c = self.repo.create("C", self.test_user_id, parent_id=b.id)
        self.assertTrue(self.repo.delete(a.id, self.test_user_id))
        remaining = {col.id for col in self.repo.list(self.test_user_id)}
        self.assertNotIn(a.id, remaining)
        self.assertNotIn(b.id, remaining)
        self.assertNotIn(c.id, remaining)

    def test_create_collection(self):
        """Test creating a collection"""
        collection = self.repo.create("My Loras", self.test_user_id)

        self.assertIsNotNone(collection)
        self.assertIsNotNone(collection.id)
        self.assertEqual(collection.name, "My Loras")
        self.assertEqual(collection.user_id, self.test_user_id)
        self.assertEqual(collection.item_count, 0)
        self.assertIsNotNone(collection.created_at)

    def test_get_by_id(self):
        """Test retrieving a collection by id (scoped and unscoped)"""
        created = self.repo.create("Checkpoints", self.test_user_id)

        fetched = self.repo.get_by_id(created.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.item_count, 0)

        # Scoped to the owner
        scoped = self.repo.get_by_id(created.id, user_id=self.test_user_id)
        self.assertIsNotNone(scoped)

        # Wrong owner returns None
        other = self.repo.get_by_id(created.id, user_id="other_user")
        self.assertIsNone(other)

    def test_list_with_counts(self):
        """Test listing collections includes a model count"""
        c1 = self.repo.create("Collection One", self.test_user_id)
        c2 = self.repo.create("Collection Two", self.test_user_id)

        model_a = self._make_model("a.safetensors")
        model_b = self._make_model("b.safetensors")
        self.repo.add_members(c1.id, [model_a, model_b], self.test_user_id)

        collections = self.repo.list(self.test_user_id)
        self.assertEqual(len(collections), 2)

        counts = {c.id: c.item_count for c in collections}
        self.assertEqual(counts[c1.id], 2)
        self.assertEqual(counts[c2.id], 0)

    def test_list_scoped_to_user(self):
        """Test list only returns the requesting user's collections"""
        self.repo.create("Mine", self.test_user_id)
        self.create_test_user(user_id="user_2", username="user2", email="user2@example.com")
        self.repo.create("Theirs", "user_2")

        mine = self.repo.list(self.test_user_id)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].name, "Mine")

    def test_rename(self):
        """Test renaming a collection"""
        created = self.repo.create("Old Name", self.test_user_id)

        ok = self.repo.rename(created.id, "New Name", self.test_user_id)
        self.assertTrue(ok)
        self.assertEqual(self.repo.get_by_id(created.id).name, "New Name")

        # Wrong owner cannot rename
        self.assertFalse(self.repo.rename(created.id, "Hacked", "other_user"))

    def test_delete(self):
        """Test deleting a collection cascades memberships"""
        created = self.repo.create("Doomed", self.test_user_id)
        model_id = self._make_model()
        self.repo.add_members(created.id, [model_id], self.test_user_id)

        ok = self.repo.delete(created.id, self.test_user_id)
        self.assertTrue(ok)
        self.assertIsNone(self.repo.get_by_id(created.id))
        # Membership rows are gone too (cascade)
        self.assertEqual(self.repo.get_for_model(model_id), [])

        # Deleting again returns False
        self.assertFalse(self.repo.delete(created.id, self.test_user_id))

    def test_add_members_dedup(self):
        """Test add_members ignores duplicates"""
        created = self.repo.create("Collection", self.test_user_id)
        model_id = self._make_model()

        added_first = self.repo.add_members(created.id, [model_id], self.test_user_id)
        self.assertEqual(added_first, 1)

        # Adding the same model again inserts nothing
        added_second = self.repo.add_members(created.id, [model_id], self.test_user_id)
        self.assertEqual(added_second, 0)

        self.assertEqual(self.repo.get_by_id(created.id).item_count, 1)

    def test_add_members_wrong_owner(self):
        """Test add_members refuses collections the user does not own"""
        created = self.repo.create("Collection", self.test_user_id)
        model_id = self._make_model()

        added = self.repo.add_members(created.id, [model_id], "other_user")
        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_by_id(created.id).item_count, 0)

    def test_remove_members(self):
        """Test remove_members deletes memberships"""
        created = self.repo.create("Collection", self.test_user_id)
        model_a = self._make_model("a.safetensors")
        model_b = self._make_model("b.safetensors")
        self.repo.add_members(created.id, [model_a, model_b], self.test_user_id)

        removed = self.repo.remove_members(created.id, [model_a])
        self.assertEqual(removed, 1)
        self.assertEqual(self.repo.get_by_id(created.id).item_count, 1)

    def test_get_for_model(self):
        """Test listing collections that contain a model"""
        c1 = self.repo.create("A", self.test_user_id)
        c2 = self.repo.create("B", self.test_user_id)
        model_id = self._make_model()

        self.repo.add_members(c1.id, [model_id], self.test_user_id)
        self.repo.add_members(c2.id, [model_id], self.test_user_id)

        collections = self.repo.get_for_model(model_id)
        self.assertEqual({c.id for c in collections}, {c1.id, c2.id})


if __name__ == '__main__':
    unittest.main()
