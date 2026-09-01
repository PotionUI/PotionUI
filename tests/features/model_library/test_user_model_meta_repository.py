import unittest
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.records import Model
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.features.models.repository import ModelRepository
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestUserModelMetaRepository(PersistenceTestBase):

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = UserModelMetaRepository()
        self.model_repo = ModelRepository()
        self.test_user_id = self.create_test_user()

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

    def test_get_missing_returns_none(self):
        model_id = self._make_model()
        self.assertIsNone(self.repo.get(self.test_user_id, model_id))

    def test_set_favorite_toggle(self):
        model_id = self._make_model()

        meta = self.repo.set_favorite(self.test_user_id, model_id, True)
        self.assertTrue(meta.is_favorite)
        self.assertEqual(meta.user_id, self.test_user_id)
        self.assertEqual(meta.model_id, model_id)

        # Toggle off (upsert should update, not duplicate)
        meta = self.repo.set_favorite(self.test_user_id, model_id, False)
        self.assertFalse(meta.is_favorite)
        self.assertIsNone(self.repo.get(self.test_user_id, model_id).custom_name)

    def test_set_custom_name_and_clear(self):
        model_id = self._make_model()

        meta = self.repo.set_custom_name(self.test_user_id, model_id, "My Favorite Checkpoint")
        self.assertEqual(meta.custom_name, "My Favorite Checkpoint")

        # Clearing with None removes the name but keeps the row
        meta = self.repo.set_custom_name(self.test_user_id, model_id, None)
        self.assertIsNone(meta.custom_name)

    def test_set_favorite_and_custom_name_independent(self):
        """Setting one field via upsert must not clobber the other."""
        model_id = self._make_model()

        self.repo.set_custom_name(self.test_user_id, model_id, "Nice Name")
        meta = self.repo.set_favorite(self.test_user_id, model_id, True)

        self.assertTrue(meta.is_favorite)
        self.assertEqual(meta.custom_name, "Nice Name")

    def test_get_map_batch(self):
        model_a = self._make_model("a.safetensors")
        model_b = self._make_model("b.safetensors")
        model_c = self._make_model("c.safetensors")

        self.repo.set_favorite(self.test_user_id, model_a, True)
        self.repo.set_custom_name(self.test_user_id, model_b, "Custom B")

        result = self.repo.get_map(self.test_user_id, [model_a, model_b, model_c])

        self.assertEqual(set(result.keys()), {model_a, model_b})
        self.assertTrue(result[model_a].is_favorite)
        self.assertEqual(result[model_b].custom_name, "Custom B")

    def test_get_map_empty_list(self):
        self.assertEqual(self.repo.get_map(self.test_user_id, []), {})

    def test_favorite_model_ids(self):
        model_a = self._make_model("a.safetensors")
        model_b = self._make_model("b.safetensors")
        self._make_model("c.safetensors")  # not favorited

        self.repo.set_favorite(self.test_user_id, model_a, True)
        self.repo.set_favorite(self.test_user_id, model_b, True)

        favorites = self.repo.favorite_model_ids(self.test_user_id)
        self.assertEqual(favorites, {model_a, model_b})

    def test_meta_scoped_per_user(self):
        """Favoriting a model for one user must not affect another user's view."""
        model_id = self._make_model()
        self.create_test_user(user_id="user_2", username="user2", email="user2@example.com")

        self.repo.set_favorite(self.test_user_id, model_id, True)

        self.assertIsNone(self.repo.get("user_2", model_id))
        self.assertEqual(self.repo.favorite_model_ids("user_2"), set())


if __name__ == '__main__':
    unittest.main()
