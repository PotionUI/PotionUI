"""Video Director document -> Wan chain per-segment frame/overlap geometry.

Wan's chain generator (`main.py`) runs each segment as its own generation,
optionally trimming a continuation ("chain") segment's leading edge -- the
frames replayed from the previous segment's tail rather than contributing new
footage -- before the per-segment clips are (optionally) stitched into one
continuous video. This module owns the pure arithmetic mirrored from that
timing: the causal VAE's `1 + 4k` frame lattice a requested length snaps to,
and how many of a continuation segment's leading frames are context rather
than new content. It holds no torch state and touches no model -- `main.py`
executes the actual generation; `src.features.video_director.compile` calls
this module (for the `family: "wan"` capability) to derive the SAME stitched
timeline every free-floating keyframe/audio offset and the `settings.duration`
reported for a per-shot render are rebased against, mirroring
`video_minimax_h3/windows.py`'s `resolve_window_geometry` for the
`minimax_h3` family.

**Requires an explicit timing profile.** Unlike H3's continuation overlap
(entirely document-derived), Wan's real per-segment overlap ALSO depends on
`motion_latent_count` -- a generation-time pipe config knob
(`GeneratorWanChainVideoPipe.get_default_config`'s `motion_latent_count`,
sourced from the `svi_motion_latent_count` form field, a SIBLING of the Video
Director document, never part of it: `pipeline.yml` threads
`{{ form.svi_motion_latent_count | default(1) }}` straight into the pipe's
config). `resolve_window_geometry` below takes that value via
`settings["timing_profile"]["motion_latent_count"]` instead of re-deriving it
-- the orchestrator (`src/features/generation/orchestrator.py`) attaches it
onto the normalized document from the SAME bound form the generator itself
reads, using the SAME undefined-only-default semantics as the preset's own
Jinja default (a field present with any value -- including the UI's own
default of 2 -- is used as-is; the field genuinely ABSENT from the form falls
back to 1, `resolve_motion_latent_count`'s own default). `compile.py` only
calls this module when that profile is actually present on the document --
see its own `_WAN_FAMILY` comment for what happens when it's absent (a
preset instance that hasn't wired the capability, or a hand-built document).

**The trim clamp mirrors H3's own, and is SEQUENTIAL.** A continuation
segment's context is `min(tail_count, the PREVIOUS segment's own
`on_disk_frames`)` -- never that previous segment's aligned `frames`, which
over-estimates whenever the previous segment was itself trimmed. This is
the same clamp `video_minimax_h3/windows.py` uses for its own
`overlap_latents = min(overlap_latents_default, num_latent_frames - 1)`,
generalized to chain sequentially: two short continuations in a row compound
(segment i's context is bounded by segment i-1's OWN trimmed output, which
was itself bounded by segment i-2's), mirroring `main.py`'s
`prev_tail.shape[0]` -- always the previous segment's ACTUAL decoded length
after its own pre-decode trim, never a re-derived aligned count. Each
segment then either trims that context off pre-decode (`context_trimmed`,
when the window is long enough: `on_disk_frames = frames - context`,
`join_frames = 0`) or defers it to a stitch-time join otherwise
(`on_disk_frames = frames`, `join_frames = min(context, frames - 1)`,
leaving at least one real frame) -- exactly one of the two per continuation
segment, mirroring `main.py`'s `segment_context_trimmed` /
`segment_join_overlap`. The TOTAL a `"chain"` segment contributes either way
is `on_disk_frames - join_frames` (the `emitted_frames` property); `main.py`
itself calls `resolve_continuation`/`tail_frame_count` from this module for
exactly that reason, so the two can never drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

CONTINUING_SUB_TYPE = "chain"

# Mirrors `txt2vid_wan22/main.py`'s own private `_TEMPORAL_DOWNSCALE` -- kept
# as a local literal (not imported) so this module stays torch-free: that
# module's import chain pulls torch immediately.
_TEMPORAL_DOWNSCALE = 4


def _snap_frame_count(frames: int, temporal_downscale: int = _TEMPORAL_DOWNSCALE) -> int:
    """Snap `frames` to the nearest `1 + k*temporal_downscale` (ties round
    down, floor of one frame) -- the SAME algorithm as
    `src.platform.runtime.native.resolution.snap_frame_count` (what
    `chain_video_wan22/main.py`'s own `_snap_geometry` actually calls),
    reimplemented locally rather than imported: that module's package
    (`src.platform.runtime.native`) pulls torch on import, which would defeat
    this module's whole point -- being callable from `compile.py` (and a
    plain test) with no model/torch dependency, exactly like
    `video_minimax_h3/geometry.py`'s own lattice arithmetic.
    """
    td = max(1, int(temporal_downscale))
    k = max(0, math.ceil((frames - 1) / td - 0.5))
    return 1 + k * td


class WanDirectorPlanError(ValueError):
    """A Director document this module cannot derive Wan chain geometry for.

    Distinct from `VideoDirectorValidationError`: the document is already
    schema-valid -- this only fails on the same defensive check `main.py`'s
    own per-segment loop makes at generation time (a segment with no
    resolved frame count).
    """


@dataclass(frozen=True)
class SegmentWindowGeometry:
    """One chain segment's own frame/overlap geometry -- the pure-arithmetic
    half of what `chain_video_wan22/main.py`'s per-segment loop produces, with
    no model/prompt/seed bookkeeping attached. Mirrors
    `video_minimax_h3.windows.SegmentWindowGeometry`'s shape so `compile.py`
    can consult either family's planner identically.
    """

    frames: int
    requested_frames: Optional[int]
    sub_type: str
    # Whether this segment continues the previous one's tail (main.py's
    # `sub_type == "chain"` check) -- the "fresh cut vs continuation" join
    # kind. A fresh t2v/i2v/flf segment (including segment 0) is always
    # `False`: it opens on its own content, contributing every aligned frame.
    is_continuation: bool
    # Pixel frames this segment's front replays from the previous segment's
    # tail rather than contributing new content ("overlap-in"), TOTAL across
    # whichever of the two stages below realized it. Always 0 for a fresh cut.
    overlap_frames: int
    # The length actually on disk BEFORE any stitch-time join -- main.py's
    # `segment_emitted_frames` (a name this module deliberately does not
    # reuse: here `emitted_frames`, below, means the FINAL net contribution
    # after the join too). Equals `frames` for a fresh cut or a continuation
    # whose context was too short to trim pre-decode; less than `frames` only
    # when THIS segment's own leading context WAS trimmed pre-decode.
    on_disk_frames: int
    # Whether this segment's own pre-decode trim ran -- mirrors main.py's
    # `segment_context_trimmed`. Mutually exclusive with `join_frames > 0`:
    # a continuation's overlap is realized at exactly one of the two stages,
    # never both.
    context_trimmed: bool
    # The overlap this segment's join is PLANNED to crossfade away at stitch
    # time -- mirrors main.py's `segment_join_overlap`. Always 0 when
    # `context_trimmed` is True (already handled pre-decode) or the segment
    # isn't a continuation.
    join_frames: int

    @property
    def emitted_frames(self) -> int:
        """Pixel frames this segment CONTRIBUTES to the stitched timeline,
        net of BOTH possible drop stages (`frames - overlap_frames`, i.e.
        `on_disk_frames - join_frames`)."""
        return self.frames - self.overlap_frames


def resolve_continuation(settings: Dict[str, Any]) -> Tuple[int, bool]:
    """`(default overlap in pixel frames, stitch)` from `settings.continuation`
    -- mirrors `chain_video_wan22/main.py`'s own read of the SAME document
    field exactly: `source: "last_frame"` pins the overlap to a single pixel
    frame; an absent `continuation` block, or one with no `overlap_frames`,
    defaults to 4.
    """
    continuation = settings.get("continuation")
    default_overlap = 4 if continuation is None else int(continuation.get("overlap_frames", 4) or 0)
    source = None if continuation is None else continuation.get("source")
    stitch = True if continuation is None else bool(continuation.get("stitch", True))
    if source == "last_frame":
        default_overlap = 1
    return default_overlap, stitch


def resolve_motion_latent_count(settings: Dict[str, Any]) -> int:
    """`settings.timing_profile.motion_latent_count`, defaulting to 1 -- the
    SAME undefined-only fallback `pipeline.yml`'s
    `{{ form.svi_motion_latent_count | default(1) }}` applies when the
    orchestrator found no `svi_motion_latent_count` on the bound form at all
    (see the module docstring's "Requires an explicit timing profile").
    """
    profile = settings.get("timing_profile") or {}
    return int(profile.get("motion_latent_count", 1) or 1)


def tail_frame_count(default_overlap: int, motion_latent_count: int) -> int:
    """Pixel frames of the previous segment's tail a continuation segment's
    front replays -- mirrors `main.py`'s own `tail_count` exactly: bounded
    above by BOTH the configured overlap and how many pixel frames
    `motion_latent_count` latent slots actually span (1 slot ~= 4 pixel
    frames, `_TEMPORAL_DOWNSCALE`).
    """
    base_tail = default_overlap if default_overlap > 0 else 1
    motion_frames = (motion_latent_count - 1) * _TEMPORAL_DOWNSCALE + 1
    return max(1, min(base_tail, motion_frames))


def resolve_window_geometry(
    segments: List[Dict[str, Any]], settings: Dict[str, Any],
) -> List[SegmentWindowGeometry]:
    """Per-segment frame/overlap geometry, in document order.

    Raises `WanDirectorPlanError` when a segment has no explicit frame count.
    """
    default_overlap, _stitch = resolve_continuation(settings)
    motion_latent_count = resolve_motion_latent_count(settings)
    tail_count = tail_frame_count(default_overlap, motion_latent_count)

    geometry: List[SegmentWindowGeometry] = []
    for index, segment in enumerate(segments):
        sub_type = segment.get("sub_type") or (CONTINUING_SUB_TYPE if index else "t2v")
        requested = segment.get("frames")
        if not isinstance(requested, int):
            raise WanDirectorPlanError(
                f"segments[{index}]: a Wan Director segment needs an explicit frame count"
            )
        frames = _snap_frame_count(requested)
        is_continuation = index > 0 and sub_type == CONTINUING_SUB_TYPE
        if is_continuation:
            # Sequential, mirroring main.py's per-segment loop exactly: the
            # context THIS segment can replay is bounded by what the
            # PREVIOUS segment actually put ON DISK after its OWN pre-decode
            # trim (`on_disk_frames`) -- never that segment's aligned
            # `frames`, which over-estimates whenever the previous segment
            # was itself trimmed. Two short continuations in a row compound:
            # main.py's `prev_tail.shape[0]` for segment i comes from
            # segment i-1's OWN trimmed output, not i-1's untrimmed length.
            context = min(tail_count, geometry[index - 1].on_disk_frames)
            if frames > context + 1:
                on_disk_frames = frames - context
                context_trimmed = True
                join_frames = 0
            else:
                on_disk_frames = frames
                context_trimmed = False
                join_frames = min(context, frames - 1)
        else:
            on_disk_frames = frames
            context_trimmed = False
            join_frames = 0
        emitted = on_disk_frames - join_frames
        overlap_frames = frames - emitted
        geometry.append(SegmentWindowGeometry(
            frames=frames,
            requested_frames=requested,
            sub_type=sub_type,
            is_continuation=is_continuation,
            overlap_frames=overlap_frames,
            on_disk_frames=on_disk_frames,
            context_trimmed=context_trimmed,
            join_frames=join_frames,
        ))
    return geometry
