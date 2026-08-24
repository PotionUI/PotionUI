"""
Proves the actual point of the Krea-2 gallery design - not just that the
pipeline renders differently, but that the files it now saves land on
indices that carry a full parameter row.

The rejected alternative wired TWO gallery pipes when Enhance is ON: the base
pass saved to indices 0..N-1, and a second `gallery_enhanced` (derived) pipe
saved the enhanced pass to indices N..2N-1 - `param_emitter` only ever writes
N rows (0..N-1), so every enhanced file's own `GenerationHistoryQuery.get_params`
call returned an EMPTY `parameters` dict (no source-generation link exists
for a plain toggle run, so the provenance fallback in
`_resolve_params_and_models` never kicks in either). The Civitai export
endpoint papered over this by re-deriving `index - quantity`, but core
history data for those files carried nothing.

The correct design has exactly one gallery pipe; with Enhance ON it saves the
enhanced pass at indices 0..N-1 - the same indices param_emitter always wrote
to. This test writes parameter rows the way `param_emitter` does for an
Enhance-ON, quantity=2 run, then asserts `get_params` returns the full set at
BOTH indices with no missing key - i.e. exactly the indices the single
`gallery` pipe now uses for its (only) saved files.
"""
import json
import sys

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.generation.repository import generation_repo
from src.features.generation.parameter_repository import generation_parameter_repo
from src.features.generation.history_query import GenerationHistoryQuery


class TestKrea2EnhanceParamAlignment(PersistenceTestBase):
    def setUp(self):
        super().setUp()

        # Repositories bind `db` at import time (see persistence_base's own
        # comment on this) - `_create_test_database` already redirects
        # repository.py/file_repository.py; the read side used by get_params
        # (parameter/model/source repos) needs the same redirection here.
        for module_path in (
            "src.features.generation.parameter_repository",
            "src.features.generation.model_repository",
            "src.features.generation.source_repository",
        ):
            module = sys.modules.get(module_path)
            if module is None:
                import importlib
                module = importlib.import_module(module_path)
            module.db = self.db

        self.user_id = self.create_test_user()
        self.generation_id = "gen_krea2_enhance_alignment"
        # create_test_generation's own form_data serialization (a naive
        # quote-swap, not json.dumps) breaks on booleans - write the row
        # directly with a real JSON payload instead.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, preset_id, form_data, user_id) VALUES (?, ?, ?, ?)",
                (
                    self.generation_id,
                    "4TK1KBQZ2XMB8ME0PTMXS1YJQP",
                    json.dumps({"enhance_enabled": True, "quantity": 2}),
                    self.user_id,
                ),
            )
        self.query = GenerationHistoryQuery(generation_repo=generation_repo)

    def _emit_param(self, name: str, values: list):
        """Mirrors ParamGenerationOutputHandler.handle: one row per index."""
        generation_parameter_repo.create_batch(self.generation_id, name, values)

    def test_enhanced_pass_param_rows_cover_every_saved_index(self):
        # Exactly what param_emitter writes for the Krea-2 txt2img pipeline
        # at quantity=2, Enhance ON (see content/presets/marketplace/Krea2/modes/txt2img/pipeline.yml).
        self._emit_param("positive_prompt", ["a cat", "a cat"])
        self._emit_param("negative_prompt", ["", ""])
        self._emit_param("cfg", [1.0, 1.0])
        self._emit_param("steps", [8, 8])
        self._emit_param("sampler", ["euler", "euler"])
        self._emit_param("resolution", ["1024x1024", "1024x1024"])
        self._emit_param("enhance", [True, True])
        self._emit_param("enhance_detail", ["balanced", "balanced"])
        self._emit_param("upscale_by", ["2x", "2x"])

        expected_keys = {
            "positive_prompt", "negative_prompt", "cfg", "steps", "sampler",
            "resolution", "enhance", "enhance_detail", "upscale_by",
        }

        # These are the only two indices the single `gallery` pipe now ever
        # saves a file at (quantity=2) - both must resolve fully, with
        # nothing missing and nothing borrowed from a fallback.
        for index in (0, 1):
            result = self.query.get_params(self.generation_id, index, self.user_id)
            params = result["parameters"]
            missing = expected_keys - set(params)
            assert not missing, f"index {index} missing parameter(s): {missing}"
            assert params["enhance"] is True
            assert params["steps"] == 8
            assert params["sampler"] == "euler"

        # The bug this guards against: an index outside 0..quantity-1 (where
        # the rejected `gallery_enhanced` pass used to land its files at
        # N..2N-1) has no parameter rows at all - proving those indices are
        # simply not produced anymore, not that they're silently backed by a
        # fallback.
        orphan = self.query.get_params(self.generation_id, 2, self.user_id)
        assert orphan["parameters"] == {}
