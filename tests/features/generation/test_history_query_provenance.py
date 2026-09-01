"""Tests for GenerationHistoryQuery.get_params's provenance
inheritance: when a generation carries a `generation_sources` link (an
"enhance" run seeded from a prior generation's output), missing/empty own
params and models fall back to the linked source - recursively, through an
enhance-of-enhance chain, with a cycle guard and a depth cap.

Uses a real (temp-file) database via PersistenceTestBase, which redirects the
canonical `src.platform.database.database.db` name every repository resolves
at call time.
"""

import sys
import os
import json

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.repository import GenerationRepository
from src.features.generation.parameter_repository import GenerationParameterRepository
from src.features.generation.model_repository import GenerationModelRepository
from src.features.generation.source_repository import GenerationSourceRepository
from src.platform.util.ids import generate_ulid


class TestGetParamsProvenanceInheritance(PersistenceTestBase):

    def setUp(self):
        super().setUp()

        self.param_repo = GenerationParameterRepository()
        self.model_repo = GenerationModelRepository()
        self.source_repo = GenerationSourceRepository()

        self.user_id = self.create_test_user()
        self.query = GenerationHistoryQuery(generation_repo=GenerationRepository())

    def _create_generation(self, gen_id: str = None) -> str:
        gen_id = gen_id or generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generations (id, preset_id, form_data, user_id, status)
                VALUES (?, ?, ?, ?, ?)
            """, (gen_id, "test_preset", json.dumps({"prompt": "test"}), self.user_id, "completed"))
        return gen_id

    def _set_param(self, gen_id: str, index: int, name: str, value):
        # create_batch places `values[i]` at parameter_index=i - pad with a
        # throwaway placeholder for indices below the one under test.
        values = [None] * index + [value]
        self.param_repo.create_batch(gen_id, name, values)

    def _create_model(self, model_id: str = None, filename: str = "model.safetensors") -> str:
        model_id = model_id or generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO models (id, filename, file_path, file_size, model_type, sha256)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (model_id, filename, f"/models/{filename}", 1024, "checkpoint", f"sha_{model_id}"))
        return model_id

    def _link_model(self, gen_id: str, model_id: str):
        self.model_repo.create_batch(gen_id, [model_id])

    def _link_source(self, child_id: str, source_id: str, source_index: int = 0, field_name: str = "source_image"):
        self.source_repo.create_for_generation(child_id, [{
            "field_name": field_name,
            "source_generation_id": source_id,
            "source_file_index": source_index,
        }])

    # --- baseline: unaffected when there is no provenance link --------------

    def test_no_source_link_returns_only_own_params_and_models(self):
        gen = self._create_generation()
        self._set_param(gen, 0, "prompt", "a cat")
        model_id = self._create_model()
        self._link_model(gen, model_id)

        result = self.query.get_params(gen, 0, self.user_id)

        self.assertEqual(result["parameters"], {"prompt": "a cat"})
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["id"], model_id)

    # --- merge semantics ------------------------------------------------------

    def test_missing_own_key_inherits_from_source(self):
        source = self._create_generation()
        self._set_param(source, 0, "seed", 12345)
        child = self._create_generation()
        self._set_param(child, 0, "prompt", "enhanced cat")
        self._link_source(child, source, source_index=0)

        result = self.query.get_params(child, 0, self.user_id)

        self.assertEqual(result["parameters"]["prompt"], "enhanced cat")
        self.assertEqual(result["parameters"]["seed"], 12345)

    def test_empty_string_own_value_falls_back_to_source(self):
        source = self._create_generation()
        self._set_param(source, 0, "cfg", 7.5)
        child = self._create_generation()
        self._set_param(child, 0, "cfg", "")
        self._link_source(child, source, source_index=0)

        result = self.query.get_params(child, 0, self.user_id)

        self.assertEqual(result["parameters"]["cfg"], 7.5)

    def test_non_empty_own_value_wins_over_source(self):
        source = self._create_generation()
        self._set_param(source, 0, "prompt", "original prompt")
        child = self._create_generation()
        self._set_param(child, 0, "prompt", "edited prompt")
        self._link_source(child, source, source_index=0)

        result = self.query.get_params(child, 0, self.user_id)

        self.assertEqual(result["parameters"]["prompt"], "edited prompt")

    def test_source_file_index_is_respected(self):
        """The source generation's params are looked up at
        `source_file_index`, not blindly at the child's own index."""
        source = self._create_generation()
        self._set_param(source, 0, "seed", 111)
        self._set_param(source, 2, "seed", 222)
        child = self._create_generation()
        self._link_source(child, source, source_index=2)

        result = self.query.get_params(child, 0, self.user_id)

        self.assertEqual(result["parameters"]["seed"], 222)

    # --- models union -----------------------------------------------------

    def test_models_union_own_first_then_source(self):
        source = self._create_generation()
        source_model = self._create_model(filename="source_checkpoint.safetensors")
        self._link_model(source, source_model)

        child = self._create_generation()
        child_model = self._create_model(filename="enhance_lora.safetensors")
        self._link_model(child, child_model)
        self._link_source(child, source, source_index=0)

        result = self.query.get_params(child, 0, self.user_id)

        model_ids = [m["id"] for m in result["models"]]
        self.assertEqual(model_ids, [child_model, source_model])

    def test_models_union_dedupes_by_id(self):
        source = self._create_generation()
        shared_model = self._create_model()
        self._link_model(source, shared_model)

        child = self._create_generation()
        self._link_model(child, shared_model)
        self._link_source(child, source, source_index=0)

        result = self.query.get_params(child, 0, self.user_id)

        model_ids = [m["id"] for m in result["models"]]
        self.assertEqual(model_ids, [shared_model])

    # --- chain recursion (enhance-of-enhance) ------------------------------

    def test_chain_of_two_links_inherits_from_the_root(self):
        root = self._create_generation()
        self._set_param(root, 0, "seed", 999)

        middle = self._create_generation()
        self._set_param(middle, 0, "prompt", "middle prompt")
        self._link_source(middle, root, source_index=0)

        leaf = self._create_generation()
        self._set_param(leaf, 0, "cfg", 5.0)
        self._link_source(leaf, middle, source_index=0)

        result = self.query.get_params(leaf, 0, self.user_id)

        self.assertEqual(result["parameters"]["cfg"], 5.0)
        self.assertEqual(result["parameters"]["prompt"], "middle prompt")
        self.assertEqual(result["parameters"]["seed"], 999)

    def test_depth_cap_stops_recursion_past_max_provenance_depth(self):
        """Build a chain one hop longer than `_MAX_PROVENANCE_DEPTH` (5): the
        hop at the cap's own values are included, but its own further source
        is never followed."""
        max_depth = GenerationHistoryQuery._MAX_PROVENANCE_DEPTH
        chain = [self._create_generation() for _ in range(max_depth + 2)]
        # chain[0] is the top-level generation under test; chain[i]'s source
        # is chain[i+1].
        for i, gen_id in enumerate(chain):
            self._set_param(gen_id, 0, f"marker_{i}", f"value_{i}")
        for i in range(len(chain) - 1):
            self._link_source(chain[i], chain[i + 1], source_index=0)

        result = self.query.get_params(chain[0], 0, self.user_id)

        # depth 0..max_depth are all reachable (own values computed even at
        # the cap before recursion stops), depth max_depth+1 is not.
        for i in range(0, max_depth + 1):
            self.assertIn(f"marker_{i}", result["parameters"])
        self.assertNotIn(f"marker_{max_depth + 1}", result["parameters"])

    def test_cycle_guard_terminates_and_merges_both_sides(self):
        gen_a = self._create_generation()
        gen_b = self._create_generation()
        self._set_param(gen_a, 0, "prompt", "from a")
        self._set_param(gen_b, 0, "seed", 42)
        self._link_source(gen_a, gen_b, source_index=0)
        self._link_source(gen_b, gen_a, source_index=0)

        # Must terminate (no RecursionError/hang) and still merge both sides.
        result = self.query.get_params(gen_a, 0, self.user_id)

        self.assertEqual(result["parameters"]["prompt"], "from a")
        self.assertEqual(result["parameters"]["seed"], 42)

    def test_source_link_cascades_away_with_its_source_generation(self):
        """`generation_sources.source_generation_id` is `ON DELETE CASCADE`
        (like `generation_models`) - deleting the source generation removes
        the link itself rather than leaving it dangling, so inheritance
        degrades to own-only without any defensive lookup ever seeing a
        nonexistent source."""
        source = self._create_generation()
        child = self._create_generation()
        self._set_param(child, 0, "prompt", "still here")
        self._link_source(child, source, source_index=0)

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM generations WHERE id = ?", (source,))

        self.assertEqual(self.source_repo.get_by_generation(child), [])
        result = self.query.get_params(child, 0, self.user_id)
        self.assertEqual(result["parameters"], {"prompt": "still here"})
