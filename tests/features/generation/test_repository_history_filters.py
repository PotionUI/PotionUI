"""Tests for the history discovery features on GenerationRepository:
ratings, favorites, server-side search, mode/preset/rating filters, sorting, facets.
"""

import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
from src.platform.util.ids import generate_ulid


class TestGenerationHistoryFilters(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    def _make(self, prompt="a prompt", preset_id="native/SDXL/realistic",
              mode="txt2img", status="completed"):
        gen = Generation(
            id=generate_ulid(),
            preset_id=preset_id,
            form_data={"prompt": prompt},
            user_id=self.user_id,
            status=status,
            mode=mode,
        )
        return self.repo.create(gen)

    # --- Ratings & favorites ---

    def test_rating_defaults_to_zero(self):
        gen = self._make()
        self.assertEqual(gen.rating, 0)
        self.assertFalse(gen.is_favorite)

    def test_update_rating_roundtrips(self):
        gen = self._make()
        ok = self.repo.update_rating(gen.id, 4, user_id=self.user_id)
        self.assertTrue(ok)
        self.assertEqual(self.repo.get_by_id(gen.id).rating, 4)

    def test_update_rating_wrong_user_no_change(self):
        gen = self._make()
        ok = self.repo.update_rating(gen.id, 5, user_id="someone_else")
        self.assertFalse(ok)
        self.assertEqual(self.repo.get_by_id(gen.id).rating, 0)

    def test_set_favorite_roundtrips(self):
        gen = self._make()
        self.assertTrue(self.repo.set_favorite(gen.id, True, user_id=self.user_id))
        self.assertTrue(self.repo.get_by_id(gen.id).is_favorite)
        self.repo.set_favorite(gen.id, False, user_id=self.user_id)
        self.assertFalse(self.repo.get_by_id(gen.id).is_favorite)

    # --- Filters ---

    def test_min_rating_filter(self):
        low = self._make(prompt="low")
        high = self._make(prompt="high")
        self.repo.update_rating(high.id, 5, user_id=self.user_id)
        results = self.repo.get_all(user_id=self.user_id, min_rating=4)
        ids = {g.id for g in results}
        self.assertIn(high.id, ids)
        self.assertNotIn(low.id, ids)

    def test_favorites_only_filter(self):
        fav = self._make(prompt="keeper")
        other = self._make(prompt="meh")
        self.repo.set_favorite(fav.id, True, user_id=self.user_id)
        results = self.repo.get_all(user_id=self.user_id, favorites_only=True)
        ids = {g.id for g in results}
        self.assertEqual(ids, {fav.id})

    def test_mode_filter(self):
        t2i = self._make(mode="txt2img")
        i2i = self._make(mode="img2img")
        results = self.repo.get_all(user_id=self.user_id, mode="img2img")
        ids = {g.id for g in results}
        self.assertIn(i2i.id, ids)
        self.assertNotIn(t2i.id, ids)

    def test_preset_filter(self):
        a = self._make(preset_id="native/SDXL/realistic")
        b = self._make(preset_id="native/QwenImage/standard")
        results = self.repo.get_all(user_id=self.user_id, preset_id="native/QwenImage/standard")
        self.assertEqual({g.id for g in results}, {b.id})

    def test_media_type_filter_mesh(self):
        mesh_gen = self._make(prompt="a mesh")
        image_gen = self._make(prompt="an image")
        self.repo.add_file(mesh_gen.id, File(
            file_path="/test/model.glb",
            file_type="MESH",
            user_id=self.user_id,
            is_final=True,
        ))
        self.repo.add_file(image_gen.id, File(
            file_path="/test/image.png",
            file_type="IMAGE",
            user_id=self.user_id,
            is_final=True,
        ))
        results = self.repo.get_all(user_id=self.user_id, media_type="mesh")
        self.assertEqual({g.id for g in results}, {mesh_gen.id})

    # --- Search ---

    def test_search_matches_prompt(self):
        cat = self._make(prompt="a fluffy cat on a sofa")
        dog = self._make(prompt="a happy dog running")
        results = self.repo.get_all(user_id=self.user_id, search="cat")
        self.assertEqual({g.id for g in results}, {cat.id})

    def test_search_and_terms(self):
        a = self._make(prompt="cyberpunk city neon")
        b = self._make(prompt="cyberpunk forest")
        results = self.repo.get_all(user_id=self.user_id, search="cyberpunk, city")
        self.assertEqual({g.id for g in results}, {a.id})

    def test_search_negation(self):
        blurry = self._make(prompt="a blurry cat")
        sharp = self._make(prompt="a sharp cat")
        results = self.repo.get_all(user_id=self.user_id, search="cat, !blurry")
        self.assertEqual({g.id for g in results}, {sharp.id})

    def test_search_matches_preset(self):
        q = self._make(prompt="landscape", preset_id="native/QwenImage/standard")
        s = self._make(prompt="landscape", preset_id="native/SDXL/realistic")
        results = self.repo.get_all(user_id=self.user_id, search="QwenImage")
        self.assertEqual({g.id for g in results}, {q.id})

    # --- Sorting ---

    def test_sort_by_rating_desc(self):
        one = self._make(prompt="one")
        five = self._make(prompt="five")
        self.repo.update_rating(one.id, 1, user_id=self.user_id)
        self.repo.update_rating(five.id, 5, user_id=self.user_id)
        results = self.repo.get_all(user_id=self.user_id, sort_by="rating", sort_dir="desc")
        self.assertEqual(results[0].id, five.id)

    def test_sort_injection_falls_back(self):
        # A malicious sort_by must not error; it falls back to created_at
        self._make()
        results = self.repo.get_all(user_id=self.user_id, sort_by="rating; DROP TABLE generations")
        self.assertIsInstance(results, list)

    # --- Count parity ---

    def test_count_matches_filtered_results(self):
        self._make(prompt="cat")
        self._make(prompt="cat")
        self._make(prompt="dog")
        results = self.repo.get_all(user_id=self.user_id, search="cat")
        count = self.repo.count_by_status(user_id=self.user_id, search="cat")
        self.assertEqual(len(results), count)

    # --- Facets ---

    def test_facets_returns_modes_and_presets(self):
        self._make(mode="txt2img", preset_id="native/SDXL/realistic")
        self._make(mode="img2img", preset_id="native/QwenImage/standard")
        facets = self.repo.get_facets(user_id=self.user_id)
        modes = {m['value'] for m in facets['modes']}
        presets = {p['id'] for p in facets['presets']}
        self.assertEqual(modes, {"txt2img", "img2img"})
        self.assertIn("native/QwenImage/standard", presets)
        # Names are resolved from preset YAML by GenerationHistoryQuery, not here.
        self.assertNotIn('name', facets['presets'][0])

    # --- Segment phrasebook provenance filter ---

    def _create_phrasebook_value(self):
        category_id = generate_ulid()
        value_id = generate_ulid()
        with self.repo_db().get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO phrasebook_categories (id, name, path, user_id)
                VALUES (?, ?, ?, ?)
            """, (category_id, "emotions", "emotions.joy", self.user_id))
            cursor.execute("""
                INSERT INTO phrasebook_values (id, category_id, label, value, user_id)
                VALUES (?, ?, ?, ?, ?)
            """, (value_id, category_id, "Joy", "joyful", self.user_id))
        return value_id

    def repo_db(self):
        """The test-patched db instance shared by the generation repository module."""
        import src.features.generation.repository as gr_module
        return gr_module.db

    def _add_segment(self, generation_id, phrasebook_value_id=None):
        from src.features.generation.segment_repository import GenerationSegmentRepository
        seg_repo = GenerationSegmentRepository()
        import src.features.generation.segment_repository as seg_module
        seg_module.db = self.repo_db()
        seg_repo.create_for_generation(generation_id, [{
            "channel": "positive",
            "segment_index": 0,
            "text": "a segment",
            "phrasebooks": ([{
                "phrasebook_value_id": phrasebook_value_id,
                "category_path": "emotions.joy",
                "value": "joyful",
            }] if phrasebook_value_id else []),
        }])

    def test_used_phrasebook_value_id_filter(self):
        value_id = self._create_phrasebook_value()
        used = self._make(prompt="used phrasebook")
        unused = self._make(prompt="unused phrasebook")
        self._add_segment(used.id, phrasebook_value_id=value_id)

        results = self.repo.get_all(user_id=self.user_id, used_phrasebook_value_id=value_id)
        ids = {g.id for g in results}
        self.assertEqual(ids, {used.id})
        self.assertNotIn(unused.id, ids)

        count = self.repo.count_by_status(user_id=self.user_id, used_phrasebook_value_id=value_id)
        self.assertEqual(count, 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
