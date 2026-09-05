"""Tests for the Wan Video Director chain's frame/overlap geometry: the
causal VAE's `1 + 4k` lattice snap and the continuation context-trim
(`motion_latent_count` vs the document's own `settings.continuation`). Pure
planning -- no torch modules, no weights, CPU-only.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes.generator.chain_video_wan22.geometry import (
    WanDirectorPlanError,
    resolve_continuation,
    resolve_motion_latent_count,
    resolve_window_geometry,
    tail_frame_count,
)


def _segment(index, *, frames=81, sub_type=None):
    return {
        "id": f"seg-{index}",
        "prompt": f"shot {index}",
        "negative_prompt": "",
        "frames": frames,
        "sub_type": sub_type if sub_type is not None else ("t2v" if index == 0 else "chain"),
    }


def _settings(*, continuation=None, motion_latent_count=None):
    settings = {"fps": 16, "seed": 7}
    if continuation is not None:
        settings["continuation"] = continuation
    if motion_latent_count is not None:
        settings["timing_profile"] = {"motion_latent_count": motion_latent_count}
    return settings


# -- resolve_continuation / resolve_motion_latent_count / tail_frame_count --


def test_no_continuation_block_defaults_the_overlap_to_four_and_stitches():
    assert resolve_continuation({}) == (4, True)


def test_an_explicit_overlap_frames_is_carried():
    assert resolve_continuation({"continuation": {"source": "tail_frames", "overlap_frames": 12, "stitch": False}}) == (12, False)


def test_last_frame_source_pins_the_overlap_to_one_regardless_of_overlap_frames():
    assert resolve_continuation({"continuation": {"source": "last_frame", "overlap_frames": 12}}) == (1, True)


def test_motion_latent_count_defaults_to_one_when_the_profile_is_absent():
    assert resolve_motion_latent_count({}) == 1


def test_motion_latent_count_is_read_from_the_timing_profile():
    assert resolve_motion_latent_count({"timing_profile": {"motion_latent_count": 3}}) == 3


@pytest.mark.parametrize(
    "default_overlap,motion_latent_count,expected",
    [
        (4, 1, 1),   # 1 latent slot ~= 1 pixel frame, well under the 4-frame overlap cap
        (4, 2, 4),   # 2 slots ~= 5 pixel frames, capped by the 4-frame overlap
        (4, 3, 4),   # 3 slots ~= 9 pixel frames, still capped
        (4, 4, 4),   # 4 slots ~= 13 pixel frames, still capped
        (1, 4, 1),   # last_frame source: capped at 1 regardless of motion latents
        (0, 2, 1),   # a zero-configured overlap floors the BASE to 1 pixel frame; the motion
                     # ceiling (5 frames) is wider than that floor, so it never binds here
    ],
)
def test_tail_frame_count_table(default_overlap, motion_latent_count, expected):
    assert tail_frame_count(default_overlap, motion_latent_count) == expected


# -- resolve_window_geometry: the reported-case numbers -----------------------
# Three 80-frame requests (all under Wan's 81-frame-per-segment ceiling):
# `align_num_frames`/`snap_frame_count` rounds 80 up to 81 (1 + 4*20). The
# middle segment resolves to "chain" (a prompt-only later segment, per
# derive_segment_sub_type) and its overlap is `tail_frame_count`.


def test_three_80_frame_segments_motion_2_matches_the_reported_81_77_81():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80), _segment(2, frames=80, sub_type="t2v")],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 4}, motion_latent_count=2),
    )
    assert [g.frames for g in geometry] == [81, 81, 81]
    assert [g.overlap_frames for g in geometry] == [0, 4, 0]
    assert [g.emitted_frames for g in geometry] == [81, 77, 81]


def test_three_80_frame_segments_motion_1_matches_the_reported_81_80_81():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80), _segment(2, frames=80, sub_type="t2v")],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 4}, motion_latent_count=1),
    )
    assert [g.frames for g in geometry] == [81, 81, 81]
    assert [g.overlap_frames for g in geometry] == [0, 1, 0]
    assert [g.emitted_frames for g in geometry] == [81, 80, 81]


def test_the_absent_config_fallback_motion_defaults_to_one():
    """No `timing_profile` on `settings` at all -- `resolve_motion_latent_count`
    falls back to 1, the SAME fallback `pipeline.yml`'s Jinja default applies
    when `svi_motion_latent_count` is missing from the bound form entirely."""
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80)],
        {"fps": 16, "continuation": {"source": "tail_frames", "overlap_frames": 4}},
    )
    assert [g.emitted_frames for g in geometry] == [81, 80]


# -- fresh cuts never carry overlap-in ---------------------------------------


def test_the_first_segment_never_continues_anything():
    geometry = resolve_window_geometry([_segment(0, frames=80)], _settings(motion_latent_count=2))
    assert geometry[0].is_continuation is False
    assert geometry[0].overlap_frames == 0
    assert geometry[0].emitted_frames == geometry[0].frames


@pytest.mark.parametrize("sub_type", ["t2v", "i2v", "flf"])
def test_a_segment_with_its_own_start_image_is_a_cut_not_a_continuation(sub_type):
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80, sub_type=sub_type), _segment(2, frames=80)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 4}, motion_latent_count=2),
    )
    assert geometry[1].is_continuation is False
    assert geometry[1].overlap_frames == 0
    assert geometry[1].emitted_frames == geometry[1].frames
    # ... and the segment after it goes back to continuing (from the cut).
    assert geometry[2].is_continuation is True
    assert geometry[2].overlap_frames == 4


# -- overlap zero / last_frame source -----------------------------------------


def test_no_continuation_block_still_trims_the_default_four_frame_overlap():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80)], _settings(motion_latent_count=2),
    )
    assert geometry[1].overlap_frames == 4


def test_an_explicit_zero_overlap_frames_setting_still_floors_to_one_pixel_frame():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 0}, motion_latent_count=2),
    )
    assert geometry[1].overlap_frames == 1


def test_last_frame_source_pins_the_overlap_to_one_frame_regardless_of_motion_latent_count():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80)],
        _settings(continuation={"source": "last_frame", "overlap_frames": 4}, motion_latent_count=4),
    )
    assert geometry[1].overlap_frames == 1


# -- motion 1-4 sweep ----------------------------------------------------------


@pytest.mark.parametrize("motion_latent_count,expected_overlap", [(1, 1), (2, 4), (3, 4), (4, 4)])
def test_motion_latent_count_sweep_against_a_four_frame_configured_overlap(motion_latent_count, expected_overlap):
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 4}, motion_latent_count=motion_latent_count),
    )
    assert geometry[1].overlap_frames == expected_overlap


# -- short windows: the overlap clamp never empties a segment -----------------


def test_a_short_continuation_window_clamps_its_overlap_to_leave_one_frame():
    # frames snaps to 5 (1 + 4*1); an 8-frame configured overlap would empty
    # (or invert) the segment, so it clamps to frames - 1 == 4.
    geometry = resolve_window_geometry(
        [_segment(0, frames=5), _segment(1, frames=5)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 8}, motion_latent_count=4),
    )
    assert geometry[1].frames == 5
    assert geometry[1].overlap_frames == 4
    assert geometry[1].emitted_frames == 1


def test_a_short_opener_clamps_the_next_continuations_overlap_to_its_own_frames():
    # A 1-frame t2v opener leaves only ONE real frame of tail, far less than
    # tail_count (12, from overlap_frames=12 / motion_latent_count=4) or the
    # continuation's own frames - 1 (12) would suggest on their own -- the
    # clamp must bind on the PREVIOUS segment's frames. Same fixture as
    # generator/chain_video_wan22 test_chain_video_short_tail.py's
    # short-opener case, so the two stay provably in sync.
    geometry = resolve_window_geometry(
        [_segment(0, frames=1, sub_type="t2v"), _segment(1, frames=13)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 12}, motion_latent_count=4),
    )
    assert geometry[0].frames == 1
    assert geometry[1].frames == 13
    assert geometry[1].overlap_frames == 1  # min(tail_count=12, prev.on_disk_frames=1, frames-1=12)
    assert geometry[1].emitted_frames == 12


def test_two_successive_short_continuations_clamp_sequentially_not_off_the_aligned_length():
    # frames [17, 17, 17], sub-types [t2v, chain, chain], overlap 12, motion 4
    # (tail_count=12): B's context is bounded by A's on-disk length (17, A is
    # a fresh cut) -> B trims 12 pre-decode -> on_disk_B=5. C's context must
    # be bounded by B's ACTUAL on-disk length (5), not B's aligned `frames`
    # (17) -- a planner that used the aligned length would wrongly predict
    # on_disk=[17, 5, 5] instead of the real [17, 5, 12].
    geometry = resolve_window_geometry(
        [_segment(0, frames=17, sub_type="t2v"), _segment(1, frames=17), _segment(2, frames=17)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 12}, motion_latent_count=4),
    )
    assert [g.frames for g in geometry] == [17, 17, 17]
    # Checked first (via fields that existed even before the sequential fix)
    # so a pre-fix run fails on the actual wrong prediction [17, 5, 5], not
    # merely an AttributeError on the newer fields checked below.
    assert [g.emitted_frames for g in geometry] == [17, 5, 12]
    assert [g.overlap_frames for g in geometry] == [0, 12, 5]
    assert [g.on_disk_frames for g in geometry] == [17, 5, 12]
    assert [g.context_trimmed for g in geometry] == [False, True, True]
    assert [g.join_frames for g in geometry] == [0, 0, 0]


def test_a_single_frame_window_clamps_to_zero_emitted_rather_than_going_negative():
    geometry = resolve_window_geometry(
        [_segment(0, frames=1), _segment(1, frames=1)],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 8}, motion_latent_count=4),
    )
    assert geometry[1].frames == 1
    assert geometry[1].overlap_frames == 0
    assert geometry[1].emitted_frames == 1


# -- arbitrary requests around the 1+4k lattice -------------------------------


@pytest.mark.parametrize("requested,expected_frames", [(1, 1), (2, 1), (3, 1), (4, 5), (5, 5), (78, 77), (79, 77), (82, 81), (130, 129)])
def test_frames_snap_to_the_vae_lattice_ties_round_down(requested, expected_frames):
    geometry = resolve_window_geometry([_segment(0, frames=requested, sub_type="t2v")], _settings())
    assert geometry[0].frames == expected_frames
    assert geometry[0].requested_frames == requested


def test_total_frames_counts_each_segments_emitted_contribution_once():
    geometry = resolve_window_geometry(
        [_segment(0, frames=80), _segment(1, frames=80), _segment(2, frames=80, sub_type="t2v")],
        _settings(continuation={"source": "tail_frames", "overlap_frames": 4}, motion_latent_count=2),
    )
    assert sum(g.emitted_frames for g in geometry) == 81 + 77 + 81


def test_a_segment_without_frames_is_refused():
    with pytest.raises(WanDirectorPlanError):
        resolve_window_geometry([{"id": "seg-0", "sub_type": "t2v"}], _settings())
