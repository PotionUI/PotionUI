"""Tests for the history discovery features on GenerationRepository:
ratings, favorites, server-side search, mode/preset/rating filters, sorting, facets.
"""

import json
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.collections.repository import CollectionRepository
from src.features.generation.history_filter import HistoryFilter
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
from src.platform.util.ids import generate_ulid


class TestGenerationHistoryFilters(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    def _make(self, prompt="a prompt", preset_id="native/SDXL/realistic",
              mode="txt2img", status="completed", form_data=None, prompt_state=None):
        gen = Generation(
            id=generate_ulid(),
            preset_id=preset_id,
            form_data=form_data if form_data is not None else {"prompt": prompt},
            user_id=self.user_id,
            status=status,
            mode=mode,
            prompt_state=prompt_state,
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

    def test_search_matches_prompt_only_in_prompt_state(self):
        segmented = self._make(form_data={"steps": 20}, prompt_state={"prompt": "a lighthouse at dusk", "promptSegments": []})
        self._make(prompt="a happy dog")
        results = self.repo.get_all(user_id=self.user_id, search="lighthouse")
        self.assertEqual({g.id for g in results}, {segmented.id})
        self.assertEqual(self.repo.count_by_status(user_id=self.user_id, search="lighthouse"), 1)

    def test_search_ignores_negative_prompt_in_both_columns(self):
        self._make(prompt="a castle", form_data={"prompt": "a castle", "negative_prompt": "blurry"})
        self._make(form_data={"steps": 1}, prompt_state={"prompt": "a tower", "negativePrompt": "blurry"})
        self.assertEqual(self.repo.get_all(user_id=self.user_id, search="blurry"), [])

    def test_search_negation_covers_prompt_state(self):
        keep = self._make(form_data={"steps": 1}, prompt_state={"prompt": "a calm sea"})
        self._make(form_data={"steps": 1}, prompt_state={"prompt": "a stormy sea"})
        results = self.repo.get_all(user_id=self.user_id, search="sea, !stormy")
        self.assertEqual({g.id for g in results}, {keep.id})

    def test_search_matches_preset(self):
        q = self._make(prompt="landscape", preset_id="native/QwenImage/standard")
        s = self._make(prompt="landscape", preset_id="native/SDXL/realistic")
        results = self.repo.get_all(user_id=self.user_id, search="QwenImage")
        self.assertEqual({g.id for g in results}, {q.id})

    def _search_ids(self, search):
        return {g.id for g in self.repo.get_all(user_id=self.user_id, search=search)}

    def test_search_matches_two_character_terms(self):
        cat = self._make(prompt="a fluffy cat")
        self._make(prompt="a happy dog")
        self.assertEqual(self._search_ids("ca"), {cat.id})
        self.assertEqual(self.repo.count_by_status(user_id=self.user_id, search="ca"), 1)

    def test_search_non_ascii_terms_fold_ascii_case_only(self):
        cafe = self._make(prompt="Caf\u00e9 terrace")
        self.assertEqual(self._search_ids("caf\u00e9"), {cafe.id})
        self.assertEqual(self._search_ids("CAF\u00e9"), {cafe.id})
        self.assertEqual(self._search_ids("CAF\u00c9"), set())

    def test_search_term_with_an_embedded_quote(self):
        sized = self._make(prompt='a 5"x7" print')
        self._make(prompt="a 5x7 print")
        self.assertEqual(self._search_ids('5"x7"'), {sized.id})

    def test_search_does_not_match_across_two_prompt_strings(self):
        both = self._make(form_data={"prompt": "red", "positive_prompt": "fox"})
        self.assertEqual(self._search_ids("redfox"), set())
        self.assertEqual(self._search_ids("red, fox"), {both.id})

    def test_search_follows_prompt_rewrites_and_deletes(self):
        gen = self._make(prompt="a red fox")
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generations SET form_data = ? WHERE id = ?",
                           (json.dumps({"prompt": "a blue whale"}), gen.id))
        self.assertEqual(self._search_ids("fox"), set())
        self.assertEqual(self._search_ids("whale"), {gen.id})

        self.repo.delete(gen.id)
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM generation_prompt_text WHERE generation_id = ?", (gen.id,))
            self.assertEqual(cursor.fetchone()[0], 0)

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

    def _link_model(self, generation_id, filename, custom_name=None, provider_name=None, user_id=None):
        model_id = generate_ulid()
        with self.repo_db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO models (id, filename, model_type) VALUES (?, ?, 'checkpoint')",
                (model_id, filename),
            )
            cursor.execute(
                "INSERT INTO generation_models (id, generation_id, model_id) VALUES (?, ?, ?)",
                (generate_ulid(), generation_id, model_id),
            )
            if custom_name:
                cursor.execute(
                    "INSERT INTO user_model_meta (user_id, model_id, custom_name) VALUES (?, ?, ?)",
                    (user_id or self.user_id, model_id, custom_name),
                )
            if provider_name:
                cursor.execute(
                    "INSERT INTO providers (id, model_id, provider, name) VALUES (?, ?, 'civitai', ?)",
                    (generate_ulid(), model_id, provider_name),
                )
        return model_id

    def test_facet_models_carry_a_readable_label_beside_the_filename(self):
        self._link_model(self._make().id, "plain_fp8_scaled.safetensors")
        self._link_model(self._make().id, "sub/dir/nested.safetensors")
        self._link_model(self._make().id, "from_provider.safetensors", provider_name="Provider Name")
        self._link_model(self._make().id, "renamed.safetensors", custom_name="My Name", provider_name="Provider Name")
        models = {m['name']: m for m in self.repo.get_facets(user_id=self.user_id)['models']}
        self.assertEqual(models["plain_fp8_scaled.safetensors"]['label'], "plain_fp8_scaled")
        self.assertEqual(models["sub/dir/nested.safetensors"]['label'], "nested")
        self.assertEqual(models["from_provider.safetensors"]['label'], "Provider Name")
        self.assertEqual(models["renamed.safetensors"]['label'], "My Name")
        self.assertEqual(models["renamed.safetensors"]['count'], 1)

    def test_facet_model_custom_name_is_per_user(self):
        other = self.create_test_user("other_user", "otheruser", "other@example.com")
        mine = self._make()
        self._link_model(mine.id, "shared.safetensors", custom_name="Other Alias", user_id=other)
        models = self.repo.get_facets(user_id=self.user_id)['models']
        self.assertEqual(models[0]['label'], "shared")

    def test_model_filter_matches_every_newline_separated_name(self):
        a = self._make()
        b = self._make()
        c = self._make()
        self._link_model(a.id, "one.safetensors")
        self._link_model(b.id, "two.safetensors")
        self._link_model(c.id, "three.safetensors")
        ids = lambda name: {g.id for g in self.repo.get_all(user_id=self.user_id, model_name=name)}
        self.assertEqual(ids("one.safetensors"), {a.id})
        self.assertEqual(ids("one.safetensors\ntwo.safetensors"), {a.id, b.id})

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
        """The test database every repository resolves at call time."""
        from src.platform.database.database import db
        return db

    def _add_segment(self, generation_id, phrasebook_value_id=None):
        from src.features.generation.segment_repository import GenerationSegmentRepository
        seg_repo = GenerationSegmentRepository()
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


class TestHistoryFilterObject(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.collections = CollectionRepository()
        self.user_id = self.create_test_user()

    def _make(self):
        return self.repo.create(Generation(
            id=generate_ulid(),
            preset_id="native/SDXL/realistic",
            form_data={"prompt": "x"},
            user_id=self.user_id,
            status="completed",
        )).id

    def _file(self, generation_id, collection_id):
        self.collections.add_members(collection_id, [generation_id], self.user_id, "history")

    def _both(self, flt):
        return {g.id for g in self.repo.get_all(flt)}, self.repo.count_by_status(flt)

    def test_unsorted_excludes_generations_filed_in_a_collection(self):
        filed, loose = self._make(), self._make()
        folder = self.collections.create("Folder", self.user_id, "history")
        self._file(filed, folder.id)

        ids, count = self._both(HistoryFilter(user_id=self.user_id, unsorted=True))

        self.assertEqual(ids, {loose})
        self.assertEqual(count, 1)

    def test_model_name_matches_only_generations_that_used_it(self):
        used, other = self._make(), self._make()
        with self.db.get_cursor() as cursor:
            for model_id, filename in (("m-1", "dreamshaper.safetensors"), ("m-2", "other.safetensors")):
                cursor.execute(
                    "INSERT INTO models (id, filename, model_type) VALUES (?, ?, 'checkpoint')",
                    (model_id, filename),
                )
            cursor.execute("INSERT INTO generation_models (id, generation_id, model_id) VALUES (?, ?, ?)",
                           (generate_ulid(), used, "m-1"))
            cursor.execute("INSERT INTO generation_models (id, generation_id, model_id) VALUES (?, ?, ?)",
                           (generate_ulid(), other, "m-2"))

        ids, count = self._both(HistoryFilter(user_id=self.user_id, model_name="dreamshaper"))

        self.assertEqual(ids, {used})
        self.assertEqual(count, 1)

    def test_completed_range_is_inclusive_of_whole_days(self):
        early, inside, late = self._make(), self._make(), self._make()
        with self.db.get_cursor() as cursor:
            for generation_id, stamp in ((early, "2026-01-01 10:00:00"), (inside, "2026-01-05 23:30:00"),
                                         (late, "2026-01-09 08:00:00")):
                cursor.execute("UPDATE generations SET completed_at = ? WHERE id = ?", (stamp, generation_id))

        ids, count = self._both(HistoryFilter(user_id=self.user_id, completed_from="2026-01-02", completed_to="2026-01-05"))

        self.assertEqual(ids, {inside})
        self.assertEqual(count, 1)

    def test_collection_filter_honours_include_descendants(self):
        parent = self.collections.create("Parent", self.user_id, "history")
        child = self.collections.create("Child", self.user_id, "history", parent_id=parent.id)
        in_parent, in_child, elsewhere = self._make(), self._make(), self._make()
        self._file(in_parent, parent.id)
        self._file(in_child, child.id)

        direct_ids, direct_count = self._both(
            HistoryFilter(user_id=self.user_id, collection_id=parent.id, include_descendants=False)
        )
        rolled_ids, rolled_count = self._both(
            HistoryFilter(user_id=self.user_id, collection_id=parent.id, include_descendants=True)
        )

        self.assertEqual(direct_ids, {in_parent})
        self.assertEqual(direct_count, 1)
        self.assertEqual(rolled_ids, {in_parent, in_child})
        self.assertEqual(rolled_count, 2)
        self.assertNotIn(elsewhere, rolled_ids)

