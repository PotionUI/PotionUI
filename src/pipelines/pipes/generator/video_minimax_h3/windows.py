"""Video Director document -> MiniMax-H3 sliding-window plan.

MiniMax-H3 generates 5-15 seconds in one packed sequence. A longer Director
document is run as a SEQUENCE of windows, each a full H3 generation, where a
continuation window is pinned to its predecessor by re-using that window's
final latent frames as keyframe conditioning rows. This module owns the pure
arithmetic of that split -- which segment becomes which window, how much of
each window is context that must be trimmed back off, and where a Director
keyframe lands once the timeline is cut into windows. It holds no torch state
and touches no model; `main.py` executes the plan.

**Continuation is a latent splice, never a pixel round trip.** The previous
window's sampled video latent tail goes straight into the next window's
condition rows: no VAE decode/re-encode, and no re-reading of a decoded frame.
The same applies to the audio carry (`audio.pack_audio_rows` /
`unpack_audio_rows`).

**Overlap is counted in pixel frames but spliced in latent frames**, and the
conversion is cycle-aware because H3's video VAE does not compress time
uniformly: a latent frame covers `1, 4, 4, 4, 4` pixel frames cycling
(`geometry.LATENT_FRAME_PIXEL_SPANS`). The two ends of a clip sit at different
phases of that cycle, so `geometry.tail_latents_for_frames` (how many latents
to take from the SOURCE window) and `geometry.head_frames_for_latents` (how
many decoded frames to trim off the FOLLOWING window) are separate walks that
coincide only at whole 17-frame chunks. The plan records both numbers so the
executor never has to re-derive either.

**Per-segment routing comes from the normalizer, not from re-reading media.**
`derive_segment_routing` already resolved every segment to a `sub_type`
(`src/features/video_director/normalize.py`); `"chain"` is the only sub-type
that continues from the previous window, and `"i2v"`/`"flf"` open on their own
image. A segment that opens on its own image is a CUT: it takes no
continuation tail and contributes its whole decoded length.

**ref2va Director runs are hard-cut-only.** A reference-conditioned request
builds every window's prefix as a `ReferenceBlock` sequence
(`layout.build_ref2va_packed_sequence`) -- a diffusers-derived layout distinct
from the OVERLAY `keyframe_anchors` mechanism continuation and per-window
Director keyframes both use (`layout.build_packed_sequence`). Diffusers ships
no block that combines the two, so `main.py`'s `_validate_refs_director_plan`
forbids `continues_previous` and a non-empty `keyframes` outright on any
window once references are active, rather than inventing a fused layout with
no reference in the ported source: every window of a refs-conditioned run is
its own independent t2v-with-refs generation, joined by hard cuts.

**Per-segment reference selection.** `WindowPlan.reference_indices` is an
optional list of indices into the request's packed reference order (images,
then videos, then audio -- `reference_order.pack_references`); `None` (the
default -- the field absent on the segment) means every reference conditions
that window, the whole-film case. A window's own subset is handed to the text
encoder AND laid out in the generator in that subset's OWN relative order,
never the packed set's global positions: `encode_reference_request` numbers
`<Picture i>`/`<Video k>`/`<Audio j>` against whatever list it is actually
given, with no "skip index k" concept, so a 2-of-4 subset RE-LABELS to 1/2 for
that window rather than preserving the global 1..4 numbering. That is the
only labeling scheme the ported presentation supports without inventing a
label-remapping layer it never had; the alternative (global labels, rows
filtered independently) was rejected for exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    FPS,
    MAX_DURATION_S,
    MIN_DURATION_S,
    align_num_frames,
    audio_latent_num_frames,
    head_frames_for_latents,
    latent_index_for_frame,
    tail_latents_for_frames,
    video_latent_num_frames,
)

CONTINUING_SUB_TYPE = "chain"
FIRST_IMAGE_SUB_TYPES = ("i2v", "flf")


class DirectorPlanError(ValueError):
    """A Director document this pipe cannot execute.

    Distinct from `VideoDirectorValidationError`: the document is already
    schema-valid: what fails here are the H3-specific limits the normalizer
    has no way to express (the released 5-15 s per-window duration range,
    the unsupported audio roles).
    """


@dataclass(frozen=True)
class WindowKeyframe:
    """One Director image conditioning a specific latent frame of one window.

    `image_index` indexes the pipe's own `image` input array, which the preset
    fills from `document.media_images` -- the same ordering
    `derive_ltx_media_fields` assigned, so the two can never drift.

    `strength` is carried for provenance only. H3's conditioning mechanism has
    no per-anchor weight: a condition row is noised to a fixed `t = 0.999` and
    then frozen, so there is nothing for a strength to scale.
    """

    image_index: int
    latent_index: int
    frame: int
    strength: float = 1.0


@dataclass(frozen=True)
class WindowPlan:
    """One H3 generation inside a windowed Director run."""

    index: int
    segment_id: str
    sub_type: str
    prompt: str
    requested_frames: int
    frames: int
    num_latent_frames: int
    num_audio_latents: int
    seed: int
    steps: Optional[int]
    # Latent frames spliced from the previous window's tail into this window's
    # leading condition rows, and the decoded pixel frames they reproduce --
    # `head_frames_for_latents(overlap_latents)`, which is exactly what gets
    # trimmed off the front of this window's output.
    overlap_latents: int
    overlap_frames: int
    keyframes: Tuple[WindowKeyframe, ...] = ()
    # `None` = every packed reference conditions this window (whole-film).
    # A tuple = a per-shot subset, indices into the packed reference order,
    # in the order given -- see the module docstring's "Per-segment
    # reference selection" section for why order matters here.
    reference_indices: Optional[Tuple[int, ...]] = None

    @property
    def continues_previous(self) -> bool:
        return self.overlap_latents > 0

    @property
    def emitted_frames(self) -> int:
        """Pixel frames this window contributes to the stitched result."""
        return self.frames - self.overlap_frames


@dataclass(frozen=True)
class DirectorPlan:
    windows: Tuple[WindowPlan, ...]
    stitch: bool
    mux_audio_path: Optional[str] = None

    @property
    def total_frames(self) -> int:
        return sum(window.emitted_frames for window in self.windows)


def _window_frames(requested: int, *, context: str) -> int:
    """Snap one segment's frame count onto the VAE's `17n+5` lattice and hold
    it to the released per-window duration range.

    The 5-15 s bound is applied PER WINDOW rather than to the stitched total:
    each window is a complete H3 generation and the range is the one the model
    was released for, so a 3-second window is out-of-distribution even though a
    three-window 9-second result would not be. The stitched total is
    deliberately unbounded -- that is the whole point of windowing.
    """
    frames = align_num_frames(requested)
    duration = frames / FPS
    if not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        raise DirectorPlanError(
            f"{context}: MiniMax-H3 generates {MIN_DURATION_S:g}-{MAX_DURATION_S:g} seconds per window at "
            f"{FPS} fps, so a segment's frames (rounded up to the next 17n+5) must be between "
            f"{int(MIN_DURATION_S * FPS)} and {int(MAX_DURATION_S * FPS)}, got {requested} "
            f"(rounded up to {frames}, {duration:.2f}s). Split the shot into more segments."
        )
    return frames


def _keyframe_media_in_placement_order(media: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The `media` entries `derive_ltx_media_fields` turned into `role:
    "keyframe"` placements, in the SAME order -- image-typed firsts, then
    lasts, then keyframes sorted by `at`.

    Rebuilt here only to recover each placement's originating `segment_id`,
    which the placement itself does not carry (the derivation is shaped for
    LTX, which has one window and therefore no segment to attribute an image
    to). The placement stays authoritative for the image INDEX; this list is
    zipped against it positionally and length-checked, so a change to the
    derivation's traversal fails loudly instead of silently rebinding images
    to the wrong segments.
    """
    def is_image(entry: Dict[str, Any]) -> bool:
        return ((entry.get("media") or {}).get("type") or "image") != "video"

    firsts = [m for m in media if m.get("role") == "first" and is_image(m)]
    lasts = [m for m in media if m.get("role") == "last" and is_image(m)]
    keyframes = sorted(
        (m for m in media if m.get("role") == "keyframe" and is_image(m)),
        key=lambda m: m.get("at") or 0,
    )
    return firsts + lasts + keyframes


def _resolve_overlap(settings: Dict[str, Any]) -> Tuple[int, bool]:
    """`(overlap latent frames, stitch)` from `settings.continuation`.

    `source: "last_frame"` pins exactly one latent frame -- the cheapest
    continuation, and the one whose pixel span (1 frame) is smallest, so it
    hands the next window a single still to continue from. `"tail_frames"`
    converts the requested pixel overlap through the VAE's cycle. A document
    with no continuation block at all runs its segments as independent cuts.
    """
    continuation = settings.get("continuation") or {}
    stitch = bool(continuation.get("stitch", True))
    source = continuation.get("source")
    if source == "last_frame":
        return 1, stitch
    if source == "tail_frames":
        return tail_latents_for_frames(int(continuation.get("overlap_frames", 0) or 0)), stitch
    return 0, stitch


def resolve_mux_audio(document: Dict[str, Any]) -> Optional[str]:
    """The single user audio track to mux over the stitched result, if any.

    `role: "condition"` (a soundtrack the model should FOLLOW) is refused
    rather than silently muxed: H3 can genuinely take a conditioning
    soundtrack -- `layout.build_packed_sequence`'s `num_condition_audio_latents`
    prefix is that mechanism -- and quietly downgrading it to a mux would look
    like it worked while the picture ignored the track completely.
    """
    entries = document.get("audio") or []
    conditioning = [entry for entry in entries if entry.get("role") == "condition"]
    if conditioning:
        raise DirectorPlanError(
            "audio: a 'condition' soundtrack (the model following your audio) is not yet supported by the "
            "MiniMax-H3 generator -- set the clip's role to 'mux' to lay it over the finished video instead"
        )
    for entry in entries:
        path = (entry.get("media") or {}).get("path")
        if path:
            return str(path)
    return None


def build_director_plan(document: Optional[Dict[str, Any]], *, default_seed: int) -> Optional[DirectorPlan]:
    """Cut a normalized Director document into the windows this pipe runs.

    Returns `None` for any document that is not a routed multi-segment
    `director` run, which is what keeps every non-Director request on the
    single-window path unchanged.
    """
    if not isinstance(document, dict) or document.get("mode") != "director":
        return None
    segments = document.get("segments") or []
    if not segments:
        return None

    settings = document.get("settings") or {}
    overlap_latents_default, stitch = _resolve_overlap(settings)
    document_seed = settings.get("seed")
    base_seed = int(document_seed) if isinstance(document_seed, int) else int(default_seed)

    windows: List[WindowPlan] = []
    for index, segment in enumerate(segments):
        sub_type = segment.get("sub_type") or (CONTINUING_SUB_TYPE if index else "t2v")
        requested = segment.get("frames")
        if not isinstance(requested, int):
            raise DirectorPlanError(
                f"segments[{index}]: a MiniMax-H3 Director segment needs an explicit frame count"
            )
        frames = _window_frames(requested, context=f"segments[{index}]")
        num_latent_frames = video_latent_num_frames(frames)

        continues = index > 0 and sub_type == CONTINUING_SUB_TYPE
        overlap_latents = min(overlap_latents_default, num_latent_frames - 1) if continues else 0
        segment_seed = segment.get("seed")

        raw_reference_indices = segment.get("reference_indices")
        reference_indices: Optional[Tuple[int, ...]] = None
        if raw_reference_indices is not None:
            if not isinstance(raw_reference_indices, list) or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in raw_reference_indices
            ):
                raise DirectorPlanError(
                    f"segments[{index}]: 'reference_indices' must be a list of integers, got "
                    f"{raw_reference_indices!r}"
                )
            reference_indices = tuple(raw_reference_indices)

        windows.append(WindowPlan(
            index=index,
            segment_id=str(segment.get("id") or f"seg-{index}"),
            sub_type=sub_type,
            prompt=str(segment.get("prompt") or ""),
            requested_frames=requested,
            frames=frames,
            num_latent_frames=num_latent_frames,
            num_audio_latents=audio_latent_num_frames(frames),
            seed=int(segment_seed) if isinstance(segment_seed, int) else base_seed + index,
            steps=segment.get("steps") if isinstance(segment.get("steps"), int) else None,
            overlap_latents=overlap_latents,
            overlap_frames=head_frames_for_latents(overlap_latents),
            reference_indices=reference_indices,
        ))

    windows = _attach_keyframes(windows, document)
    return DirectorPlan(windows=tuple(windows), stitch=stitch, mux_audio_path=resolve_mux_audio(document))


def _attach_keyframes(windows: List[WindowPlan], document: Dict[str, Any]) -> List[WindowPlan]:
    """Bind every Director image to the window and latent frame it conditions."""
    placements = [p for p in (document.get("media_placements") or []) if p.get("role") == "keyframe"]
    if not placements:
        return windows

    sources = _keyframe_media_in_placement_order(document.get("media") or [])
    if len(sources) != len(placements):
        raise DirectorPlanError(
            f"media: {len(placements)} image placement(s) but {len(sources)} matching media entries -- "
            "the document's media list and its derived placements disagree"
        )

    by_window: Dict[int, List[WindowKeyframe]] = {}
    by_segment = {window.segment_id: window for window in windows}
    for placement, source in zip(placements, sources):
        frame = placement.get("frame")
        if frame in ("first", "last"):
            window = by_segment.get(source.get("segment_id"))
            if window is None:
                continue
            local_frame = 0 if frame == "first" else window.frames - 1
        else:
            window, local_frame = _locate_frame(windows, int(frame))
        by_window.setdefault(window.index, []).append(WindowKeyframe(
            image_index=int(placement["index"]),
            latent_index=min(latent_index_for_frame(local_frame), window.num_latent_frames - 1),
            frame=local_frame,
            strength=float(placement.get("strength", 1.0)),
        ))

    return [replace(window, keyframes=tuple(by_window.get(window.index, ()))) for window in windows]


def _locate_frame(windows: List[WindowPlan], global_frame: int) -> Tuple[WindowPlan, int]:
    """Map a stitched-timeline pixel frame to `(window, frame within it)`.

    A continuation window's decoded output opens with `overlap_frames` of
    replayed context that the stitch trims away, so a global frame sits
    `overlap_frames` further into the window than into the timeline. A frame
    past the end of the timeline clamps to the last window's last frame rather
    than failing the generation -- the normalizer already bounded `at` against
    the document's own duration, and rounding at the boundary should not cost
    a user their run.
    """
    start = 0
    for window in windows:
        if global_frame < start + window.emitted_frames:
            return window, global_frame - start + window.overlap_frames
        start += window.emitted_frames
    last = windows[-1]
    return last, last.frames - 1
