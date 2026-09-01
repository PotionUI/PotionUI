"""Tests for GenerationSourceRepository (provenance links): bulk
create + ordered fetch, the "primary" (first-by-field_name) accessor
`get_params` inheritance relies on, and cascade delete on either side of the
link when a generation is removed.
"""

import sys
import os
import json

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.source_repository import GenerationSourceRepository
from src.platform.util.ids import generate_ulid


class TestGenerationSourceRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationSourceRepository()

        self.user_id = self.create_test_user()

    def _create_generation(self, gen_id: str = None) -> str:
        gen_id = gen_id or generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id, status)
                VALUES (?, ?, ?, ?, ?)
            """, (gen_id, "test_preset", json.dumps({"prompt": "test"}), self.user_id, "completed"))
        return gen_id

    def test_create_for_generation_roundtrip(self):
        child = self._create_generation()
        source = self._create_generation()

        created = self.repo.create_for_generation(child, [
            {"field_name": "source_image", "source_generation_id": source, "source_file_index": 2},
        ])

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].generation_id, child)
        self.assertEqual(created[0].field_name, "source_image")
        self.assertEqual(created[0].source_generation_id, source)
        self.assertEqual(created[0].source_file_index, 2)
        self.assertIsNotNone(created[0].id)

        fetched = self.repo.get_by_generation(child)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0].source_generation_id, source)

    def test_get_by_generation_orders_by_field_name(self):
        child = self._create_generation()
        source_a = self._create_generation()
        source_b = self._create_generation()

        self.repo.create_for_generation(child, [
            {"field_name": "reference_image", "source_generation_id": source_b, "source_file_index": 0},
            {"field_name": "source_image", "source_generation_id": source_a, "source_file_index": 0},
        ])

        fetched = self.repo.get_by_generation(child)
        self.assertEqual([s.field_name for s in fetched], ["reference_image", "source_image"])

    def test_get_by_generation_empty_when_no_links(self):
        child = self._create_generation()
        self.assertEqual(self.repo.get_by_generation(child), [])

    def test_get_primary_for_generation_returns_first_by_field_name(self):
        child = self._create_generation()
        source_a = self._create_generation()
        source_b = self._create_generation()

        self.repo.create_for_generation(child, [
            {"field_name": "z_field", "source_generation_id": source_b, "source_file_index": 5},
            {"field_name": "a_field", "source_generation_id": source_a, "source_file_index": 1},
        ])

        primary = self.repo.get_primary_for_generation(child)
        self.assertIsNotNone(primary)
        self.assertEqual(primary.field_name, "a_field")
        self.assertEqual(primary.source_generation_id, source_a)

    def test_get_primary_for_generation_none_when_no_links(self):
        child = self._create_generation()
        self.assertIsNone(self.repo.get_primary_for_generation(child))

    def test_to_dict_shape(self):
        child = self._create_generation()
        source = self._create_generation()
        created = self.repo.create_for_generation(child, [
            {"field_name": "source_image", "source_generation_id": source, "source_file_index": 0},
        ])[0]

        as_dict = created.to_dict()
        self.assertEqual(as_dict["generation_id"], child)
        self.assertEqual(as_dict["field_name"], "source_image")
        self.assertEqual(as_dict["source_generation_id"], source)
        self.assertEqual(as_dict["source_file_index"], 0)
        self.assertIn("id", as_dict)
        self.assertIn("created_at", as_dict)

    def test_deleting_child_generation_cascades(self):
        child = self._create_generation()
        source = self._create_generation()
        self.repo.create_for_generation(child, [
            {"field_name": "source_image", "source_generation_id": source, "source_file_index": 0},
        ])

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (child,))

        self.assertEqual(self.repo.get_by_generation(child), [])

    def test_deleting_source_generation_cascades(self):
        child = self._create_generation()
        source = self._create_generation()
        self.repo.create_for_generation(child, [
            {"field_name": "source_image", "source_generation_id": source, "source_file_index": 0},
        ])

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (source,))

        # The link itself is gone (not left dangling) once its source is deleted -
        # cascades the same way the generation_models junction table does.
        self.assertEqual(self.repo.get_by_generation(child), [])
