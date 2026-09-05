"""
Per-shot generation compiler for the Video Director.

`normalize_video_director` (`src/features/video_director/normalize.py`) validates and
canonicalizes the WHOLE film -- every segment, resolved `sub_type`, rolled
`settings.seed`, packed `reference_indices`, the lot. `compile_shot_plan` runs
strictly AFTER that, on its output, and answers a narrower question: "the user
only checked shots 2 and 3 -- give me a document that renders exactly that
span, and only that span, as if it had always been the whole film".

This is a CHAIN-STYLE-ONLY concern (Wan / MiniMax-H3 video / MiniMax-H3 refs):
those presets already give one segment == one shot, so a submission still
carries the FULL segment list plus a `render: {scope: "shots", shot_ids}`
key naming which of them to actually execute -- see docs/video-director.md's
`render` section. A timeline-style (LTX) submission never reaches this
module: each editor shot already compiles to its OWN standalone wire document
client-side (one shot == one full document), so there is nothing left to
carve out of it server-side.

**Never filter `segments` down to the selected ids.** A segment's derived
values -- its seed, its sub_type, its packed reference subset -- are only
correct in the context of the FULL film (`chain_video_wan22/main.py`'s
`base_seed + i` and `video_minimax_h3/windows.py`'s identical formula both
use the segment's position in the list they're handed as `i`). Re-running the
selected segments through a plan built from just those few segments would
silently roll DIFFERENT seeds and could relabel a packed reference subset.
Instead, this module walks the FULL, already-normalized segment list, bakes
every position-dependent derived value onto the SELECTED segments explicitly
(so it survives being handed to the pipe as a shorter list), and only THEN
slices the list down. `_normalize_render` (normalize.py) has already checked
every id in `shot_ids` names a real segment; this module additionally
requires them to form one CONTIGUOUS run of the film -- a selection with a
gap in it is rejected rather than silently collapsed to render just the
named segments back-to-back, which is the same "never filter" discipline
applied to the selection itself. The order `shot_ids` arrives in doesn't
matter: the compiled span always comes back in the film's own segment order
(continuation depends on it), never the caller's order.
"""

from typing import Any, Dict, List, Optional

from src.features.video_director.normalize import (
    VideoDirectorValidationError,
    derive_ltx_media_fields,
    wan_model_set_for,
)
from src.pipelines.pipes.generator.video_minimax_h3.windows import resolve_window_geometry

# The one family whose stitched timeline this module knows how to derive from
# something OTHER than a segment's own raw `frames / fps` -- see
# `_effective_segment_durations`. Keyed off `family` (the orchestrator's own
# read of the target preset's `video_director.family` capability, resolved
# from the SAME preset capabilities block `normalize_video_director` was
# handed, never guessed from the document's shape); every other family,
# including Wan and an absent/unknown `family`, keeps the legacy raw-frame
# axis -- Wan's own effective per-window overlap depends on a pipe-config
# knob (`motion_latent_count`) that never travels with the Video Director
# document, so there is no document-only "Wan planner" to call here yet.
_MINIMAX_H3_FAMILY = "minimax_h3"


def _segment_duration(segment: Dict[str, Any], fps: Optional[float]) -> float:
    """A chain-style segment's own length in seconds, or `0.0` when either its
    `frames` or the document's `fps` is missing -- callers only ever use this
    to build cumulative offsets, where a `0.0` segment simply contributes no
    width rather than raising (a timeline-style document, which has no
    per-segment `frames` at all, never reaches this module in the first
    place -- see the module docstring)."""
    frames = segment.get("frames")
    if isinstance(frames, int) and isinstance(fps, (int, float)) and fps > 0:
        return frames / fps
    return 0.0


def _effective_segment_durations(
    segments: List[Dict[str, Any]], settings: Dict[str, Any], fps: Optional[float], family: Optional[str],
) -> List[float]:
    """Each segment's own CONTRIBUTION to the stitched timeline, in seconds,
    in document order -- the axis every cumulative offset, keyframe `at` and
    audio `start`/`length` in this module is expressed against.

    For the `minimax_h3` family this is the window planner's actual
    emitted-frame timeline (`video_minimax_h3/windows.py`'s
    `resolve_window_geometry` -- the SAME snap-to-`17n+5` and
    overlap-frame-trim arithmetic `build_director_plan` runs at generation
    time, called here without a model loaded), not a raw `frames / fps` sum:
    a continuation window's leading `overlap_frames` replay the previous
    window's tail rather than adding new footage, and a requested frame count
    that isn't already on the VAE's lattice is snapped up before it ever
    reaches seconds. Every other family falls back to the legacy raw-frame
    axis (`_segment_duration`) -- see the module-level `_MINIMAX_H3_FAMILY`
    comment for why that isn't yet wrong to leave alone for Wan.
    """
    if family == _MINIMAX_H3_FAMILY and isinstance(fps, (int, float)) and fps > 0:
        return [geometry.emitted_frames / fps for geometry in resolve_window_geometry(segments, settings)]
    return [_segment_duration(segment, fps) for segment in segments]


def compile_shot_plan(
    normalized_doc: Dict[str, Any], shot_ids: List[str], *, family: Optional[str] = None,
) -> Dict[str, Any]:
    """Cut `normalized_doc` (the return value of `normalize_video_director`,
    already validated against a chain-style/`segment_routing` preset) down to
    the contiguous span of segments named by `shot_ids`, with every
    position-dependent derived value baked onto each surviving segment
    explicitly. Returns a NEW dict shaped exactly like `normalized_doc` --
    every pipeline/pipe that reads `form.video_director.*` sees a document
    indistinguishable from one that was always just this span.

    `family` is the orchestrator's own read of the target preset's
    `video_director.family` capability (the SAME capabilities block
    `normalize_video_director` validated the document against) -- it selects
    which family's own window-planner arithmetic derives the stitched
    timeline every free-floating keyframe/audio offset in this document is
    rebased against (see `_effective_segment_durations`). `None` (the
    default) keeps the legacy raw `frames / fps` axis, correct for every
    family that doesn't snap frame counts or trim continuation overlap off
    the FRONT of a window's decoded output.

    Raises `VideoDirectorValidationError` when:
    - `shot_ids` is empty, or names an id the document has no segment for
      (`_normalize_render` already checks this against the WHOLE film's ids
      before this ever runs, but a caller compiling directly is checked too);
    - the selected ids are not a single contiguous run of the film in segment
      order (never silently reordered or filtered down to a subset);
    - the span's first segment resolves to the `"chain"` sub_type -- it
      continues the previous segment's tail frames, but that segment sits
      outside the span and so has nothing to hand off ("Generate previous +
      this shot" is exactly the workaround: submit the contiguous span
      starting at the nearest fresh cut instead).
    """
    segments = normalized_doc.get("segments") or []
    index_by_id = {segment.get("id"): index for index, segment in enumerate(segments)}

    if not shot_ids:
        raise VideoDirectorValidationError(["render.shot_ids: at least one shot id is required"])

    unknown_ids = [shot_id for shot_id in shot_ids if shot_id not in index_by_id]
    if unknown_ids:
        raise VideoDirectorValidationError([f"render.shot_ids: unknown segment id(s) {unknown_ids!r}"])

    indices = sorted(index_by_id[shot_id] for shot_id in shot_ids)
    start_index, end_index = indices[0], indices[-1]
    span_width = end_index - start_index + 1
    if len(set(indices)) != len(indices) or span_width != len(indices):
        raise VideoDirectorValidationError([
            "render.shot_ids must name a single contiguous run of the film's shots -- "
            "compiling a selection with a gap in it would silently filter the film instead of "
            "rendering the span asked for"
        ])

    span_segments = segments[start_index:end_index + 1]

    first_segment = span_segments[0]
    if first_segment.get("sub_type") == "chain":
        raise VideoDirectorValidationError([
            f"shot {start_index + 1} needs its previous shot -- it continues the previous segment's "
            "tail frames, but that segment is outside this render's span. Submit the contiguous span "
            "from the nearest fresh cut instead (\"Generate previous + this shot\"), or convert this "
            "shot to a fresh cut first."
        ])

    settings = normalized_doc.get("settings") or {}
    fps = settings.get("fps")
    base_seed = settings.get("seed")
    base_seed = base_seed if isinstance(base_seed, int) else 0

    span_ids = {segment.get("id") for segment in span_segments}

    compiled_segments: List[Dict[str, Any]] = []
    for original_index in range(start_index, end_index + 1):
        segment = segments[original_index]
        compiled_segment = dict(segment)
        # Bake the seed every pipe would have derived for this segment AT
        # ITS ORIGINAL POSITION in the full film -- once the compiled list is
        # handed to the pipe as a shorter list, `segment.get("seed") or
        # base_seed + i` (chain_video_wan22/main.py, windows.py) would
        # otherwise roll a DIFFERENT seed from this span's own (smaller) `i`.
        # An already-explicit per-segment seed (a user override) is left
        # untouched either way.
        seed = segment.get("seed")
        compiled_segment["seed"] = seed if isinstance(seed, int) else base_seed + original_index
        # `sub_type` was already resolved (never re-derived here) by
        # `derive_segment_routing` inside `normalize_video_director` --
        # carried through unchanged so a segment mid-span that happens to
        # look like a fresh segment-0 at its NEW local position never gets
        # relabeled.
        # `reference_indices` was already resolved per-segment by
        # `_normalize_segment_references` -- `None` (whole pool) stays
        # `None`, an explicit subset stays exactly that subset; neither is
        # touched here.
        compiled_segment["sequence_index"] = original_index
        compiled_segments.append(compiled_segment)

    # Cumulative start time (seconds) of every segment in the FULL document --
    # the same "chain's total duration" axis a free-floating (`anywhere`)
    # keyframe's `at` and an audio track's `start` are expressed against
    # (normalize.py's `chain_keyframes_anywhere`; windows.py's `_locate_frame`
    # walks this same axis one cumulative window at a time). Each segment's
    # own width on that axis is what it actually CONTRIBUTES to the stitched
    # result -- for `minimax_h3` that is the window planner's emitted-frame
    # duration (aligned frames minus the overlap trimmed off a continuation
    # window's front), never a raw `frames / fps` sum -- see
    # `_effective_segment_durations`.
    effective_durations = _effective_segment_durations(segments, settings, fps, family)
    cumulative_starts: List[float] = []
    running = 0.0
    for duration in effective_durations:
        cumulative_starts.append(running)
        running += duration
    span_start = cumulative_starts[start_index]
    span_duration = sum(effective_durations[start_index:end_index + 1])
    span_end = span_start + span_duration
    # Whether this span reaches the film's own last segment -- the ONE case
    # where the family's endpoint-ownership rule (below) can apply, since
    # every other span's tail hands off to a segment that still exists and
    # still owns that time.
    is_final_span = end_index == len(segments) - 1

    compiled_media: List[Dict[str, Any]] = []
    for item in normalized_doc.get("media") or []:
        role = item.get("role")
        if role in ("first", "last"):
            # Per-segment edge media is tied to a segment_id, not to film
            # time -- keep it exactly as-is when its owning segment survived
            # into the span, drop it otherwise (its segment is gone).
            if item.get("segment_id") in span_ids:
                compiled_media.append(dict(item))
            continue

        # role == "keyframe": a free-floating image anywhere along the
        # chain's timeline (only reachable when the preset declares
        # `keyframes: "anywhere"` -- normalize.py rejects it otherwise).
        # Rebase its `at` onto the span's own local time, or drop it
        # outright when it lands on a shot this render doesn't cover.
        at = item.get("at")
        if not isinstance(at, (int, float)) or at < span_start:
            continue
        if at >= span_end:
            # `minimax_h3`'s own full-plan rule (windows.py's `_locate_frame`)
            # does NOT hand a keyframe past the last window's own emitted
            # frames to a window that doesn't exist -- it clamps to the LAST
            # window's own last decoded frame instead (normalize.py already
            # allows `at` up to the film's raw, un-effective duration, which
            # can overshoot the real emitted total once alignment/overlap are
            # accounted for). That ownership only transfers to THIS span when
            # it's the one ending the film; every other span's overshoot
            # still belongs to the segment right after it, so it's dropped as
            # before. Kept UNCLAMPED (not pinned to any specific value): any
            # `at - span_start` here is already `>= span_duration` by
            # construction, which is all `_locate_frame` needs to re-derive
            # the identical clamp once this compiled document is re-planned.
            if not (family == _MINIMAX_H3_FAMILY and is_final_span):
                continue
        rebased_item = dict(item)
        rebased_item["at"] = at - span_start
        compiled_media.append(rebased_item)

    compiled_audio: List[Dict[str, Any]] = []
    for item in normalized_doc.get("audio") or []:
        start = item.get("start") or 0.0
        length = item.get("length") or 0.0
        clip_end = start + length
        if clip_end <= span_start or start >= span_end:
            continue  # entirely outside the span

        # A track that started before the span had already played
        # `skipped` seconds of itself by the time the span begins -- carry
        # that forward onto `trim_start` on top of whatever trim it already
        # had, and clamp both ends of the track to the span's own window.
        skipped = max(0.0, span_start - start)
        rebased_item = dict(item)
        rebased_item["start"] = max(start, span_start) - span_start
        rebased_item["trim_start"] = (item.get("trim_start") or 0.0) + skipped
        rebased_item["length"] = min(clip_end, span_end) - max(start, span_start)
        compiled_audio.append(rebased_item)

    ic_lora = normalized_doc.get("ic_lora") or []

    needs_t2v_set = False
    needs_i2v_set = False
    for segment in compiled_segments:
        if wan_model_set_for(segment.get("sub_type")) == "i2v":
            needs_i2v_set = True
        else:
            needs_t2v_set = True

    out: Dict[str, Any] = dict(normalized_doc)
    out.pop("render", None)
    out["segments"] = compiled_segments
    out["media"] = compiled_media
    out["audio"] = compiled_audio
    out["settings"] = {**settings, "duration": span_duration}
    out["needs_t2v_set"] = needs_t2v_set
    out["needs_i2v_set"] = needs_i2v_set
    out.update(derive_ltx_media_fields(compiled_media, ic_lora, fps))

    if len(span_segments) == 1:
        out["shot"] = {
            "id": span_segments[0].get("id"),
            "index": start_index,
            "count": len(segments),
        }
    else:
        # A multi-segment span ("Generate previous + this shot") is a
        # continuous render, not a single owned shot -- no `shot`
        # provenance key names ONE of the segments it covers.
        out.pop("shot", None)

    return out
