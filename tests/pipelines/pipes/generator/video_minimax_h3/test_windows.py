"""Tests for the MiniMax-H3 Video Director window plan: the pixel<->latent
overlap arithmetic, how a document's segments become windows, and where a
Director image lands once the timeline is cut. Pure planning -- no torch
modules, no weights, CPU-only."""

from __future__ import annotations

import pytest

from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    LATENT_FRAME_PIXEL_SPANS,
    align_num_frames,
    head_frames_for_latents,
    latent_index_for_frame,
    tail_latents_for_frames,
    video_latent_num_frames,
)
from src.pipelines.pipes.generator.video_minimax_h3.windows import (
    DirectorPlanError,
    build_director_plan,
    resolve_mux_audio,
)

# One window's worth of frames, on the 17n+5 lattice and inside the released
# 5-15 s range: 124 frames = 5.17 s, 260 frames = 10.83 s.
SHORT = 124
LONG = 260


# -- pixel <-> latent overlap arithmetic --------------------------------------

@pytest.mark.parametrize("num_latents,expected_frames", [
    (0, 0), (1, 1), (2, 5), (3, 9), (4, 13), (5, 17), (6, 18), (7, 22), (8, 26), (9, 30), (10, 34), (11, 35),
])
def test_head_frames_for_latents_table(num_latents, expected_frames):
    """Frames the FIRST n latents cover -- what a continuation window trims."""
    assert head_frames_for_latents(num_latents) == expected_frames


@pytest.mark.parametrize("frames,expected_latents", [
    (1, 1), (4, 1), (5, 2), (9, 3), (13, 4), (17, 5), (18, 6), (21, 6), (22, 7), (34, 10), (35, 11),
])
def test_tail_latents_for_frames_table(frames, expected_latents):
    """Latents the LAST n frames need -- what a continuation window splices.

    The tail walk starts at a different phase of the (1,4,4,4,4) cycle than
    the head walk, so a single latent covers 4 frames here where it covers 1
    at the head.
    """
    assert tail_latents_for_frames(frames) == expected_latents


@pytest.mark.parametrize("chunks", [1, 2, 3])
def test_the_two_walks_agree_exactly_on_whole_chunks(chunks):
    """17 pixel frames <-> 5 latent frames is the one place head and tail
    arithmetic coincide, which is why the preset offers 17 and 34."""
    frames = 17 * chunks
    latents = 5 * chunks
    assert tail_latents_for_frames(frames) == latents
    assert head_frames_for_latents(latents) == frames


def test_the_two_walks_disagree_off_a_chunk_boundary():
    # Guards the table above from being "simplified" into one shared walk.
    assert head_frames_for_latents(1) == 1 and tail_latents_for_frames(1) == 1
    assert head_frames_for_latents(6) == 18 and tail_latents_for_frames(18) == 6
    assert head_frames_for_latents(tail_latents_for_frames(21)) == 18  # not 21


@pytest.mark.parametrize("n", range(8))
def test_head_walk_over_a_whole_clip_returns_its_frame_count(n):
    frames = 17 * n + 5
    assert head_frames_for_latents(video_latent_num_frames(frames)) == frames


@pytest.mark.parametrize("frame,expected", [
    (0, 0), (1, 1), (4, 1), (5, 2), (8, 2), (9, 3), (13, 4), (16, 4), (17, 5), (18, 6),
])
def test_latent_index_for_frame_table(frame, expected):
    assert latent_index_for_frame(frame) == expected


def test_the_span_cycle_is_the_vae_chunk_shape():
    assert sum(LATENT_FRAME_PIXEL_SPANS) == 17
    assert len(LATENT_FRAME_PIXEL_SPANS) == 5


# -- document -> windows -------------------------------------------------------

def _document(segments, *, continuation=("tail_frames", 17, True), seed=1000, media=(), audio=()):
    settings = {"fps": 24, "seed": seed, "resolution": "", "duration": None}
    settings["continuation"] = (
        None if continuation is None
        else {"source": continuation[0], "overlap_frames": continuation[1], "stitch": continuation[2]}
    )
    return {
        "schema_version": 1, "mode": "director", "settings": settings,
        "segments": list(segments), "media": list(media), "audio": list(audio),
        "ic_lora": [], "media_images": [], "media_videos": [], "media_placements": [],
    }


def _segment(index, *, frames=SHORT, sub_type=None, prompt=None, seed=None, steps=None, reference_indices=None):
    segment = {
        "id": f"seg-{index}", "prompt": prompt if prompt is not None else f"shot {index}",
        "negative_prompt": "", "frames": frames, "seed": seed, "steps": steps, "cfg": None,
        "loras": None, "start": None, "end": None,
        "sub_type": sub_type if sub_type is not None else ("t2v" if index == 0 else "chain"),
    }
    if reference_indices is not None:
        segment["reference_indices"] = reference_indices
    return segment


@pytest.mark.parametrize("document", [None, {}, {"mode": "t2v", "segments": [_segment(0)]}])
def test_no_plan_for_anything_that_is_not_a_director_document(document):
    """The guard that keeps every ordinary request on the single-window path."""
    assert build_director_plan(document, default_seed=7) is None


def test_one_window_per_segment():
    plan = build_director_plan(_document([_segment(0), _segment(1), _segment(2)]), default_seed=7)
    assert [w.index for w in plan.windows] == [0, 1, 2]
    assert [w.segment_id for w in plan.windows] == ["seg-0", "seg-1", "seg-2"]
    assert [w.prompt for w in plan.windows] == ["shot 0", "shot 1", "shot 2"]


def test_the_first_window_never_continues_anything():
    plan = build_director_plan(_document([_segment(0), _segment(1)]), default_seed=7)
    assert plan.windows[0].overlap_latents == 0
    assert plan.windows[0].overlap_frames == 0
    assert not plan.windows[0].continues_previous


def test_a_chain_segment_carries_the_documents_overlap():
    plan = build_director_plan(_document([_segment(0), _segment(1)]), default_seed=7)
    assert plan.windows[1].overlap_latents == 5     # 17 pixel frames, one VAE chunk
    assert plan.windows[1].overlap_frames == 17
    assert plan.windows[1].continues_previous


def test_a_segment_with_its_own_start_image_is_a_cut_not_a_continuation():
    plan = build_director_plan(
        _document([_segment(0), _segment(1, sub_type="i2v"), _segment(2)]), default_seed=7,
    )
    assert plan.windows[1].overlap_latents == 0
    assert plan.windows[1].emitted_frames == plan.windows[1].frames
    # ... and the shot after it goes back to continuing.
    assert plan.windows[2].overlap_latents == 5


def test_last_frame_continuation_pins_exactly_one_latent():
    plan = build_director_plan(
        _document([_segment(0), _segment(1)], continuation=("last_frame", 17, True)), default_seed=7,
    )
    assert plan.windows[1].overlap_latents == 1
    assert plan.windows[1].overlap_frames == 1


def test_a_document_without_a_continuation_block_runs_independent_cuts():
    plan = build_director_plan(_document([_segment(0), _segment(1)], continuation=None), default_seed=7)
    assert all(window.overlap_latents == 0 for window in plan.windows)


def test_frames_snap_up_to_the_vae_lattice_and_are_reported():
    plan = build_director_plan(_document([_segment(0, frames=130)]), default_seed=7)
    window = plan.windows[0]
    assert window.requested_frames == 130
    assert window.frames == align_num_frames(130) == 141
    assert window.num_latent_frames == video_latent_num_frames(141)


@pytest.mark.parametrize("frames", [1, 60, 100])
def test_a_window_shorter_than_the_released_range_is_refused(frames):
    with pytest.raises(DirectorPlanError, match="per window"):
        build_director_plan(_document([_segment(0, frames=frames)]), default_seed=7)


def test_a_window_just_under_the_minimum_is_accepted_once_it_snaps_up():
    """119 frames is 4.96s, below the 5s floor -- but it snaps to 124 (5.17s)
    before the range is checked, so it runs. The bound is on what executes."""
    plan = build_director_plan(_document([_segment(0, frames=119)]), default_seed=7)
    assert plan.windows[0].frames == 124


def test_a_window_longer_than_the_released_range_is_refused():
    with pytest.raises(DirectorPlanError, match="per window"):
        build_director_plan(_document([_segment(0, frames=400)]), default_seed=7)


def test_seeds_walk_the_documents_seed_and_a_pinned_segment_wins():
    plan = build_director_plan(
        _document([_segment(0), _segment(1), _segment(2, seed=999)], seed=1000), default_seed=7,
    )
    assert [w.seed for w in plan.windows] == [1000, 1001, 999]


def test_per_segment_steps_are_carried_and_default_to_none():
    plan = build_director_plan(_document([_segment(0, steps=8), _segment(1)]), default_seed=7)
    assert plan.windows[0].steps == 8
    assert plan.windows[1].steps is None


def test_stitch_flag_is_carried():
    assert build_director_plan(_document([_segment(0)]), default_seed=7).stitch is True
    plan = build_director_plan(
        _document([_segment(0), _segment(1)], continuation=("tail_frames", 17, False)), default_seed=7,
    )
    assert plan.stitch is False


def test_total_frames_counts_each_window_once():
    """The stitched length is the sum of what each window CONTRIBUTES -- the
    overlap is context, not footage."""
    plan = build_director_plan(_document([_segment(0), _segment(1), _segment(2)]), default_seed=7)
    assert [w.emitted_frames for w in plan.windows] == [SHORT, SHORT - 17, SHORT - 17]
    assert plan.total_frames == SHORT * 3 - 34


def test_a_segment_without_frames_is_refused():
    segment = _segment(0)
    segment["frames"] = None
    with pytest.raises(DirectorPlanError, match="explicit frame count"):
        build_director_plan(_document([segment]), default_seed=7)


# -- Director images -> window + latent index ----------------------------------

def _with_keyframe(document, at_frame, *, index=0):
    document["media"] = [{"role": "keyframe", "at": at_frame / 24, "segment_id": None,
                          "strength": 0.5, "media": {"path": "/k.png", "type": "image"}}]
    document["media_images"] = ["/k.png"]
    document["media_placements"] = [
        {"source": "image", "index": index, "frame": at_frame, "strength": 0.5, "role": "keyframe"},
    ]
    return document


def test_a_keyframe_inside_the_first_window_keeps_its_own_frame():
    plan = build_director_plan(
        _with_keyframe(_document([_segment(0), _segment(1)]), 40), default_seed=7,
    )
    (keyframe,) = plan.windows[0].keyframes
    assert plan.windows[1].keyframes == ()
    assert keyframe.frame == 40
    assert keyframe.latent_index == latent_index_for_frame(40)
    assert keyframe.image_index == 0
    assert keyframe.strength == 0.5


def test_a_keyframe_in_a_later_window_is_offset_by_the_trimmed_overlap():
    """Window 1 emits from timeline frame 124 on, but its own decoded output
    opens 17 frames earlier -- the context it replays. A timeline frame is
    therefore that much further into the window."""
    plan = build_director_plan(
        _with_keyframe(_document([_segment(0), _segment(1)]), SHORT + 10), default_seed=7,
    )
    assert plan.windows[0].keyframes == ()
    (keyframe,) = plan.windows[1].keyframes
    assert keyframe.frame == 10 + 17
    assert keyframe.latent_index == latent_index_for_frame(27)


def test_the_frame_that_opens_the_second_window_is_the_boundary():
    plan = build_director_plan(
        _with_keyframe(_document([_segment(0), _segment(1)]), SHORT), default_seed=7,
    )
    (keyframe,) = plan.windows[1].keyframes
    assert keyframe.frame == 17

    plan = build_director_plan(
        _with_keyframe(_document([_segment(0), _segment(1)]), SHORT - 1), default_seed=7,
    )
    (keyframe,) = plan.windows[0].keyframes
    assert keyframe.frame == SHORT - 1


def test_a_keyframe_past_the_end_clamps_to_the_last_frame():
    plan = build_director_plan(
        _with_keyframe(_document([_segment(0), _segment(1)]), 100_000), default_seed=7,
    )
    (keyframe,) = plan.windows[-1].keyframes
    assert keyframe.frame == plan.windows[-1].frames - 1
    assert keyframe.latent_index == plan.windows[-1].num_latent_frames - 1


def test_a_segments_own_first_image_anchors_that_windows_opening_frame():
    document = _document([_segment(0), _segment(1, sub_type="i2v")])
    document["media"] = [{"role": "first", "at": None, "segment_id": "seg-1", "strength": 1.0,
                          "media": {"path": "/a.png", "type": "image"}}]
    document["media_images"] = ["/a.png"]
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
    ]
    plan = build_director_plan(document, default_seed=7)
    assert plan.windows[0].keyframes == ()
    (keyframe,) = plan.windows[1].keyframes
    assert keyframe.frame == 0 and keyframe.latent_index == 0


def test_a_segments_own_last_image_anchors_that_windows_final_latent():
    document = _document([_segment(0, sub_type="flf")])
    document["media"] = [{"role": "last", "at": None, "segment_id": "seg-0", "strength": 1.0,
                          "media": {"path": "/z.png", "type": "image"}}]
    document["media_images"] = ["/z.png"]
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": "last", "strength": 1.0, "role": "keyframe"},
    ]
    plan = build_director_plan(document, default_seed=7)
    (keyframe,) = plan.windows[0].keyframes
    assert keyframe.latent_index == plan.windows[0].num_latent_frames - 1


def test_an_ic_lora_reference_placement_is_not_treated_as_a_keyframe():
    document = _document([_segment(0)])
    document["media_placements"] = [
        {"source": "video", "index": 0, "frame": "first", "strength": 1.0, "role": "reference"},
    ]
    assert build_director_plan(document, default_seed=7).windows[0].keyframes == ()


def test_placements_that_outnumber_their_media_entries_fail_loudly():
    document = _document([_segment(0)])
    document["media_placements"] = [
        {"source": "image", "index": 0, "frame": 10, "strength": 1.0, "role": "keyframe"},
    ]
    with pytest.raises(DirectorPlanError, match="disagree"):
        build_director_plan(document, default_seed=7)


# -- per-segment reference selection --------------------------------------------

def test_a_segment_with_no_reference_indices_leaves_the_window_field_none():
    plan = build_director_plan(_document([_segment(0)]), default_seed=7)
    assert plan.windows[0].reference_indices is None


def test_a_segments_reference_indices_are_parsed_into_a_tuple():
    plan = build_director_plan(_document([_segment(0, reference_indices=[0, 2])]), default_seed=7)
    assert plan.windows[0].reference_indices == (0, 2)


def test_a_non_list_reference_indices_is_refused():
    with pytest.raises(DirectorPlanError, match="reference_indices"):
        build_director_plan(_document([_segment(0, reference_indices=1)]), default_seed=7)


@pytest.mark.parametrize("bad", [["0"], [1.5], [True], [None]])
def test_reference_indices_entries_must_all_be_plain_ints(bad):
    with pytest.raises(DirectorPlanError, match="reference_indices"):
        build_director_plan(_document([_segment(0, reference_indices=bad)]), default_seed=7)


def test_bite_check_reference_indices_parsing_actually_validates_entries():
    # BITE CHECK: an all-int list, the shape the guard above is supposed to
    # accept, must not raise -- confirms the parametrized rejection above
    # is catching real bad entries, not every list.
    plan = build_director_plan(_document([_segment(0, reference_indices=[1, 2, 3])]), default_seed=7)
    assert plan.windows[0].reference_indices == (1, 2, 3)


# -- audio roles ----------------------------------------------------------------

def test_a_mux_audio_clip_is_carried_to_the_stitched_output():
    document = _document([_segment(0)], audio=[
        {"id": None, "role": "mux", "start": 0.0, "trim_start": 0.0, "length": 5.0,
         "media": {"path": "/track.wav", "type": "audio"}},
    ])
    assert resolve_mux_audio(document) == "/track.wav"
    assert build_director_plan(document, default_seed=7).mux_audio_path == "/track.wav"


def test_a_condition_soundtrack_is_refused_rather_than_silently_muxed():
    document = _document([_segment(0)], audio=[
        {"id": None, "role": "condition", "start": 0.0, "trim_start": 0.0, "length": 5.0,
         "media": {"path": "/track.wav", "type": "audio"}},
    ])
    with pytest.raises(DirectorPlanError, match="not yet supported"):
        build_director_plan(document, default_seed=7)


def test_no_audio_entries_means_no_mux():
    assert build_director_plan(_document([_segment(0)]), default_seed=7).mux_audio_path is None
