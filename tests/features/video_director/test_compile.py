"""
Tests for `compile_shot_plan` -- the per-shot generation compiler that runs
AFTER `normalize_video_director` (see src/features/video_director/compile.py).

Every fixture document is built by actually calling `normalize_video_director`
against a chain-style (`segment_routing: true`) capability set, exactly like
`GenerationOrchestrator.start_generation()` does before ever reaching
`compile_shot_plan` -- so these tests exercise the compiler against the same
shape the orchestrator hands it, not a hand-rolled approximation of it.
"""

import pytest

from src.features.video_director.compile import compile_shot_plan
from src.features.video_director.normalize import (
    VideoDirectorValidationError,
    normalize_video_director,
)

_LIMITS = {"default_duration": 5, "default_fps": 24, "max_duration": 60}

# A Wan-like chain preset: five 1fps/5-frame (5s) segments, keyframes allowed
# ANYWHERE along the chain (not just first_only) and audio enabled, so the
# media/audio `at` rebasing paths are actually exercised.
CHAIN_CAPS = {
    "preset_modes": ["video"],
    "segment_routing": True,
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {
            "keyframes": "anywhere",
            "audio": True,
            "max_segments": 8,
            "max_frames_per_segment": 81,
        },
    },
    "limits": _LIMITS,
}

# A MiniMax-H3-refs-like preset: continuation disabled (a derived "chain"
# resolves to "t2v" instead), per-shot reference selection from a packed pool.
REFS_CAPS = {
    "preset_modes": ["refs"],
    "segment_routing": True,
    "references": "per_shot",
    "reference_fields": ["references"],
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {"max_segments": 8, "max_frames_per_segment": 345, "continuation": None},
    },
    "limits": _LIMITS,
}


@pytest.fixture
def storage_dir(tmp_path):
    (tmp_path / "image.png").write_bytes(b"fake-image")
    (tmp_path / "audio.wav").write_bytes(b"fake-audio")
    return tmp_path


def _segment(seg_id, **overrides):
    seg = {"id": seg_id, "prompt": "a cat", "negative_prompt": "", "frames": 5}
    seg.update(overrides)
    return seg


def _chain_doc(segments, media=None, audio=None, seed=1000):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 1, "duration": None, "resolution": "", "seed": seed},
        "segments": segments,
        "media": media or [],
        "audio": audio or [],
        "ic_lora": [],
    }


@pytest.fixture
def five_segment_doc(storage_dir):
    """seg-0 t2v, seg-1/seg-3/seg-4 derived 'chain', seg-2 an explicit fresh
    cut ('t2v') mid-film -- so [seg-2, seg-3] is a valid span to select (starts
    at a fresh cut) while [seg-3] alone is not (its predecessor, seg-2, is
    outside a single-shot span). seg-3 carries an explicit seed override;
    every other segment's seed must be baked from the document's base seed.
    """
    keyframe_media = [
        # Lands inside seg-0's window ([0, 5)) -- outside any span this
        # module's tests select, so it must never survive compilation.
        {"id": "kf-0", "role": "keyframe", "segment_id": None, "at": 2.0, "strength": 1.0,
         "media": {"relative_path": "image.png"}},
        # Lands inside seg-2's window ([10, 15)).
        {"id": "kf-2", "role": "keyframe", "segment_id": None, "at": 12.0, "strength": 0.5,
         "media": {"relative_path": "image.png"}},
        # Lands inside seg-3's window ([15, 20)).
        {"id": "kf-3", "role": "keyframe", "segment_id": None, "at": 17.0, "strength": 1.0,
         "media": {"relative_path": "image.png"}},
    ]
    audio = [
        # Straddles the seg-2/seg-3 span boundary at t=10: starts 3s before
        # the span, ends 4s into it.
        {"id": "a-straddle", "role": "mux", "start": 7.0, "trim_start": 1.0, "length": 7.0,
         "media": {"relative_path": "audio.wav"}},
        # Entirely inside the span ([10, 20)).
        {"id": "a-inside", "role": "mux", "start": 11.0, "trim_start": 0.0, "length": 2.0,
         "media": {"relative_path": "audio.wav"}},
        # Entirely outside the span (ends before it starts).
        {"id": "a-outside", "role": "mux", "start": 0.0, "trim_start": 0.0, "length": 3.0,
         "media": {"relative_path": "audio.wav"}},
    ]
    raw = _chain_doc(
        segments=[
            _segment("seg-0"),
            _segment("seg-1"),
            _segment("seg-2", sub_type="t2v"),
            _segment("seg-3", seed=42),
            _segment("seg-4"),
        ],
        media=keyframe_media,
        audio=audio,
    )
    return normalize_video_director(raw, CHAIN_CAPS, str(storage_dir))


class TestSpanValidation:
    def test_unknown_shot_id_raises(self, five_segment_doc):
        with pytest.raises(VideoDirectorValidationError, match="unknown segment id"):
            compile_shot_plan(five_segment_doc, ["seg-99"])

    def test_empty_shot_ids_raises(self, five_segment_doc):
        with pytest.raises(VideoDirectorValidationError, match="at least one shot id"):
            compile_shot_plan(five_segment_doc, [])

    def test_non_contiguous_selection_is_rejected_never_silently_filtered(self, five_segment_doc):
        """Bite check: selecting seg-0 and seg-2 (skipping seg-1) must be
        rejected outright, never silently collapsed to render just those two
        segments back-to-back -- that would render a film that never
        existed."""
        with pytest.raises(VideoDirectorValidationError, match="contiguous"):
            compile_shot_plan(five_segment_doc, ["seg-0", "seg-2"])

    def test_shot_ids_order_does_not_matter_output_is_always_film_order(self, five_segment_doc):
        """Row checkboxes only SCOPE what renders; the compiled span always
        runs in the film's own segment order regardless of the order
        `shot_ids` happened to arrive in (continuation depends on it)."""
        compiled = compile_shot_plan(five_segment_doc, ["seg-3", "seg-2"])
        assert [s["id"] for s in compiled["segments"]] == ["seg-2", "seg-3"]

    def test_span_must_start_at_a_fresh_cut(self, five_segment_doc):
        """seg-3 resolves to 'chain' -- it continues seg-2's tail frames, but
        seg-2 is outside a [seg-3] single-shot span."""
        with pytest.raises(VideoDirectorValidationError, match="needs its previous shot"):
            compile_shot_plan(five_segment_doc, ["seg-3"])

    def test_multi_shot_span_may_start_at_a_fresh_cut_and_continue_within_it(self, five_segment_doc):
        """[seg-2, seg-3] starts at seg-2's fresh cut; seg-3 continuing seg-2
        WITHIN the span is fine -- only the span's own first segment is
        checked."""
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        assert [s["id"] for s in compiled["segments"]] == ["seg-2", "seg-3"]


class TestBakedSegmentValues:
    def test_seed_matches_the_full_plan_value_for_the_same_original_index(self, five_segment_doc):
        """seg-2 sits at original index 2 with no explicit seed -- the full
        plan (and chain_video_wan22/windows.py) would derive `base_seed + 2`
        regardless of how many segments actually execute."""
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        base_seed = five_segment_doc["settings"]["seed"]
        seg2, seg3 = compiled["segments"]
        assert seg2["seed"] == base_seed + 2
        # seg-3's explicit seed override survives untouched, not re-derived
        # from its position in this (shorter) span.
        assert seg3["seed"] == 42

    def test_sub_type_carried_through_unchanged(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        seg2, seg3 = compiled["segments"]
        assert seg2["sub_type"] == "t2v"
        assert seg3["sub_type"] == "chain"

    def test_sequence_index_is_the_original_film_position_not_the_span_position(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        seg2, seg3 = compiled["segments"]
        assert seg2["sequence_index"] == 2
        assert seg3["sequence_index"] == 3

    def test_single_shot_span_gets_shot_provenance(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2"])
        assert compiled["shot"] == {"id": "seg-2", "index": 2, "count": 5}

    def test_multi_shot_span_carries_no_shot_provenance(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        assert "shot" not in compiled

    def test_render_key_is_stripped(self, five_segment_doc):
        doc = dict(five_segment_doc)
        doc["render"] = {"scope": "shots", "shot_ids": ["seg-2"]}
        compiled = compile_shot_plan(doc, ["seg-2"])
        assert "render" not in compiled


class TestMediaAndAudioOffsets:
    def test_keyframes_rebased_to_the_span_and_out_of_span_ones_dropped(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        keyframes = {m["id"]: m for m in compiled["media"] if m["role"] == "keyframe"}
        # kf-0 (at t=2, inside seg-0) is outside the [10, 20) span.
        assert "kf-0" not in keyframes
        # kf-2 (at t=12) rebases to 2s into the span.
        assert keyframes["kf-2"]["at"] == pytest.approx(2.0)
        # kf-3 (at t=17) rebases to 7s into the span.
        assert keyframes["kf-3"]["at"] == pytest.approx(7.0)

    def test_audio_clipped_and_rebased_to_the_span(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        audio = {a["id"]: a for a in compiled["audio"]}
        assert "a-outside" not in audio

        straddle = audio["a-straddle"]
        # Started 3s before the span (t=7 vs span start t=10): the visible
        # slice starts at the span's own t=0, having already skipped an
        # extra 3s of source on top of its original 1s trim_start.
        assert straddle["start"] == pytest.approx(0.0)
        assert straddle["trim_start"] == pytest.approx(1.0 + 3.0)
        # Original clip ran t=[7, 14); clipped to the span's [10, 14) is 4s.
        assert straddle["length"] == pytest.approx(4.0)

        inside = audio["a-inside"]
        assert inside["start"] == pytest.approx(1.0)  # t=11 - span start t=10
        assert inside["trim_start"] == pytest.approx(0.0)
        assert inside["length"] == pytest.approx(2.0)

    def test_settings_duration_is_the_span_duration(self, five_segment_doc):
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        assert compiled["settings"]["duration"] == pytest.approx(10.0)  # two 5s segments


class TestModelSetFlags:
    def test_needs_flags_reflect_only_the_selected_span(self, five_segment_doc):
        """seg-2 is 't2v' (needs the t2v set), seg-3 is 'chain' (needs the
        i2v set) -- both flags true for this span, independent of what the
        rest of the film (seg-0/1/4) would have needed."""
        compiled = compile_shot_plan(five_segment_doc, ["seg-2", "seg-3"])
        assert compiled["needs_t2v_set"] is True
        assert compiled["needs_i2v_set"] is True

    def test_a_span_opening_on_i2v_and_continuing_needs_only_the_i2v_set(self, storage_dir):
        """A span whose fresh-cut opener is itself i2v-consuming (not t2v)
        needs only the i2v set, even though it still passes the 'starts at a
        fresh cut' check (only 'chain' is rejected as an opener)."""
        raw = _chain_doc(segments=[
            _segment("seg-a", sub_type="i2v"),
            _segment("seg-b"),  # derives to 'chain', continuing seg-a
        ])
        doc = normalize_video_director(raw, CHAIN_CAPS, str(storage_dir))
        compiled = compile_shot_plan(doc, ["seg-a", "seg-b"])
        assert compiled["needs_t2v_set"] is False
        assert compiled["needs_i2v_set"] is True


class TestReferenceIndices:
    @pytest.fixture
    def refs_doc(self, storage_dir):
        form_data = {
            "references": [
                {"relative_path": "image.png", "path": str(storage_dir / "image.png")},
                {"relative_path": "audio.wav", "path": str(storage_dir / "audio.wav")},
            ],
        }
        raw = _chain_doc(
            segments=[
                _segment("seg-0"),
                # A non-empty proper subset -- must survive compilation
                # exactly, never collapsing back to "every reference".
                _segment("seg-1", references=[{"path": str(storage_dir / "audio.wav")}]),
                # references omitted entirely -- "every reference" (None).
                _segment("seg-2", sub_type="t2v"),
            ],
        )
        return normalize_video_director(raw, REFS_CAPS, str(storage_dir), form_data)

    def test_a_subset_never_collapses_to_none(self, refs_doc):
        compiled = compile_shot_plan(refs_doc, ["seg-1"])
        assert compiled["segments"][0]["reference_indices"] == [1]

    def test_none_stays_none(self, refs_doc):
        compiled = compile_shot_plan(refs_doc, ["seg-2"])
        assert compiled["segments"][0]["reference_indices"] is None
