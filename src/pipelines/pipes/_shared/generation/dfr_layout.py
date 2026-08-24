"""DFR canvas / tile / stitch layout math -- pure integers, no torch, no GPU.

Implements the segment grid, the per-round tile windows and the latent stitch
plan for ``generator/dfr_video_ltx``'s temporal densification rounds, from the
method description in ``ai/DFR_FACTS_SPEC.md`` (§1.4, §1.5.3, §1.6, §1.9,
§1.11). It is deliberately a separate module from the pipe: the whole layout is
integer arithmetic, so it can be pinned against the spec's worked tables in a
unit test that runs before any weights exist -- which is the only cheap defence
against the seam being off by one latent frame, a defect whose output still
decodes and reads as a stutter or a skip rather than as a bug.

Three ideas, in the order the pipe uses them:

**Canvas.** A clip is covered by a segment grid of length ``S`` chosen from
{24, 32} (whichever leaves the smaller padding remainder; a tie keeps the
larger), and one keyframe sits at every segment boundary except frame 0 --
excluded because under causal encoding the first latent frame already covers
exactly one pixel frame. The terminal frame IS a slot, and the tiling below
depends on that.

**Tiles.** Round ``r`` cuts the timeline at the keyframe positions carried into
it. Tiles meet exactly at those seams; every tile but the first reaches one
segment BACKWARDS past the seam it shares with its predecessor. That lead-in
exists for two independent reasons and both matter: a tile's local latent 0 is
a one-pixel-frame image latent and its local latent 1 was denoised against it,
so neither may enter the mid-canvas stream; and the window still has to OPEN on
a keyframe anchor. The lead-in is therefore discarded, plus **one more latent**
-- the seam latent itself, which the previous tile keeps because handover
happens exactly at the shared keyframe.

**Stitch.** Each tile contributes its latent from its drop prefix onward and
the pieces are concatenated. There is no overlap blending, no crossfade and no
weighted seam; treating the lead-in as generic "context overlap" and blending
it corrupts every seam. :attr:`DfrRoundLayout.stitched_latents` is checked
against :attr:`DfrRoundLayout.expected_latents` by the pipe after every round.

Two tie-breaks live here and they point in OPPOSITE directions: within a round
the same mid-segment slot position can be produced by two tiles (the lead-in
overlaps), and the EARLIER tile wins; when the round's anchors and its new
slots are merged into the next round's bag, the SLOT wins.

Increment 1 generates no slots -- :func:`plan_round` still reports them, and
:func:`dedupe_slots` / :func:`merge_keyframe_bag` still implement both rules,
because the bag bookkeeping is what increment 2 attaches generated content to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, TypeVar

# Segment-length candidates for the canvas grid; ties keep the LARGER.
DFR_SEGMENT_CANDIDATES: Tuple[int, ...] = (24, 32)
# A non-first tile's window starts this many segments before the region it owns.
TILE_LEAD_SEGMENTS = 1
# The causal VAE's temporal grid: pixel frames come in 1 + 8k, one latent frame
# per 8 pixel frames after the first.
LTX_TEMPORAL = 8
# A segment shorter than this cannot carry a tile lead-in.
MIN_SEGMENT_LATENTS = 2

T = TypeVar("T")


def _latent_index(pixel: int) -> int:
    return pixel // LTX_TEMPORAL


def _require_frame_grid(frames: int, what: str) -> None:
    if frames < 1 or (frames - 1) % LTX_TEMPORAL != 0:
        raise ValueError(
            f"{what} must be on the causal VAE's 1 + 8k temporal grid, got {frames}")


# -- canvas -------------------------------------------------------------------

@dataclass(frozen=True)
class DfrCanvas:
    """The segment grid laid over one clip.

    ``canvas_frames`` can EXCEED ``requested_frames`` (when the content length
    is not a multiple of the chosen segment length); the excess is trimmed at
    the very end of the pipe, never here.
    """

    requested_frames: int
    segment_length: int
    content_padded: int
    canvas_frames: int
    slot_positions: Tuple[int, ...]

    @property
    def padding_frames(self) -> int:
        return self.canvas_frames - self.requested_frames


def plan_canvas(requested_frames: int, candidates: Sequence[int] = DFR_SEGMENT_CANDIDATES) -> DfrCanvas:
    """Choose the segment length and place the canvas slots for a request.

    The candidate leaving the smaller padding remainder wins; on a tie the
    LARGER candidate is kept. Slots land on every segment boundary except frame
    0, terminal included.
    """
    _require_frame_grid(requested_frames, "DFR canvas frame count")
    content = requested_frames - 1
    if content <= 0:
        raise ValueError("DFR canvas needs at least 9 frames (one 8-frame latent past frame 0)")

    # `-s` breaks the tie toward the LARGER candidate: equal padding sorts by
    # descending segment length.
    segment = min(candidates, key=lambda s: ((s - content % s) % s, -s))
    content_padded = -(-content // segment) * segment
    slots = tuple(range(segment, content_padded + 1, segment))
    return DfrCanvas(
        requested_frames=requested_frames,
        segment_length=segment,
        content_padded=content_padded,
        canvas_frames=content_padded + 1,
        slot_positions=slots,
    )


def round_output_frames(frames: int, rounds: int) -> int:
    """Pixel frames after ``rounds`` temporal rounds: each round maps
    ``N -> 2(N - 1) + 1``, so the contract to the caller is
    ``(frames - 1) * 2**rounds + 1``."""
    if rounds < 0:
        raise ValueError(f"DFR round count must be non-negative, got {rounds}")
    return (frames - 1) * (2 ** rounds) + 1


def reanchor_positions(frames: int, segment_length: int) -> Tuple[int, ...]:
    """The canvas slot positions for a round's own timeline, at the ORIGINAL
    segment length.

    The escape hatch behind the pipe's ``reanchor_each_round``: carrying only
    anchors forward doubles the seam spacing every round, and re-deriving the
    grid on the new timeline restores full density. Reusing the canvas's own
    ``segment_length`` (rather than re-running the candidate selection) keeps
    the result exact -- a round's content length is the canvas content doubled,
    so it stays a multiple of the same segment length and the last position is
    always the terminal frame.
    """
    _require_frame_grid(frames, "DFR round frame count")
    content = frames - 1
    if content % segment_length != 0:
        raise ValueError(
            f"DFR re-anchor: {frames} frames is not a whole number of "
            f"{segment_length}-frame segments past frame 0")
    return tuple(range(segment_length, content + 1, segment_length))


# -- carry-forward bookkeeping ------------------------------------------------

def double_positions(positions: Iterable[int]) -> Tuple[int, ...]:
    """Positions on the next round's timeline. The latents themselves are
    single-frame, so only their positions scale."""
    return tuple(2 * int(p) for p in positions)


def dedupe_slots(per_tile_slots: Sequence[Sequence[Tuple[int, T]]]) -> List[Tuple[int, T]]:
    """Collapse ``(position, value)`` slots produced by the tiles of ONE round.

    Because a non-first tile's lead-in overlaps its predecessor, the same
    mid-segment position can be generated twice; the EARLIER tile's version
    wins (first occurrence in tile order). Survivors come back sorted by
    position.
    """
    seen: Dict[int, T] = {}
    for tile_slots in per_tile_slots:
        for position, value in tile_slots:
            seen.setdefault(int(position), value)
    return sorted(seen.items())


def merge_keyframe_bag(
    anchors: Sequence[Tuple[int, T]],
    slots: Sequence[Tuple[int, T]],
) -> List[Tuple[int, T]]:
    """The next round's bag: anchors first, then slots.

    Slots are merged AFTER anchors, so a position present in both keeps the
    SLOT's value -- the opposite tie-break to :func:`dedupe_slots`, which is
    exactly why the two live next to each other here.
    """
    bag: Dict[int, T] = {int(p): v for p, v in anchors}
    for position, value in slots:
        bag[int(position)] = value
    if not bag:
        raise ValueError("DFR keyframe bag is empty -- a round needs at least one anchor")
    return sorted(bag.items())


# -- tiles --------------------------------------------------------------------

@dataclass(frozen=True)
class DfrTile:
    """One tile's window on the round's timeline.

    ``pixel_start``/``pixel_end`` are INCLUSIVE global pixel-frame bounds;
    ``latent_start``/``latent_end`` are a half-open latent range. ``anchors``
    and ``slots`` are GLOBAL pixel positions -- call :meth:`to_local` before
    handing them to a conditioning builder.
    """

    index: int
    pixel_start: int
    pixel_end: int
    own_pixel_start: int
    latent_start: int
    latent_end: int
    local_frames: int
    drop_latent_prefix: int
    owned_segments: int
    lead_segments: int
    anchors: Tuple[int, ...]
    slots: Tuple[int, ...]

    @property
    def latent_frames(self) -> int:
        return self.latent_end - self.latent_start

    @property
    def kept_latents(self) -> int:
        """Latent frames this tile contributes to the stitch."""
        return self.latent_frames - self.drop_latent_prefix

    def to_local(self, pixel: int) -> int:
        """Global pixel index -> tile-local pixel index."""
        return int(pixel) - self.pixel_start

    def projected_tokens(self, tokens_per_latent_frame: int) -> int:
        """Video tokens this tile denoises: its own latent frames plus one
        latent frame's worth per appended anchor."""
        return (self.latent_frames + len(self.anchors)) * int(tokens_per_latent_frame)


@dataclass(frozen=True)
class DfrRoundLayout:
    frames: int
    seams: Tuple[int, ...]
    boundaries: Tuple[int, ...]
    tiles: Tuple[DfrTile, ...]

    @property
    def stitched_latents(self) -> int:
        return sum(t.kept_latents for t in self.tiles)

    @property
    def expected_latents(self) -> int:
        return (self.frames - 1) // LTX_TEMPORAL + 1


def _validate_boundaries(boundaries: Sequence[int], frames: int) -> None:
    if len(boundaries) < 2:
        raise ValueError("DFR tiling needs at least one seam")
    for lo, hi in zip(boundaries, boundaries[1:]):
        if hi <= lo:
            raise ValueError(
                f"DFR seam positions must be strictly increasing, got {tuple(boundaries[1:])}")
        span = hi - lo
        if span % LTX_TEMPORAL != 0:
            raise ValueError(
                f"DFR segment span {lo}..{hi} is {span} pixel frames, not a multiple of 8")
        if span < MIN_SEGMENT_LATENTS * LTX_TEMPORAL:
            raise ValueError(
                f"DFR segment span {lo}..{hi} is {span} pixel frames; a segment must be at "
                f"least {MIN_SEGMENT_LATENTS * LTX_TEMPORAL} pixel frames "
                f"({MIN_SEGMENT_LATENTS} latent frames) to carry a tile lead-in")
    if boundaries[-1] != frames - 1:
        raise ValueError(
            f"DFR last seam is {boundaries[-1]} but the round's terminal frame is {frames - 1} -- "
            f"the seam list must end on the terminal frame")


def _ownership(n_segments: int, num_tiles: int) -> List[int]:
    """Contiguous runs of segments per tile: ``divmod`` with the remainder
    distributed to the EARLIEST tiles (largest runs first)."""
    tiles_used = max(1, min(int(num_tiles), n_segments))
    base, rem = divmod(n_segments, tiles_used)
    return [base + 1 if i < rem else base for i in range(tiles_used)]


def plan_round(seams: Sequence[int], *, frames: int, num_tiles: int) -> DfrRoundLayout:
    """Tile windows for one temporal round.

    ``seams`` are the keyframe positions carried into this round (already
    doubled onto its timeline); ``frames`` is the round's own pixel-frame count
    and is what the last seam is checked against. ``num_tiles`` is clamped to
    the segment count -- a finer-than-``2**round`` split is legal and changes
    only how many segments each tile owns, never the seam, lead-in or drop
    rules.
    """
    _require_frame_grid(frames, "DFR round frame count")
    boundaries = (0,) + tuple(int(s) for s in seams)
    _validate_boundaries(boundaries, frames)

    runs = _ownership(len(boundaries) - 1, num_tiles)
    tiles: List[DfrTile] = []
    own_lo = 0
    for index, run in enumerate(runs):
        own_hi = own_lo + run
        lead = 0 if index == 0 else min(TILE_LEAD_SEGMENTS, own_lo)
        window_lo = own_lo - lead

        pixel_start = boundaries[window_lo]
        pixel_end = boundaries[own_hi]
        latent_start = _latent_index(pixel_start)
        latent_end = _latent_index(pixel_end) + 1
        # The seam latent itself is kept by the PREVIOUS tile, so every tile
        # after the first resumes one latent past its own region's start.
        drop = _latent_index(boundaries[own_lo]) - latent_start + (1 if index else 0)

        tiles.append(DfrTile(
            index=index,
            pixel_start=pixel_start,
            pixel_end=pixel_end,
            own_pixel_start=boundaries[own_lo],
            latent_start=latent_start,
            latent_end=latent_end,
            local_frames=(latent_end - latent_start - 1) * LTX_TEMPORAL + 1,
            drop_latent_prefix=drop,
            owned_segments=run,
            lead_segments=lead,
            anchors=tuple(boundaries[i] for i in range(window_lo, own_hi + 1) if boundaries[i] != 0),
            slots=tuple((boundaries[i] + boundaries[i + 1]) // 2 for i in range(window_lo, own_hi)),
        ))
        own_lo = own_hi

    return DfrRoundLayout(frames=frames, seams=tuple(boundaries[1:]), boundaries=boundaries,
                          tiles=tuple(tiles))


def default_tile_count(round_index: int) -> int:
    """``2**round`` -- the tile count a round uses unless a config overrides it
    or the token budget forces a finer split."""
    return 2 ** int(round_index)


def resolve_tile_count(
    seams: Sequence[int],
    *,
    round_index: int,
    override: Optional[int] = None,
    tokens_per_latent_frame: Optional[int] = None,
    max_tile_tokens: Optional[int] = None,
) -> int:
    """How many tiles this round should use.

    An explicit ``override`` wins outright. Otherwise the count starts at
    ``2**round_index`` and is raised until no tile's projected token count
    exceeds ``max_tile_tokens`` or the one-owned-segment floor is reached --
    the VRAM relief valve, which never changes the seam geometry.
    """
    n_segments = len(seams)
    if override:
        return max(1, min(int(override), n_segments))

    count = min(default_tile_count(round_index), n_segments)
    if not max_tile_tokens or not tokens_per_latent_frame:
        return count

    frames = seams[-1] + 1
    while count < n_segments:
        layout = plan_round(seams, frames=frames, num_tiles=count)
        if all(t.projected_tokens(tokens_per_latent_frame) <= max_tile_tokens for t in layout.tiles):
            return count
        count += 1
    return count


# -- tile-local media conditioning (§1.9) -------------------------------------

def tile_local_placements(
    tile: DfrTile,
    placements: Sequence[Dict[str, Any]],
    *,
    frame_key: str = "frame",
) -> List[Dict[str, Any]]:
    """Re-address the caller's media placements for one tile.

    Frame index 0 means *this tile's* first frame, so re-applying the opening
    image on a non-first tile would pin the wrong frame onto the seam. The
    first tile (window starting at pixel 0) keeps the caller's placements
    verbatim; every other tile keeps only those whose global frame index falls
    inside its window and rebases each to ``idx - pixel_start``. A tile with
    nothing left gets an empty list, which skips the image encoder entirely.

    ``placements`` must already carry INTEGER pixel-frame indices under
    ``frame_key`` -- resolving ``"first"``/``"last"`` is the caller's job.
    """
    if tile.pixel_start == 0:
        return list(placements)
    local: List[Dict[str, Any]] = []
    for placement in placements:
        index = int(placement[frame_key])
        if tile.pixel_start <= index <= tile.pixel_end:
            local.append({**placement, frame_key: index - tile.pixel_start})
    return local
