"""Tests for GenerationSegmentRepository: bulk create + ordered fetch with nested
phrasebooks, and cascade delete when the parent generation is removed.
"""

import sys
import os
import json

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.segment_repository import GenerationSegmentRepository
from src.platform.util.ids import generate_ulid


class TestGenerationSegmentRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationSegmentRepository()

        self.user_id = self.create_test_user()

    def _create_generation(self, gen_id: str = None) -> str:
        gen_id = gen_id or generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id, status)
                VALUES (?, ?, ?, ?, ?)
            """, (gen_id, "test_preset", json.dumps({"prompt": "test"}), self.user_id, "completed"))
        return gen_id

    def _create_phrasebook_value(self) -> str:
        category_id = generate_ulid()
        value_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO phrasebook_categories (id, name, path, user_id)
                VALUES (?, ?, ?, ?)
            """, (category_id, "emotions", "emotions.joy", self.user_id))
            cursor.execute("""
                INSERT INTO phrasebook_values (id, category_id, label, value, user_id)
                VALUES (?, ?, ?, ?, ?)
            """, (value_id, category_id, "Joy", "joyful", self.user_id))
        return value_id

    def test_create_and_get_by_generation_roundtrip(self):
        generation_id = self._create_generation()
        phrasebook_value_id = self._create_phrasebook_value()

        segments = [
            {
                "channel": "positive",
                "prompt_index": 0,
                "segment_index": 1,
                "segment_type": "content",
                "text": "second segment",
                "is_disabled": False,
                "name": "Composition",
                "color": "#123456",
                "description": "Foreground composition details",
                "phrasebooks": [
                    {
                        "phrasebook_value_id": phrasebook_value_id,
                        "category_path": "emotions.joy",
                        "value": "joyful",
                    }
                ],
            },
            {
                "channel": "positive",
                "prompt_index": 0,
                "segment_index": 0,
                "segment_type": "content",
                "text": "first segment",
            },
            {
                "channel": "negative",
                "prompt_index": 0,
                "segment_index": 0,
                "segment_type": "break",
                "text": "",
            },
        ]

        created = self.repo.create_for_generation(generation_id, segments)
        self.assertEqual(len(created), 3)

        fetched = self.repo.get_by_generation(generation_id)
        self.assertEqual(len(fetched), 3)

        # Ordered by channel, prompt_index, segment_index -> negative channel first
        self.assertEqual(fetched[0].channel, "negative")
        self.assertEqual(fetched[1].channel, "positive")
        self.assertEqual(fetched[1].text, "first segment")
        self.assertEqual(fetched[2].text, "second segment")

        second = fetched[2]
        self.assertEqual(second.name, "Composition")
        self.assertEqual(second.color, "#123456")
        self.assertEqual(second.description, "Foreground composition details")
        self.assertEqual(len(second.phrasebooks), 1)
        self.assertEqual(second.phrasebooks[0].phrasebook_value_id, phrasebook_value_id)
        self.assertEqual(second.phrasebooks[0].category_path, "emotions.joy")

        # to_dict includes nested phrasebooks
        as_dict = second.to_dict()
        self.assertIn("phrasebooks", as_dict)
        self.assertEqual(as_dict["name"], "Composition")
        self.assertEqual(as_dict["color"], "#123456")
        self.assertEqual(as_dict["description"], "Foreground composition details")
        self.assertEqual(as_dict["phrasebooks"][0]["value"], "joyful")

    def test_get_by_generation_empty(self):
        generation_id = self._create_generation()
        self.assertEqual(self.repo.get_by_generation(generation_id), [])

    def test_cascade_delete_when_generation_removed(self):
        generation_id = self._create_generation()
        self.repo.create_for_generation(generation_id, [
            {"channel": "positive", "segment_index": 0, "text": "hello",
             "phrasebooks": [{"phrasebook_value_id": None, "category_path": "x", "value": "y"}]},
        ])
        self.assertEqual(len(self.repo.get_by_generation(generation_id)), 1)

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (generation_id,))

        self.assertEqual(self.repo.get_by_generation(generation_id), [])

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM generation_segments WHERE generation_id = ?", (generation_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM generation_segment_phrasebook WHERE generation_id = ?", (generation_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
