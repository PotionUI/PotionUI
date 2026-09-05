"""
Cross-check between the frontend's `planDirectorSelection` chain branch
(frontend/src/lib/utils/directorPlanner.ts) and the real
`compile_shot_plan` (src/features/video_director/compile.py): every fixture
here mirrors one of `directorPlanner.test.ts`'s
"chain (segment_routing) validates the checked span itself" cases (a fresh
cut c1, c2 continuing c1, c3 continuing c2). What the frontend planner would
accept must actually be accepted by `compile_shot_plan`; what it blocks
(a selection starting mid-continuation, or a noncontiguous one) must actually
be rejected -- the frontend UI's readiness reads only ever mean anything if
they agree with the compiler that runs after them.

Builds documents through `normalize_video_director` (never a hand-rolled
already-normalized dict) so `sub_type` is derived by the SAME
`derive_segment_sub_type` the frontend's `deriveChainSegmentSubType` mirrors,
exactly the shape `GenerationOrchestrator` hands `compile_shot_plan` in
production.
"""

import pytest

from src.features.video_director.compile import compile_shot_plan
from src.features.video_director.normalize import (
    VideoDirectorValidationError,
    normalize_video_director,
)

_CHAIN_CAPS = {
    "preset_modes": ["video"],
    "segment_routing": True,
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {"keyframes": "first_only", "max_segments": 8},
    },
    "limits": {"default_duration": 5, "default_fps": 16, "max_duration": 60},
}


@pytest.fixture
def three_segment_chain(tmp_path):
    """c1 fresh (t2v, index 0); c2/c3 prompt-only later segments -> both
    derive 'chain' (continue their predecessor) -- the same
    `threeSegmentChain()` fixture directorPlanner.test.ts builds via
    `normalizeDirectorValue`, here built through the real backend
    normalizer instead of the frontend's mirror of it."""
    document = {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 16, "seed": 7},
        "segments": [
            {"id": "c1", "prompt": "fresh cut", "frames": 49},
            {"id": "c2", "prompt": "continues c1", "frames": 49},
            {"id": "c3", "prompt": "continues c2", "frames": 49},
        ],
    }
    return normalize_video_director(document, _CHAIN_CAPS, str(tmp_path))


class TestPlannerAcceptedSelectionsCompile:
    """`planDirectorSelection` reports `blockingReasons: []` for these --
    `compile_shot_plan` must accept the exact `shotsToSubmit` it would emit."""

    def test_full_contiguous_span_from_the_fresh_cut(self, three_segment_chain):
        compiled = compile_shot_plan(three_segment_chain, ["c1", "c2", "c3"])
        assert [s["id"] for s in compiled["segments"]] == ["c1", "c2", "c3"]

    def test_checked_out_of_film_order_still_compiles_in_film_order(self, three_segment_chain):
        # buildDirectorSubmission's `orderedChecked` always resolves checked ids
        # to film order before this is ever called (PLAN.md's own discipline);
        # compile_shot_plan re-sorts regardless, so this holds either way.
        compiled = compile_shot_plan(three_segment_chain, ["c3", "c1", "c2"])
        assert [s["id"] for s in compiled["segments"]] == ["c1", "c2", "c3"]


class TestPlannerBlockedSelectionsAreActuallyRejected:
    """`planDirectorSelection` reports non-empty `blockingReasons` (and an
    empty `shotsToSubmit`, i.e. nothing is ever sent) for these -- confirms
    the frontend's block isn't overcautious relative to what the backend
    actually enforces, and would still reject them if something upstream ever
    forwarded them anyway."""

    def test_selection_starting_on_a_continuation_shot_is_rejected(self, three_segment_chain):
        with pytest.raises(VideoDirectorValidationError, match="needs its previous shot"):
            compile_shot_plan(three_segment_chain, ["c2"])

    def test_multi_hop_selection_missing_its_earliest_predecessor_is_rejected(self, three_segment_chain):
        # Matches directorPlanner.test.ts's "checking c2 and c3 without c1"
        # case -- c2 (the span's start once c1 is excluded) still resolves to
        # 'chain', so this is rejected the same way selecting c2 alone is.
        with pytest.raises(VideoDirectorValidationError, match="needs its previous shot"):
            compile_shot_plan(three_segment_chain, ["c2", "c3"])

    def test_noncontiguous_selection_skipping_the_middle_shot_is_rejected(self, three_segment_chain):
        with pytest.raises(VideoDirectorValidationError, match="contiguous"):
            compile_shot_plan(three_segment_chain, ["c1", "c3"])
