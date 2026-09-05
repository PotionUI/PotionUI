"""Tests for `compile_shot_plan`'s `family="wan"` span geometry -- the Wan
counterpart of `test_compile_geometry.py`'s MiniMax-H3 suite.

Before this module existed, a Wan chain document's per-shot render duration
was always the raw `sum(segment.frames) / fps`, ignoring the causal VAE's
`1 + 4k` frame-count snap and the continuation context-trim
(`chain_video_wan22/geometry.py`). Unlike `minimax_h3`, that arithmetic ALSO
needs `motion_latent_count` -- a generation-time pipe config value that never
lives on the document itself -- so `compile_shot_plan` only takes the Wan
geometry path when the document already carries an explicit
`settings.timing_profile` (what the orchestrator attaches from the bound
form before compiling ever runs -- see `orchestrator.py`'s `timing_capability`
handling). These tests build a Wan-shaped document through the real
`normalize_video_director` -> `compile_shot_plan(..., family="wan")` path and
cross-check the result against `chain_video_wan22/geometry.py`'s own
`resolve_window_geometry`, the same module the compiler calls.
"""

import pytest

from src.features.video_director.compile import compile_shot_plan
from src.features.video_director.normalize import normalize_video_director
from src.pipelines.pipes.generator.chain_video_wan22.geometry import resolve_window_geometry

_LIMITS = {"default_duration": 5, "default_fps": 16, "max_duration": 60}

# Mirrors content/presets/marketplace/Wan/preset.yml's `video`-mode
# `video_director` capabilities: first_only keyframes (no free-floating
# "anywhere" placement -- unlike MiniMax-H3), a 4-frame tail-frame
# continuation default.
WAN_CAPS = {
    "preset_modes": ["video"],
    "segment_routing": True,
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {
            "keyframes": "first_only",
            "per_segment_loras": True,
            "max_segments": 8,
            "max_frames_per_segment": 81,
            "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True},
        },
    },
    "limits": _LIMITS,
}

FPS = 16
_CONTINUATION = {"source": "tail_frames", "overlap_frames": 4, "stitch": True}


@pytest.fixture
def storage_dir(tmp_path):
    return tmp_path


def _segment(seg_id, frames, **overrides):
    seg = {"id": seg_id, "prompt": "a cat", "negative_prompt": "", "frames": frames}
    seg.update(overrides)
    return seg


def _doc(segments, seed=1000, continuation=_CONTINUATION):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {
            "fps": FPS, "duration": None, "resolution": "", "seed": seed, "continuation": continuation,
        },
        "segments": segments,
        "media": [],
        "audio": [],
        "ic_lora": [],
    }


def _with_timing_profile(doc, motion_latent_count):
    """Mirrors `orchestrator.py`'s own attach step -- exactly what a real
    submission does between `normalize_video_director` and
    `compile_shot_plan` once the preset declares a `timing` capability."""
    return {**doc, "settings": {**doc["settings"], "timing_profile": {"motion_latent_count": motion_latent_count}}}


@pytest.fixture
def three_shot_doc(storage_dir):
    """A(t2v, 80f) / B(chain, 80f, continues A) / C(t2v, 80f, a fresh cut) --
    the exact reported case: 80-frame requests snap to 81, and a motion-2
    profile trims B's continuation overlap to 4 frames ([81, 77, 81])."""
    raw = _doc(segments=[
        _segment("seg-a", 80, sub_type="t2v"),
        _segment("seg-b", 80),  # derives to "chain": continues seg-a
        _segment("seg-c", 80, sub_type="t2v"),  # explicit fresh cut
    ])
    return normalize_video_director(raw, WAN_CAPS, str(storage_dir))


class TestTheReportedCase:
    def test_motion_2_profile_agrees_with_the_geometry_module_on_81_77_81(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=2)
        geometry = resolve_window_geometry(doc["segments"], doc["settings"])
        assert [g.emitted_frames for g in geometry] == [81, 77, 81]

        compiled = compile_shot_plan(doc, ["seg-a", "seg-b", "seg-c"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((81 + 77 + 81) / FPS)

    def test_motion_1_profile_agrees_with_the_geometry_module_on_81_80_81(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=1)
        geometry = resolve_window_geometry(doc["segments"], doc["settings"])
        assert [g.emitted_frames for g in geometry] == [81, 80, 81]

        compiled = compile_shot_plan(doc, ["seg-a", "seg-b", "seg-c"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((81 + 80 + 81) / FPS)

    def test_compiling_c_alone_reports_its_own_real_emitted_duration(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=2)
        compiled = compile_shot_plan(doc, ["seg-c"], family="wan")
        # seg-c is a fresh t2v cut -- its OWN duration, unaffected by seg-b's
        # overlap trim (that trim only shortens seg-b's own contribution).
        assert compiled["settings"]["duration"] == pytest.approx(81 / FPS)

    def test_compiling_a_and_b_together_trims_bs_overlap_within_the_span(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=2)
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((81 + 77) / FPS)

    def test_without_a_timing_profile_the_legacy_raw_sum_axis_is_used_instead(self, three_shot_doc):
        """A `wan`-family document with no `settings.timing_profile` attached
        (a preset instance that hasn't wired the `timing` capability, or a
        hand-built document) is "unqualified" -- compile_shot_plan falls back
        to the raw (un-snapped, un-trimmed) axis rather than guessing a
        motion_latent_count."""
        compiled = compile_shot_plan(three_shot_doc, ["seg-a", "seg-b", "seg-c"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((80 + 80 + 80) / FPS)

    def test_without_a_family_the_legacy_raw_sum_axis_is_used_regardless_of_the_profile(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=2)
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b", "seg-c"])
        assert compiled["settings"]["duration"] == pytest.approx((80 + 80 + 80) / FPS)


class TestSeedAndProvenanceUnaffectedByFamily:
    def test_a_compiled_spans_baked_seed_and_sequence_index_do_not_depend_on_family(self, three_shot_doc):
        doc = _with_timing_profile(three_shot_doc, motion_latent_count=2)
        wan = compile_shot_plan(doc, ["seg-c"], family="wan")
        legacy = compile_shot_plan(doc, ["seg-c"])
        assert wan["segments"][0]["seed"] == legacy["segments"][0]["seed"] == 1000 + 2
        assert wan["segments"][0]["sequence_index"] == legacy["segments"][0]["sequence_index"] == 2


class TestOverlapVariants:
    def test_no_continuation_block_matches_the_raw_sum_once_frames_are_lattice_aligned(self, storage_dir):
        raw = _doc(segments=[
            _segment("seg-a", 81, sub_type="t2v"),
            _segment("seg-b", 81, sub_type="t2v"),
        ], continuation=None)
        doc = normalize_video_director(raw, WAN_CAPS, str(storage_dir))
        doc = _with_timing_profile(doc, motion_latent_count=2)
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((81 + 81) / FPS)

    def test_last_frame_source_trims_a_single_frame_regardless_of_motion(self, storage_dir):
        raw = _doc(segments=[
            _segment("seg-a", 80, sub_type="t2v"),
            _segment("seg-b", 80),
        ], continuation={"source": "last_frame", "overlap_frames": 4, "stitch": True})
        doc = normalize_video_director(raw, WAN_CAPS, str(storage_dir))
        doc = _with_timing_profile(doc, motion_latent_count=4)
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((81 + 80) / FPS)

    def test_frames_off_the_lattice_snap_up_before_becoming_seconds(self, storage_dir):
        # No per-segment frame cap here (unlike the real Wan preset's 81):
        # this fixture only exercises the lattice snap itself, on a request
        # larger than that preset's actual ceiling would allow.
        caps = {**WAN_CAPS, "modes": {**WAN_CAPS["modes"], "director": {**WAN_CAPS["modes"]["director"], "max_frames_per_segment": None}}}
        raw = _doc(segments=[_segment("seg-a", 130, sub_type="t2v")], continuation=None)
        doc = normalize_video_director(raw, caps, str(storage_dir))
        doc = _with_timing_profile(doc, motion_latent_count=2)
        compiled = compile_shot_plan(doc, ["seg-a"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx(129 / FPS)
        assert compiled["settings"]["duration"] != pytest.approx(130 / FPS)

    def test_stitch_off_does_not_change_the_effective_duration_math(self, storage_dir):
        """`stitch` only governs whether the generator additionally muxes the
        segments into one continuous file at generation time -- the per-shot
        editor timeline (what compile_shot_plan reports) is the same either
        way, mirroring how the MiniMax-H3 planner also ignores its own
        `DirectorPlan.stitch` for this purpose."""
        raw_on = _doc(segments=[
            _segment("seg-a", 80, sub_type="t2v"), _segment("seg-b", 80),
        ], continuation={"source": "tail_frames", "overlap_frames": 4, "stitch": True})
        raw_off = _doc(segments=[
            _segment("seg-a", 80, sub_type="t2v"), _segment("seg-b", 80),
        ], continuation={"source": "tail_frames", "overlap_frames": 4, "stitch": False})
        doc_on = _with_timing_profile(normalize_video_director(raw_on, WAN_CAPS, str(storage_dir)), 2)
        doc_off = _with_timing_profile(normalize_video_director(raw_off, WAN_CAPS, str(storage_dir)), 2)
        on = compile_shot_plan(doc_on, ["seg-a", "seg-b"], family="wan")
        off = compile_shot_plan(doc_off, ["seg-a", "seg-b"], family="wan")
        assert on["settings"]["duration"] == pytest.approx(off["settings"]["duration"])

    def test_a_short_window_still_leaves_at_least_one_frame_of_duration(self, storage_dir):
        raw = _doc(segments=[
            _segment("seg-a", 1, sub_type="t2v"), _segment("seg-b", 1),
        ], continuation={"source": "tail_frames", "overlap_frames": 8, "stitch": True})
        doc = _with_timing_profile(normalize_video_director(raw, WAN_CAPS, str(storage_dir)), 4)
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b"], family="wan")
        assert compiled["settings"]["duration"] == pytest.approx((1 + 1) / FPS)
