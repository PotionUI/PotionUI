"""Tests for `compile_shot_plan`'s family-aware span geometry (the `family`
keyword compile.py's `_effective_segment_durations` dispatches on).

Before this module existed, `compile_shot_plan` derived every shot's start
and the selected span's duration from a raw `sum(segment.frames) / fps`,
regardless of family. That is correct for a family with no per-window frame
snap and no continuation-overlap trim (the legacy path, still exercised by
`test_compile.py` and unchanged here), but wrong for MiniMax-H3: a window's
requested frame count snaps up to the video VAE's `17n+5` lattice, and a
continuation window's leading `overlap_frames` REPLAY the previous window's
tail rather than adding new footage to the stitched result -- see
`video_minimax_h3/windows.py`'s module docstring. These tests build a
MiniMax-H3-shaped document through the real
`normalize_video_director` -> `compile_shot_plan(..., family="minimax_h3")`
path and cross-check the result against `build_director_plan`, the same
window planner the generator itself runs.
"""

import pytest

from src.features.video_director.compile import compile_shot_plan
from src.features.video_director.normalize import normalize_video_director
from src.pipelines.pipes.generator.video_minimax_h3.geometry import latent_index_for_frame
from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan

_LIMITS = {"default_duration": 5, "default_fps": 24, "max_duration": 15}

# Mirrors content/presets/marketplace/MiniMax-H3/preset.yml's `video`-mode
# `video_director` capabilities: keyframes anywhere, one VAE chunk (17
# frames) of tail-frame continuation overlap by default.
H3_CAPS = {
    "preset_modes": ["video", "refs"],
    "segment_routing": True,
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {
            "keyframes": "anywhere",
            "audio": True,
            "max_segments": 6,
            "max_frames_per_segment": 345,
            "continuation": {"source": "tail_frames", "overlap_frames": 17, "stitch": True},
            "max_overlap_frames": 34,
        },
    },
    "limits": _LIMITS,
}

# The `refs` capability override: continuation is a declared `null`, so every
# shot is an independent cut -- no family-vs-legacy divergence to find, but
# still exercises the H3 alignment snap with zero overlap.
H3_NO_OVERLAP_CAPS = {
    **H3_CAPS,
    "modes": {
        **H3_CAPS["modes"],
        "director": {**H3_CAPS["modes"]["director"], "continuation": None, "max_overlap_frames": None},
    },
}

FPS = 24


@pytest.fixture
def storage_dir(tmp_path):
    (tmp_path / "image.png").write_bytes(b"fake-image")
    return tmp_path


def _segment(seg_id, frames, **overrides):
    seg = {"id": seg_id, "prompt": "a cat", "negative_prompt": "", "frames": frames}
    seg.update(overrides)
    return seg


# Mirrors the preset's own continuation default (one VAE chunk of tail-frame
# overlap) -- normalize_video_director only VALIDATES a document's own
# `settings.continuation` against the capability's `max_overlap_frames`; it
# never defaults it in from the capability, so every fixture that wants
# continuation active must submit this explicitly, exactly like the real
# Director editor does.
_CONTINUATION = {"source": "tail_frames", "overlap_frames": 17, "stitch": True}


def _doc(segments, media=None, seed=1000, continuation=_CONTINUATION):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {
            "fps": FPS, "duration": None, "resolution": "", "seed": seed, "continuation": continuation,
        },
        "segments": segments,
        "media": media or [],
        "audio": [],
        "ic_lora": [],
    }


def _keyframe(at):
    return {"id": "kf", "role": "keyframe", "segment_id": None, "at": at, "strength": 1.0,
             "media": {"relative_path": "image.png"}}


@pytest.fixture
def three_shot_doc(storage_dir):
    """A(t2v, 56f) / B(chain, 56f, continues A with a 17-frame overlap) /
    C(t2v, 56f, a fresh cut) -- the exact case from the DIR-04 report: the
    full plan emits A whole (56), B trimmed by its overlap (56-17=39), and C
    whole (56), landing C's own window at cumulative frame 95 (95/24 s),
    not the naive raw-sum 112/24 s two whole 56-frame segments would give.
    """
    raw = _doc(segments=[
        _segment("seg-a", 56, sub_type="t2v"),
        _segment("seg-b", 56),  # derives to "chain": continues seg-a
        _segment("seg-c", 56, sub_type="t2v"),  # explicit fresh cut
    ], media=[_keyframe(4.25)])  # global frame 102 -- inside seg-c's real window
    return normalize_video_director(raw, H3_CAPS, str(storage_dir))


class TestTheReportedCase:
    def test_full_plan_places_c_at_frame_95_not_the_raw_sum_112(self, three_shot_doc):
        """Ground truth from the actual generator planner, not hand arithmetic."""
        plan = build_director_plan(three_shot_doc, default_seed=1000)
        assert [w.emitted_frames for w in plan.windows] == [56, 39, 56]
        cumulative = 0
        starts = []
        for window in plan.windows:
            starts.append(cumulative)
            cumulative += window.emitted_frames
        assert starts == [0, 56, 95]  # not [0, 56, 112]

    def test_compiling_c_alone_keeps_the_keyframe_at_the_real_span_start(self, three_shot_doc):
        compiled = compile_shot_plan(three_shot_doc, ["seg-c"], family="minimax_h3")
        assert compiled["segments"] == compiled["segments"]  # sanity: no exception above
        assert compiled["settings"]["duration"] == pytest.approx(56 / FPS)
        (keyframe,) = [m for m in compiled["media"] if m["role"] == "keyframe"]
        # Global t=4.25s (frame 102) rebased onto seg-c's REAL start (frame
        # 95, i.e. t=95/24s) is frame 7 into the shot, not frame 102 - 112 =
        # negative (which the naive raw-sum axis would have dropped outright).
        assert keyframe["at"] == pytest.approx(7 / FPS)
        (placement,) = compiled["media_placements"]
        assert placement["frame"] == 7

    def test_the_compiled_document_re_plans_the_keyframe_at_c_local_frame_7(self, three_shot_doc):
        """End to end: hand the COMPILED (single-shot) document back to the
        real window planner and confirm it independently agrees the
        keyframe lands at local frame 7 -- before the frame is converted to
        a latent index."""
        compiled = compile_shot_plan(three_shot_doc, ["seg-c"], family="minimax_h3")
        plan = build_director_plan(compiled, default_seed=1000)
        assert len(plan.windows) == 1
        (keyframe,) = plan.windows[0].keyframes
        assert keyframe.frame == 7
        assert keyframe.latent_index == latent_index_for_frame(7)

    def test_a_keyframe_outside_the_selected_span_is_dropped(self, three_shot_doc):
        compiled = compile_shot_plan(three_shot_doc, ["seg-a"], family="minimax_h3")
        assert compiled["media"] == []

    def test_without_a_family_the_legacy_raw_sum_axis_is_used_instead(self, three_shot_doc):
        """Backward compatibility: an unspecified `family` (every caller
        before this change, and every non-minimax_h3 preset today) must keep
        the OLD raw-frame-sum axis byte for byte -- including its bug, which
        is exactly why the keyframe is dropped here instead of kept."""
        compiled = compile_shot_plan(three_shot_doc, ["seg-c"])
        assert compiled["media"] == []  # the naive 112/24s start drops the t=4.25s keyframe
        assert compiled["settings"]["duration"] == pytest.approx(56 / FPS)


class TestMultiShotSpanAndOverlapVariants:
    def test_a_multi_shot_span_spans_the_real_emitted_duration(self, three_shot_doc):
        """[seg-a, seg-b] together: seg-b's overlap against seg-a is trimmed
        WITHIN the span (both segments are selected), so the span is
        56 + (56 - 17) = 95 frames, not a raw 112."""
        compiled = compile_shot_plan(three_shot_doc, ["seg-a", "seg-b"], family="minimax_h3")
        assert compiled["settings"]["duration"] == pytest.approx((56 + (56 - 17)) / FPS)

    def test_no_overlap_continuation_matches_the_raw_sum(self, storage_dir):
        """Every shot an independent cut (continuation disabled): the H3
        axis and the legacy raw-sum axis agree, since there is no overlap to
        trim and the requested frames are already lattice-aligned."""
        raw = _doc(segments=[
            _segment("seg-a", 56, sub_type="t2v"),
            _segment("seg-b", 56, sub_type="t2v"),
            _segment("seg-c", 56, sub_type="t2v"),
        ], continuation=None)
        doc = normalize_video_director(raw, H3_NO_OVERLAP_CAPS, str(storage_dir))
        h3 = compile_shot_plan(doc, ["seg-c"], family="minimax_h3")
        legacy = compile_shot_plan(doc, ["seg-c"])
        assert h3["settings"]["duration"] == pytest.approx(legacy["settings"]["duration"])
        assert h3["settings"]["duration"] == pytest.approx(56 / FPS)

    def test_frames_off_the_vae_lattice_snap_up_before_becoming_seconds(self, storage_dir):
        """130 requested frames snaps to 141 (17*8+5) -- the compiled span's
        duration must reflect the SNAPPED frame count, not the requested one."""
        raw = _doc(segments=[_segment("seg-a", 130, sub_type="t2v")], continuation=None)
        doc = normalize_video_director(raw, H3_NO_OVERLAP_CAPS, str(storage_dir))
        compiled = compile_shot_plan(doc, ["seg-a"], family="minimax_h3")
        assert compiled["settings"]["duration"] == pytest.approx(141 / FPS)
        assert compiled["settings"]["duration"] != pytest.approx(130 / FPS)
