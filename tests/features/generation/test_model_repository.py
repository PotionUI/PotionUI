import unittest
import json
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.model_repository import GenerationModelRepository
from src.features.generation.records import GenerationModel

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    import random
    import string
    def generate_ulid():
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestGenerationModelRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationModelRepository()

        # Patch db in the generation_model_repository module, and in the
        # models repository it enriches file rows through (also imports
        # `db` at load time, so left unpatched it queries the real database).
        import src.features.generation.model_repository
        src.features.generation.model_repository.db = self.db
        import src.features.models.repository
        src.features.models.repository.db = self.db

        self.test_user_id = self.create_test_user()
        self.other_user_id = self.create_test_user(
            user_id="other_user", username="otheruser", email="other@example.com"
        )

        # Create a test model
        self.test_model_id = generate_ulid()
        self._create_test_model(self.test_model_id)

    def _create_test_model(self, model_id: str, filename: str = "test_model.safetensors"):
        """Create a test model in the database."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO models (id, filename, file_path, file_size, model_type, sha256)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (model_id, filename, f"/models/{filename}", 1024, "checkpoint", "abc123"))

    def _create_completed_generation(self, gen_id: str, user_id: str) -> str:
        """Create a completed generation and return its ID."""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id, status)
                VALUES (?, ?, ?, ?, ?)
            """, (gen_id, "test_preset", json.dumps({"prompt": "test"}), user_id, "completed"))
        return gen_id

    def _create_generation_model_link(self, generation_id: str, model_id: str):
        """Link a generation to a model."""
        link_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generation_models (id, generation_id, model_id)
                VALUES (?, ?, ?)
            """, (link_id, generation_id, model_id))

    def _create_generation_with_file(self, gen_id: str, user_id: str, model_id: str):
        """Create a completed generation linked to a model, with a file."""
        self._create_completed_generation(gen_id, user_id)
        self._create_generation_model_link(gen_id, model_id)

        # Create a file and link it
        file_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO files (id, file_path, file_type, user_id, is_final)
                VALUES (?, ?, ?, ?, ?)
            """, (file_id, f"/outputs/{gen_id}/image.png", "IMAGE", user_id, True))

            gf_id = generate_ulid()
            cursor.execute("""
                INSERT INTO generation_files (id, generation_id, file_id)
                VALUES (?, ?, ?)
            """, (gf_id, gen_id, file_id))

    def test_get_generations_by_model_empty(self):
        """Returns empty list when no generations are associated with the model."""
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id
        )
        self.assertEqual(generations, [])
        self.assertEqual(total, 0)

    def test_get_generations_by_model_returns_matching(self):
        """Returns generations that used the specified model."""
        gen_id = generate_ulid()
        self._create_generation_with_file(gen_id, self.test_user_id, self.test_model_id)

        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id
        )
        self.assertEqual(total, 1)
        self.assertEqual(len(generations), 1)
        self.assertEqual(generations[0].id, gen_id)

    def test_get_generations_by_model_filters_by_user(self):
        """Only returns generations owned by the specified user."""
        gen1_id = generate_ulid()
        gen2_id = generate_ulid()
        self._create_generation_with_file(gen1_id, self.test_user_id, self.test_model_id)
        self._create_generation_with_file(gen2_id, self.other_user_id, self.test_model_id)

        # Query for test_user should only return gen1
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id
        )
        self.assertEqual(total, 1)
        self.assertEqual(generations[0].id, gen1_id)

        # Query for other_user should only return gen2
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.other_user_id
        )
        self.assertEqual(total, 1)
        self.assertEqual(generations[0].id, gen2_id)

    def test_get_generations_by_model_excludes_non_completed(self):
        """Only returns completed generations."""
        gen_completed = generate_ulid()
        gen_pending = generate_ulid()

        self._create_generation_with_file(gen_completed, self.test_user_id, self.test_model_id)

        # Create a pending generation linked to same model
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id, status)
                VALUES (?, ?, ?, ?, ?)
            """, (gen_pending, "test_preset", json.dumps({"prompt": "test"}), self.test_user_id, "pending"))
        self._create_generation_model_link(gen_pending, self.test_model_id)

        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id
        )
        self.assertEqual(total, 1)
        self.assertEqual(generations[0].id, gen_completed)

    def test_get_generations_by_model_pagination(self):
        """Pagination with limit and offset works correctly."""
        gen_ids = []
        for i in range(5):
            gid = generate_ulid()
            gen_ids.append(gid)
            self._create_generation_with_file(gid, self.test_user_id, self.test_model_id)

        # Get first page
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id, limit=2, offset=0
        )
        self.assertEqual(total, 5)
        self.assertEqual(len(generations), 2)

        # Get second page
        generations2, total2 = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id, limit=2, offset=2
        )
        self.assertEqual(total2, 5)
        self.assertEqual(len(generations2), 2)

        # Pages should not overlap
        page1_ids = {g.id for g in generations}
        page2_ids = {g.id for g in generations2}
        self.assertTrue(page1_ids.isdisjoint(page2_ids))

    def test_get_generations_by_model_loads_files(self):
        """Files are loaded for each returned generation."""
        gen_id = generate_ulid()
        self._create_generation_with_file(gen_id, self.test_user_id, self.test_model_id)

        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id
        )
        self.assertEqual(len(generations), 1)
        self.assertGreater(len(generations[0].files), 0)
        self.assertEqual(generations[0].files[0].file_type, "IMAGE")

    def test_get_generations_by_model_total_count_accuracy(self):
        """Total count is accurate regardless of limit/offset."""
        for i in range(7):
            gid = generate_ulid()
            self._create_generation_with_file(gid, self.test_user_id, self.test_model_id)

        # Small page should still report total=7
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id, limit=3, offset=0
        )
        self.assertEqual(total, 7)
        self.assertEqual(len(generations), 3)

        # Large offset returns fewer results but same total
        generations, total = self.repo.get_generations_by_model(
            self.test_model_id, self.test_user_id, limit=3, offset=6
        )
        self.assertEqual(total, 7)
        self.assertEqual(len(generations), 1)

    def test_get_by_generation_with_include_flags_does_not_raise(self):
        """get_by_generation(include_model_info/include_files) is the history
        modal's params/models path — a self-import bug here ImportError'd and
        500'd every get_params call (maintainer repro 2026-07-16)."""
        gen_id = generate_ulid()
        self._create_completed_generation(gen_id, self.test_user_id)
        self._create_generation_model_link(gen_id, self.test_model_id)

        models = self.repo.get_by_generation(
            gen_id, include_model_info=True, include_files=True
        )
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].id, self.test_model_id)
        # files loaded (possibly empty list), not left unset
        self.assertIsNotNone(models[0].files)


if __name__ == '__main__':
    unittest.main()
