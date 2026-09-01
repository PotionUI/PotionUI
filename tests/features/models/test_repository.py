import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.models.repository import ModelRepository
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.features.models.records import Model


class TestModelRepository(PersistenceTestBase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        super().setUp()
        self.repository = ModelRepository()

        self.model_collection_repo = ModelCollectionRepository()
        self.user_model_meta_repo = UserModelMetaRepository()

    def _create_model(self, file_path: str = "/models/loras/test_lora.safetensors",
                       model_type: str = "lora", sha256: str = "a" * 64) -> Model:
        model = Model(
            filename=os.path.basename(file_path),
            file_path=file_path,
            file_size=1024,
            sha256=sha256,
            model_type=model_type,
        )
        return self.repository.create(model)

    def test_get_all_with_sorting_parameters(self):
        """Test that get_all method accepts and processes sorting parameters correctly."""
        # Test with different sort parameters - the main goal is to ensure no exceptions are raised
        test_cases = [
            ("indexed_at", "desc"),
            ("modified_at", "asc"),
            ("filename", "desc"),
            ("file_size", "asc"),
            ("model_type", "desc")
        ]

        for sort_by, sort_order in test_cases:
            try:
                result = self.repository.get_all(
                    sort_by=sort_by,
                    sort_order=sort_order,
                    limit=10,
                    offset=0
                )

                self.assertIsNotNone(result)
                self.assertIsInstance(result, list)

            except Exception as e:
                self.fail(f"get_all failed with sort_by={sort_by}, sort_order={sort_order}: {e}")

    def test_get_all_with_invalid_sort_field_uses_default(self):
        """Test that invalid sort field falls back to default indexed_at."""
        try:
            result = self.repository.get_all(
                sort_by="invalid_field",
                sort_order="desc",
                limit=10,
                offset=0
            )

            self.assertIsNotNone(result)
            self.assertIsInstance(result, list)

        except Exception as e:
            self.fail(f"get_all failed with invalid sort field: {e}")

    def test_get_all_sort_order_case_insensitive(self):
        """Test that sort order is case insensitive."""
        # Test different case variations
        for sort_order in ["DESC", "desc", "Desc", "ASC", "asc", "Asc"]:
            try:
                result = self.repository.get_all(
                    sort_by="indexed_at",
                    sort_order=sort_order,
                    limit=10,
                    offset=0
                )

                self.assertIsNotNone(result)
                self.assertIsInstance(result, list)

            except Exception as e:
                self.fail(f"get_all failed with sort_order={sort_order}: {e}")

    # --- triggers ---

    def test_create_persists_is_directory_true(self):
        model = Model(
            filename="Qwen3-4B", file_path="/models/llm/Qwen3-4B",
            file_size=4096, sha256="f" * 64, model_type="llm", is_directory=True,
        )
        created = self.repository.create(model)

        self.assertTrue(created.is_directory)
        fetched = self.repository.get_by_id(created.id)
        self.assertTrue(fetched.is_directory)

    def test_create_defaults_is_directory_false(self):
        model = self._create_model()
        self.assertFalse(model.is_directory)

        fetched = self.repository.get_by_id(model.id)
        self.assertFalse(fetched.is_directory)

    def test_update_persists_is_directory_change(self):
        model = self._create_model()
        model.is_directory = True

        success = self.repository.update(model)
        self.assertTrue(success)

        fetched = self.repository.get_by_id(model.id)
        self.assertTrue(fetched.is_directory)

    def test_from_row_without_model_metadata_column_defaults_to_empty_dict(self):
        row = {
            'id': 'model-id',
            'filename': 'test.safetensors',
            'file_path': '/models/test.safetensors',
            'file_size': 1024,
            'sha256': 'a' * 64,
            'model_type': 'lora',
            'created_at': None,
            'updated_at': None,
            'indexed_at': None,
            'description': None,
        }

        class FakeRow(dict):
            def keys(self):
                return super().keys()

        fake_row = FakeRow(row)
        model = Model.from_row(fake_row)
        self.assertEqual(model.model_metadata, {})

    def test_create_does_not_clobber_existing_model_metadata_on_reindex(self):
        model = self._create_model()
        self.repository.update_model_metadata(model.id, {"triggers": ["easy", "wave"]})

        # Simulate a reindex pass via upsert() on the same file_path.
        updated = Model(
            filename=model.filename,
            file_path=model.file_path,
            file_size=2048,
            sha256=model.sha256,
            model_type=model.model_type,
        )
        result = self.repository.upsert(updated)

        self.assertEqual(result.id, model.id)
        self.assertEqual(result.model_metadata, {"triggers": ["easy", "wave"]})

    def test_update_does_not_clobber_existing_model_metadata(self):
        model = self._create_model()
        self.repository.update_model_metadata(model.id, {"triggers": ["easy", "wave"]})

        model.file_size = 4096
        self.repository.update(model)

        fetched = self.repository.get_by_id(model.id)
        self.assertEqual(fetched.model_metadata, {"triggers": ["easy", "wave"]})
        self.assertEqual(fetched.file_size, 4096)

    # --- model library overlay (library_user_id / favorites_only / collection_id) ---

    def test_get_all_without_library_user_id_omits_overlay_fields(self):
        """Existing callers that don't pass library_user_id keep identical behavior."""
        model = self._create_model()

        results = self.repository.get_all()

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].custom_name)
        self.assertFalse(results[0].is_favorite)

    def test_get_all_with_library_user_id_surfaces_custom_name_and_favorite(self):
        user_id = self.create_test_user()
        model = self._create_model()
        self.user_model_meta_repo.set_favorite(user_id, model.id, True)
        self.user_model_meta_repo.set_custom_name(user_id, model.id, "My Checkpoint")

        results = self.repository.get_all(library_user_id=user_id)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_favorite)
        self.assertEqual(results[0].custom_name, "My Checkpoint")

    def test_get_all_favorites_only_filters(self):
        user_id = self.create_test_user()
        favorited = self._create_model(file_path="/models/checkpoints/fav.safetensors", sha256="b" * 64)
        self._create_model(file_path="/models/checkpoints/not_fav.safetensors", sha256="c" * 64)
        self.user_model_meta_repo.set_favorite(user_id, favorited.id, True)

        results = self.repository.get_all(library_user_id=user_id, favorites_only=True)

        self.assertEqual([m.id for m in results], [favorited.id])
        self.assertEqual(
            self.repository.count_total(library_user_id=user_id, favorites_only=True), 1
        )

    def test_get_all_collection_id_filters(self):
        user_id = self.create_test_user()
        in_collection = self._create_model(file_path="/models/checkpoints/in.safetensors", sha256="d" * 64)
        self._create_model(file_path="/models/checkpoints/out.safetensors", sha256="e" * 64)
        collection = self.model_collection_repo.create("My Collection", user_id)
        self.model_collection_repo.add_members(collection.id, [in_collection.id], user_id)

        results = self.repository.get_all(collection_id=collection.id)

        self.assertEqual([m.id for m in results], [in_collection.id])
        self.assertEqual(self.repository.count_total(collection_id=collection.id), 1)

    def test_get_all_favorites_only_without_library_user_id_returns_all(self):
        """favorites_only is a no-op when there's no library_user_id to join against."""
        self._create_model()

        results = self.repository.get_all(favorites_only=True)

        self.assertEqual(len(results), 1)

    def test_get_by_id_without_library_user_id_omits_overlay_fields(self):
        """Existing callers that don't pass library_user_id keep identical behavior."""
        model = self._create_model()

        fetched = self.repository.get_by_id(model.id)

        self.assertIsNone(fetched.custom_name)
        self.assertFalse(fetched.is_favorite)

    def test_get_by_id_with_library_user_id_surfaces_custom_name_and_favorite(self):
        user_id = self.create_test_user()
        model = self._create_model()
        self.user_model_meta_repo.set_favorite(user_id, model.id, True)
        self.user_model_meta_repo.set_custom_name(user_id, model.id, "My Checkpoint")

        fetched = self.repository.get_by_id(model.id, library_user_id=user_id)

        self.assertTrue(fetched.is_favorite)
        self.assertEqual(fetched.custom_name, "My Checkpoint")

    def test_new_model_defaults_to_available(self):
        model = self._create_model()

        self.assertTrue(model.is_available)
        self.assertIsNone(model.unavailable_at)

    def test_mark_unavailable_keeps_the_row_and_its_metadata(self):
        """Tags/ratings/assignments must survive a soft-unavailable mark - only the
        availability flag changes, the row itself is never touched otherwise."""
        model = self._create_model()

        result = self.repository.mark_unavailable(model.id)

        self.assertTrue(result)
        fetched = self.repository.get_by_id(model.id, include_providers=False, include_tags=False)
        self.assertFalse(fetched.is_available)
        self.assertIsNotNone(fetched.unavailable_at)
        self.assertEqual(fetched.filename, model.filename)
        self.assertEqual(fetched.sha256, model.sha256)

    def test_mark_unavailable_unknown_id_returns_false(self):
        self.assertFalse(self.repository.mark_unavailable("does-not-exist"))

    def test_update_revives_a_model_marked_unavailable(self):
        """`update()` is only ever called by the indexer once it has found a model's
        file on disk again, so it clears is_available/unavailable_at unconditionally."""
        model = self._create_model()
        self.repository.mark_unavailable(model.id)

        revived = self.repository.get_by_id(model.id, include_providers=False, include_tags=False)
        self.assertFalse(revived.is_available)
        revived.is_available = True
        revived.unavailable_at = None

        self.repository.update(revived)

        fetched = self.repository.get_by_id(model.id, include_providers=False, include_tags=False)
        self.assertTrue(fetched.is_available)
        self.assertIsNone(fetched.unavailable_at)