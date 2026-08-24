"""Table-pinned tests for the DFR canvas/tile/stitch integer math.

Every expected value here is transcribed from ``ai/DFR_FACTS_SPEC.md``'s
numbered sections (cited per test) rather than derived from the
implementation. The layout is pure integer arithmetic, so these run without
weights, without a GPU and before any of the pipe work -- which is the point:
the spec's own risk R1 ("the seam handover is off by exactly one latent
frame") produces output that still decodes, so the tables are the only oracle
that can catch it cheaply.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes._shared.generation.dfr_layout import (
    DFR_SEGMENT_CANDIDATES,
    TILE_LEAD_SEGMENTS,
    default_tile_count,
    double_positions,
    dedupe_slots,
    merge_keyframe_bag,
    plan_canvas,
    plan_round,
    reanchor_positions,
    resolve_tile_count,
    round_output_frames,
    tile_local_placements,
)


# -- constants (spec §1.14) ---------------------------------------------------

def test_constants_match_spec_table():
    # §1.14: segment length candidates {24, 32}; tile lead-in 1 segment.
    assert DFR_SEGMENT_CANDIDATES == (24, 32)
    assert TILE_LEAD_SEGMENTS == 1


# -- canvas grid (spec §1.4) --------------------------------------------------

def test_canvas_121_frames_worked_example():
    # §1.4 worked example: num_frames = 121 -> content = 120; padding is 0 for
    # S=24 and 8 for S=32, so S = 24; canvas = 121; slots [24, 48, 72, 96, 120].
    canvas = plan_canvas(121)
    assert canvas.segment_length == 24
    assert canvas.content_padded == 120
    assert canvas.canvas_frames == 121
    assert canvas.slot_positions == (24, 48, 72, 96, 120)


def test_canvas_89_frames_tie_keeps_the_larger_candidate():
    # §1.4 worked example: num_frames = 89 -> content = 88; padding is 8 for
    # BOTH candidates, so the tie rule picks S = 32; canvas pads to 97 frames;
    # slots [32, 64, 96]. The caller still receives 89 frames (§1.11).
    canvas = plan_canvas(89)
    assert canvas.segment_length == 32
    assert canvas.content_padded == 96
    assert canvas.canvas_frames == 97
    assert canvas.slot_positions == (32, 64, 96)


def test_canvas_excludes_frame_zero_and_includes_the_terminal():
    # §1.4 step 4: slots are every segment boundary EXCEPT frame 0, and the
    # terminal frame IS included (the tiling code refuses a seam list whose
    # last entry is not the terminal).
    canvas = plan_canvas(121)
    assert 0 not in canvas.slot_positions
    assert canvas.slot_positions[-1] == canvas.content_padded


def test_canvas_rejects_off_grid_frame_counts():
    # §1.3: the requested frame count must satisfy (num_frames - 1) % 8 == 0.
    with pytest.raises(ValueError, match="1 \\+ 8k"):
        plan_canvas(100)


# -- output framing (spec §1.11) ----------------------------------------------

@pytest.mark.parametrize("frames,rounds,expected", [
    (121, 0, 121),
    (121, 1, 241),
    (121, 2, 481),
    (89, 1, 177),
])
def test_round_output_frames_contract(frames, rounds, expected):
    # §1.11: the contract to the caller is exactly
    # (requested_frames - 1) * 2**rounds + 1 frames.
    assert round_output_frames(frames, rounds) == expected


# -- carry-forward bookkeeping (spec §1.5.3) ----------------------------------

def test_positions_double_between_rounds():
    # §1.5.3: at the start of round r, positions are DOUBLED (the round doubles
    # the timeline) while the latents themselves are unchanged.
    assert double_positions((24, 48, 72, 96, 120)) == (48, 96, 144, 192, 240)


def test_intra_round_slot_dedup_keeps_the_earlier_tile():
    # §1.5.3 worked example, num_frames = 121, 1 round: tile 0 slots
    # 24, 72, 120; tile 1 slots 120, 168, 216; after dedup (tile 0 wins 120)
    # the surviving set is 24, 72, 120, 168, 216, re-sorted by position.
    tile0 = [(24, "t0@24"), (72, "t0@72"), (120, "t0@120")]
    tile1 = [(120, "t1@120"), (168, "t1@168"), (216, "t1@216")]
    survivors = dedupe_slots([tile0, tile1])
    assert [p for p, _ in survivors] == [24, 72, 120, 168, 216]
    # The tie-break: EARLIER tile wins the collision at 120.
    assert dict(survivors)[120] == "t0@120"


def test_anchor_slot_merge_lets_the_slot_win():
    # §1.5.3: slots are merged AFTER anchors, so on a positional collision the
    # SLOT value overwrites the anchor value -- the opposite tie-break to the
    # intra-round dedup above (the spec flags these as two different rules
    # twenty lines apart).
    anchors = [(48, "anchor@48"), (96, "anchor@96")]
    slots = [(48, "slot@48"), (72, "slot@72")]
    merged = merge_keyframe_bag(anchors, slots)
    assert [p for p, _ in merged] == [48, 72, 96]
    assert dict(merged)[48] == "slot@48"


def test_merged_bag_is_sorted_ascending():
    # §1.5.3: the result is emitted sorted by position ascending, with latents
    # concatenated in that order.
    merged = merge_keyframe_bag([(240, "a"), (48, "b")], [(144, "c")])
    assert [p for p, _ in merged] == [48, 144, 240]


def test_empty_bag_is_an_error():
    # §1.5.3: an empty bag is an error.
    with pytest.raises(ValueError, match="empty"):
        merge_keyframe_bag([], [])


def test_carry_bag_into_round_two_matches_the_worked_example():
    # §1.5.3 worked example, num_frames = 121, 1 round: the carry bag into
    # round 2 is 24, 48, 72, 96, 120, 144, 168, 192, 216, 240 -- the union of
    # the DOUBLED anchor positions and the deduped slot positions.
    anchors = [(p, f"a{p}") for p in double_positions(plan_canvas(121).slot_positions)]
    slots = dedupe_slots([
        [(24, "s24"), (72, "s72"), (120, "s120")],
        [(120, "s120b"), (168, "s168"), (216, "s216")],
    ])
    merged = merge_keyframe_bag(anchors, slots)
    assert [p for p, _ in merged] == [24, 48, 72, 96, 120, 144, 168, 192, 216, 240]


# -- tile windows: the §1.6 worked tables -------------------------------------

# §1.6 worked tile table, num_frames = 121, Round 1 (241 frames, seams
# [48, 96, 144, 192, 240], 2 tiles, ownership [3, 2]):
#
# | tile | pixel window | latent range | local frames | drop | anchors | slots |
# | 0    | 0…144        | [0, 19)      | 145          | 0    | 48, 96, 144        | 24, 72, 120   |
# | 1    | 96…240       | [12, 31)     | 145          | 7    | 96, 144, 192, 240  | 120, 168, 216 |
#
# Stitched temporal extent 19 + (19 - 7) = 31 == (241 - 1)//8 + 1.
_ROUND_1_TABLE = [
    # (index, pixel_start, pixel_end, latent_start, latent_end, local_frames,
    #  drop, anchors, slots)
    (0, 0, 144, 0, 19, 145, 0, (48, 96, 144), (24, 72, 120)),
    (1, 96, 240, 12, 31, 145, 7, (96, 144, 192, 240), (120, 168, 216)),
]

# §1.6 worked tile table, Round 2 (481 frames, 10 seams, 4 tiles, ownership
# [3, 3, 2, 2]) -- note tile 1 is LARGER than the others (193 local frames):
# the remainder distribution gives early tiles the extra segment, and that
# tile is the memory high-water mark of the whole pipeline.
_ROUND_2_TABLE = [
    (0, 0, 144, 0, 19, 145, 0, (48, 96, 144), (24, 72, 120)),
    (1, 96, 288, 12, 37, 193, 7, (96, 144, 192, 240, 288), (120, 168, 216, 264)),
    (2, 240, 384, 30, 49, 145, 7, (240, 288, 336, 384), (264, 312, 360)),
    (3, 336, 480, 42, 61, 145, 7, (336, 384, 432, 480), (360, 408, 456)),
]

_ROUND_1_SEAMS = (48, 96, 144, 192, 240)
_ROUND_2_SEAMS = (48, 96, 144, 192, 240, 288, 336, 384, 432, 480)


def _assert_table(layout, table):
    assert len(layout.tiles) == len(table)
    for tile, row in zip(layout.tiles, table):
        (index, pixel_start, pixel_end, latent_start, latent_end,
         local_frames, drop, anchors, slots) = row
        assert tile.index == index
        assert (tile.pixel_start, tile.pixel_end) == (pixel_start, pixel_end)
        assert (tile.latent_start, tile.latent_end) == (latent_start, latent_end)
        assert tile.local_frames == local_frames
        assert tile.drop_latent_prefix == drop
        assert tile.anchors == anchors
        assert tile.slots == slots


def test_round_one_tile_table():
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    _assert_table(layout, _ROUND_1_TABLE)


def test_round_one_stitched_extent():
    # §1.6: stitched temporal extent 19 + (19 - 7) = 31, which equals
    # (241 - 1)//8 + 1. The pipeline asserts exactly this (§1.6 / R1).
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    assert [t.kept_latents for t in layout.tiles] == [19, 12]
    assert layout.stitched_latents == 31
    assert layout.stitched_latents == (241 - 1) // 8 + 1


def test_round_two_tile_table():
    layout = plan_round(_ROUND_2_SEAMS, frames=481, num_tiles=4)
    _assert_table(layout, _ROUND_2_TABLE)


def test_round_two_stitched_extent():
    # §1.6: stitched extent 61 == (481 - 1)//8 + 1.
    layout = plan_round(_ROUND_2_SEAMS, frames=481, num_tiles=4)
    assert layout.stitched_latents == 61
    assert layout.stitched_latents == (481 - 1) // 8 + 1


def test_round_two_ownership_gives_the_remainder_to_the_earliest_tiles():
    # §1.6: n_segments split by divmod with the remainder distributed to the
    # earliest tiles (largest runs first) -- 10 segments over 4 tiles is
    # [3, 3, 2, 2], which is what makes tile 1 the 193-frame high-water mark.
    layout = plan_round(_ROUND_2_SEAMS, frames=481, num_tiles=4)
    assert [t.owned_segments for t in layout.tiles] == [3, 3, 2, 2]
    assert max(t.local_frames for t in layout.tiles) == 193


# -- the lead-in and its plus-one (spec §1.6, risk R1) ------------------------

def test_first_tile_has_no_lead_in_and_drops_nothing():
    # §1.6: the first tile gets lead 0, and its drop prefix is
    # latent_index(boundaries[own_lo]) - latent_start == 0 with no +1.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    first = layout.tiles[0]
    assert first.lead_segments == 0
    assert first.pixel_start == 0
    assert first.drop_latent_prefix == 0


def test_non_first_tile_drops_the_lead_in_plus_exactly_one():
    # §1.6: drop_latent_prefix = latent_index(boundaries[own_lo]) -
    # latent_start, PLUS 1 more for every tile after the first. The extra
    # latent is the seam latent itself -- the PREVIOUS tile keeps the 8-frame
    # latent token that ends on the keyframe mark, and this tile resumes
    # strictly after it.
    #
    # This is risk R1: too few dropped latents duplicates an 8-frame span at
    # every seam (a stutter), too many drops one (a skip), and both still
    # decode. Round 1 tile 1: lead-in is one 48-frame segment = 6 latents, so
    # the recorded drop of 7 is exactly lead-in + 1.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    tile = layout.tiles[1]
    lead_in_latents = (tile.own_pixel_start - tile.pixel_start) // 8
    assert lead_in_latents == 6
    assert tile.drop_latent_prefix == lead_in_latents + 1 == 7


def test_every_non_first_tile_in_round_two_drops_lead_in_plus_one():
    layout = plan_round(_ROUND_2_SEAMS, frames=481, num_tiles=4)
    assert [t.drop_latent_prefix for t in layout.tiles] == [0, 7, 7, 7]
    for tile in layout.tiles[1:]:
        lead_in_latents = (tile.own_pixel_start - tile.pixel_start) // 8
        assert tile.drop_latent_prefix == lead_in_latents + 1


def test_lead_in_window_still_opens_on_a_keyframe_anchor():
    # §1.6: one segment of lead-in puts the tile's local latent 0 (a
    # one-pixel-frame image latent) and local latent 1 (denoised against it)
    # inside the discarded prefix while STILL starting the window on a
    # keyframe anchor, which the frame-0 keyframe lock requires.
    layout = plan_round(_ROUND_2_SEAMS, frames=481, num_tiles=4)
    for tile in layout.tiles[1:]:
        assert tile.pixel_start in _ROUND_2_SEAMS
        assert tile.anchors[0] == tile.pixel_start


def test_anchors_exclude_frame_zero():
    # §1.6: every boundary from window_lo through own_hi inclusive, with
    # position 0 excluded -- frame 0 is not a keyframe, so the first tile's
    # window start contributes no anchor.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    assert 0 not in layout.tiles[0].anchors


def test_slots_include_the_lead_in_segment_midpoint():
    # §1.6: the midpoints of the LEAD-IN segment are included, which is
    # exactly why the intra-round slot dedup of §1.5.3 exists -- tile 1's
    # first slot (120) is also tile 0's last.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    assert layout.tiles[1].slots[0] == layout.tiles[0].slots[-1] == 120


# -- window validation (spec §1.6) --------------------------------------------

def test_seams_must_be_strictly_increasing():
    with pytest.raises(ValueError, match="strictly increasing"):
        plan_round((48, 48, 96), frames=97, num_tiles=2)


def test_spans_must_be_a_multiple_of_eight():
    with pytest.raises(ValueError, match="multiple of 8"):
        plan_round((20, 96), frames=97, num_tiles=1)


def test_spans_must_be_at_least_two_latent_frames():
    # §1.6: every span must be at least 2 latent frames (16 pixel frames) --
    # a shorter segment cannot carry a tile lead-in.
    with pytest.raises(ValueError, match="16 pixel frames"):
        plan_round((8, 48), frames=49, num_tiles=2)


def test_last_seam_must_be_the_terminal_frame():
    # §1.4/§1.6: the tiling refuses a seam list whose last entry is not the
    # terminal frame.
    with pytest.raises(ValueError, match="terminal"):
        plan_round((48, 96), frames=241, num_tiles=2)


def test_stitched_extent_is_validated_against_the_frame_count():
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    assert layout.stitched_latents == layout.expected_latents


# -- tile counts: the default, the relief valve, the auto-split ---------------

def test_default_tile_count_is_two_to_the_round():
    # §1.14: tiles per round = 2**round.
    assert [default_tile_count(r) for r in (0, 1, 2)] == [1, 2, 4]


def test_tile_count_is_clamped_to_the_segment_count():
    # §1.14: 2**round, CLAMPED to the segment count. Two seams cannot make
    # four tiles.
    layout = plan_round((48, 96), frames=97, num_tiles=4)
    assert len(layout.tiles) == 2


def test_num_tiles_beyond_two_to_the_round_keeps_the_seam_rules():
    # The design's finer-split relief valve: a larger tile count leaves the
    # seam/lead-in/drop rules unchanged -- tiles just own fewer segments each.
    # Round 1's 5 segments over 4 tiles is ownership [2, 1, 1, 1].
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=4)
    assert [t.owned_segments for t in layout.tiles] == [2, 1, 1, 1]
    assert [t.drop_latent_prefix for t in layout.tiles] == [0, 7, 7, 7]
    for tile in layout.tiles[1:]:
        lead_in_latents = (tile.own_pixel_start - tile.pixel_start) // 8
        assert tile.drop_latent_prefix == lead_in_latents + 1
    # The stitch contract is unchanged by the finer split.
    assert layout.stitched_latents == (241 - 1) // 8 + 1


def test_finer_split_never_goes_below_one_owned_segment():
    # The 1-owned-segment floor: 5 segments can make at most 5 tiles.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=99)
    assert len(layout.tiles) == 5
    assert all(t.owned_segments == 1 for t in layout.tiles)
    assert layout.stitched_latents == (241 - 1) // 8 + 1


def test_resolve_tile_count_prefers_the_explicit_override():
    assert resolve_tile_count(_ROUND_1_SEAMS, round_index=1, override=3) == 3


def test_resolve_tile_count_auto_splits_over_the_token_budget():
    # §8's VRAM valve: at 2**round the round-1 tiles are 19 latent frames +
    # 3/4 anchors; with 1000 tokens per latent frame the worst tile projects
    # 23000 tokens. A 20000-token budget forces a finer split.
    assert resolve_tile_count(
        _ROUND_1_SEAMS, round_index=1,
        tokens_per_latent_frame=1000, max_tile_tokens=100_000) == 2
    finer = resolve_tile_count(
        _ROUND_1_SEAMS, round_index=1,
        tokens_per_latent_frame=1000, max_tile_tokens=20_000)
    assert finer > 2
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=finer)
    assert all(t.projected_tokens(1000) <= 20_000 for t in layout.tiles)


def test_resolve_tile_count_stops_at_the_segment_floor():
    # An unsatisfiable budget hits the 1-owned-segment floor rather than
    # looping forever or raising.
    assert resolve_tile_count(
        _ROUND_1_SEAMS, round_index=1,
        tokens_per_latent_frame=1000, max_tile_tokens=1) == 5


# -- tile-local remapping (spec §1.6, §1.9) -----------------------------------

def test_to_local_subtracts_the_window_start():
    # §1.6: global pixel indices become tile-local by subtracting pixel_start.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    tile = layout.tiles[1]
    assert tile.to_local(96) == 0
    assert tile.to_local(144) == 48
    assert [tile.to_local(a) for a in tile.anchors] == [0, 48, 96, 144]


def test_first_tile_keeps_the_callers_images_verbatim():
    # §1.9: the first tile (whose window starts at pixel 0) gets the caller's
    # images verbatim.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    placements = [{"index": 0, "frame": 0}, {"index": 1, "frame": 200}]
    assert tile_local_placements(layout.tiles[0], placements) == placements


def test_non_first_tile_keeps_only_in_window_images_and_rebases_them():
    # §1.9: every other tile keeps only the images whose global frame index
    # falls INSIDE its window (pixel_start <= idx <= pixel_end) and rebases
    # each to idx - pixel_start. Frame index 0 means THIS tile's first frame,
    # so re-applying the opening image on a non-first tile would pin the wrong
    # frame onto the seam.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    tile = layout.tiles[1]  # window 96…240
    placements = [{"index": 0, "frame": 0}, {"index": 1, "frame": 200}]
    assert tile_local_placements(tile, placements) == [{"index": 1, "frame": 104}]


def test_tile_with_no_surviving_images_gets_an_empty_list():
    # §1.9: a tile with no surviving images gets an empty conditioning list,
    # skipping the image encoder entirely.
    layout = plan_round(_ROUND_1_SEAMS, frames=241, num_tiles=2)
    assert tile_local_placements(layout.tiles[1], [{"index": 0, "frame": 0}]) == []


# -- re-anchoring (the increment-1 escape hatch) ------------------------------

def test_reanchor_reproduces_the_full_density_bag():
    # The `reanchor_each_round` hatch re-derives the bag on the round's own
    # timeline at the canvas's segment length. For 121 frames (S = 24), the
    # round-1 timeline has 240 content frames, so re-anchoring yields
    # [24, 48, …, 240] -- exactly §1.5.3's carry bag into round 2, and hence
    # §1.6's round-2 table (10 seams, 4 tiles, ownership [3, 3, 2, 2]).
    canvas = plan_canvas(121)
    positions = reanchor_positions(241, canvas.segment_length)
    assert positions == (24, 48, 72, 96, 120, 144, 168, 192, 216, 240)
    layout = plan_round(double_positions(positions), frames=481, num_tiles=4)
    _assert_table(layout, _ROUND_2_TABLE)


def test_anchor_only_rounds_halve_the_seam_density():
    # Without generated slots the bag only doubles, so round 2 sees 5 seams
    # rather than 10 -- coarser, but every layout constraint still holds.
    seams_r2 = double_positions(_ROUND_1_SEAMS)
    assert seams_r2 == (96, 192, 288, 384, 480)
    layout = plan_round(seams_r2, frames=481, num_tiles=4)
    assert [t.owned_segments for t in layout.tiles] == [2, 1, 1, 1]
    assert layout.stitched_latents == (481 - 1) // 8 + 1
