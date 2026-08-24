"""
Validation + canonicalization for the "Video Director" wire document.

The frontend submits `form_data.video_director` as a free-form JSON document
describing one of several composition modes (t2v/i2v/flf/director). The
"director" mode is capability-shaped, not name-shaped: a preset that declares
`segment_routing` runs it as a routed multi-segment chain (Wan -- per-segment
frames/loras/sub-types), a preset that does not runs it as one keyframe/audio
timeline generation (LTX). This module is the single gate between that document
and the pipes that consume it:
it never talks to models or the filesystem beyond resolving media paths, and it
never knows about specific model families - what's allowed in a given mode
comes entirely from the `capabilities` dict derived from the preset's
`vars.video_director`.

Like `src/features/prompt/expander.py`, this is intentionally a bag of plain
functions rather than a class: there is no state to carry between calls, and a
validator gains nothing from being an object.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.platform.util.latents import generate_seed
from src.platform.util.path_resolution import apply_preset_mode_overlay, resolve_media_ref as _resolve_media_ref

logger = logging.getLogger(__name__)

_MEDIA_ROLES = {"first", "last", "keyframe"}
_CONTINUATION_SOURCES = {"tail_frames", "last_frame"}
# How a family's pipes consume an audio track: `condition` feeds it into the
# generation, `mux` lays it onto the finished video track. The normalizer only
# validates the value; which of the two a preset can actually honour is the
# pipe's business (LTX muxes, a native audio-conditioned family conditions).
_AUDIO_ROLES = {"condition", "mux"}

_SUB_TYPES = ("t2v", "i2v", "flf", "chain")
# Which loaded checkpoint SET each resolved sub-type draws from. t2v shots run
# on the plain (16-channel) t2v experts; every image-conditioned shot -- a fresh
# start image (i2v), a start+end pair (flf), or a continuation of the previous
# segment's tail frames (chain) -- runs on the concat-i2v (36-channel) experts.
_I2V_SUB_TYPES = frozenset({"i2v", "flf", "chain"})

# Wan's chain generators (generator/{txt2vid_wan22,img2vid_wan22,
# chain_video_wan22}/main.py) cap PipeConfigSpec("frames", ...) at 257.
_CHAIN_MAX_FRAMES_HARD_CAP = 257
_CHAIN_STEPS_RANGE = (1, 150)
_CHAIN_CFG_RANGE = (0, 30)
# Every native video generator's PipeConfigSpec("fps", ...) caps at 1.0-60.0 --
# generator/{video_ltx,txt2vid_ltx,txt2vid_wan22,img2vid_wan22,
# chain_video_wan22}/main.py all agree, so this bound is safe to enforce
# regardless of preset/mode. The frame-COUNT ceiling is not: it differs per
# family (LTX 1001 vs Wan's 257 above), so it isn't hardcoded here -- a preset
# opts a non-chain mode into that check by declaring `limits.max_frames` (see
# content/presets/marketplace/LTX-2/preset.yml, mirroring generator/video_ltx +
# generator/txt2vid_ltx's PipeConfigSpec("frames", ..., max_value=1001)).
_FPS_RANGE = (1.0, 60.0)
# Mirrors the LTX causal VAE's temporal chunking (a valid frame count is
# 1 + k*8 -- see _TEMPORAL_DOWNSCALE / snap_frame_count in
# generator/txt2vid_ltx/main.py). Only exercised once `limits.max_frames`
# triggers the frame-count check below, so it never applies to Wan.
_LTX_TEMPORAL_DOWNSCALE = 8


class VideoDirectorValidationError(ValueError):
    """Raised with every problem found in a document, not just the first."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))

    def __str__(self) -> str:
        return "; ".join(self.errors)


def derive_segment_sub_type(
    *, index: int, has_first_media: bool, has_last_media: bool, override: Optional[str]
) -> str:
    """Resolve one Director segment to its generation sub-type -- the SINGLE
    source of truth the pipeline, the pipes and the frontend all mirror.

    Contract (deterministic, so the frontend can display the same per-segment
    badge it would render without asking the server):

      1. an explicit ``override`` (segment ``sub_type``, one of t2v/i2v/flf/chain)
         always wins -- this is how a prompt-only later segment is forced to a
         fresh cut instead of continuing the previous one;
      2. else a segment carrying both a start and an end image is ``flf``;
      3. else a segment carrying a start image is ``i2v``;
      4. else the FIRST segment (index 0), prompt-only, is a fresh ``t2v`` shot;
      5. else a prompt-only LATER segment defaults to ``chain`` -- a continuation
         of the previous segment's tail frames.
    """
    if override in _SUB_TYPES:
        return override
    if has_first_media and has_last_media:
        return "flf"
    if has_first_media:
        return "i2v"
    if index == 0:
        return "t2v"
    return "chain"


def wan_model_set_for(sub_type: str) -> str:
    """Map a resolved sub-type to the checkpoint set that runs it: ``"i2v"`` for
    the concat-conditioned sub-types (i2v/flf/chain), ``"t2v"`` otherwise."""
    return "i2v" if sub_type in _I2V_SUB_TYPES else "t2v"


def derive_segment_routing(
    segments: List[Dict[str, Any]], media: List[Dict[str, Any]], *, continuation_disabled: bool = False,
) -> Dict[str, Any]:
    """Attach a resolved ``sub_type`` to every segment (in place) and return the
    document-level ``{needs_t2v_set, needs_i2v_set}`` flags the pipeline's
    conditional model-set loading branches on. ``segments[i]["sub_type"]`` is
    read as the per-segment override before deriving (see
    :func:`derive_segment_sub_type`).

    ``continuation_disabled`` coerces a DERIVED (un-overridden) ``"chain"``
    result to ``"t2v"`` -- a hard cut instead of a continuation. This is the
    silent half of the references/continuation incompatibility (see
    `normalize_video_director`'s ``chain_continuation_disabled``): a
    prompt-only later segment that would normally continue the previous one
    simply becomes an independent shot instead. An EXPLICIT ``sub_type:
    "chain"`` override under the same capability is rejected outright before
    this ever runs (`_normalize_segments`) -- this function only ever sees
    documents that already passed that check.
    """
    first_by_segment = {m.get("segment_id") for m in media if m.get("role") == "first"}
    last_by_segment = {m.get("segment_id") for m in media if m.get("role") == "last"}

    needs_t2v = needs_i2v = False
    for index, segment in enumerate(segments):
        seg_id = segment.get("id")
        sub_type = derive_segment_sub_type(
            index=index,
            has_first_media=seg_id in first_by_segment,
            has_last_media=seg_id in last_by_segment,
            override=segment.get("sub_type"),
        )
        if continuation_disabled and sub_type == "chain":
            sub_type = "t2v"
        segment["sub_type"] = sub_type
        if wan_model_set_for(sub_type) == "i2v":
            needs_i2v = True
        else:
            needs_t2v = True

    return {"needs_t2v_set": needs_t2v, "needs_i2v_set": needs_i2v}


def normalize_video_director(
    document: Dict[str, Any],
    capabilities: Dict[str, Any],
    storage_dir: str,
    form_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Validate `document` against `capabilities` and return a NEW canonical dict.

    `capabilities` is expected to already be the EFFECTIVE, post-overlay set
    (see `apply_preset_mode_overlay`) -- this function does not apply an
    overlay itself, it only reads the merged result. `form_data` is the
    submitting user's bound form values, needed only to resolve a segment's
    `references[].form_media` selections (see `_resolve_reference_entry`);
    every other document shape ignores it.

    Raises `VideoDirectorValidationError` carrying every error found. Never
    mutates `document`; unknown top-level keys are preserved verbatim, unknown
    keys inside known structures are dropped.
    """
    errors: List[str] = []
    capabilities = capabilities or {}
    form_data = form_data or {}
    modes = capabilities.get("modes") or {}
    limits = capabilities.get("limits") or {}
    routing_enabled = bool(capabilities.get("segment_routing"))
    references_capability = capabilities.get("references")
    reference_fields = capabilities.get("reference_fields") or []
    reference_pool = _packed_reference_pool(reference_fields, form_data)
    storage_path = Path(storage_dir).resolve()

    out: Dict[str, Any] = {
        key: value
        for key, value in document.items()
        if key not in {"schema_version", "mode", "settings", "segments", "media", "audio", "ic_lora"}
    }

    schema_version = document.get("schema_version")
    if schema_version is None:
        errors.append("missing schema_version")
    elif schema_version != 1:
        errors.append(
            f"unsupported schema_version {schema_version!r}: this server only understands schema_version 1 "
            "(the document was produced by a newer client than this server supports)"
        )

    mode = document.get("mode")
    # Lenient read of pre-director stored documents: the retired Wan "chain"
    # sub-mode is the routed "director" sub-mode now. Remap only when this
    # preset exposes a routed director mode; the canonical output always
    # carries the new mode string, never "chain".
    if mode == "chain" and "chain" not in modes and "director" in modes and routing_enabled:
        mode = "director"
    if mode not in modes:
        allowed = sorted(modes.keys())
        errors.append(f"unsupported mode {mode!r}: allowed modes are {allowed}")
        # Nothing else can be meaningfully validated without a known mode.
        raise VideoDirectorValidationError(errors)

    mode_caps = modes.get(mode) or {}
    # The "director" sub-mode's shape comes from capabilities, not its name: a
    # preset declaring segment_routing runs it as a routed multi-segment chain
    # (Wan), one that doesn't as a single keyframe/audio timeline generation
    # (LTX). Every branch below keys off these two flags, never the mode string.
    chain_style = mode == "director" and routing_enabled
    timeline_style = mode == "director" and not routing_enabled
    out["schema_version"] = 1
    out["mode"] = mode

    # A chain style whose mode declares `continuation` EXPLICITLY as `null`
    # (the key present, not merely absent -- see apply_preset_mode_overlay)
    # cannot combine continuation with whatever this mode is doing instead
    # (MiniMax-H3's `refs` mode: continuation's condition-row overlay has no
    # layout that also carries a reference-conditioned prefix). Absence of
    # the key changes nothing -- most chain presets never declare it at all
    # and keep their normal continuation behaviour.
    chain_continuation_disabled = (
        chain_style and "continuation" in mode_caps and mode_caps.get("continuation") is None
    )

    settings, duration = _normalize_settings(
        document.get("settings") or {}, chain_style, limits, mode_caps, errors,
        continuation_disabled=chain_continuation_disabled,
    )
    out["settings"] = settings

    segments = _normalize_segments(
        document.get("segments") or [], chain_style, timeline_style, mode_caps, duration, errors, routing_enabled,
        references_capability=references_capability,
        reference_fields=reference_fields,
        reference_pool=reference_pool,
        form_data=form_data,
        storage_path=storage_path,
        continuation_disabled=chain_continuation_disabled,
    )
    out["segments"] = segments
    segment_ids = {segment["id"] for segment in segments}

    media = _normalize_media(
        document.get("media") or [], mode, chain_style, timeline_style, mode_caps, duration,
        settings.get("fps"), segments, segment_ids, storage_path, errors,
        i2v_declared="i2v" in modes, flf_declared="flf" in modes,
    )
    out["media"] = media

    out["audio"] = _normalize_audio(document.get("audio") or [], mode_caps, storage_path, errors)
    out["ic_lora"] = _normalize_ic_lora(document.get("ic_lora") or [], timeline_style, mode_caps, storage_path, errors)

    if errors:
        raise VideoDirectorValidationError(errors)

    if routing_enabled:
        out.update(derive_segment_routing(
            out["segments"], out["media"], continuation_disabled=chain_continuation_disabled,
        ))

    out.update(derive_ltx_media_fields(out["media"], out["ic_lora"], out["settings"].get("fps")))

    return out


def round_half_up_common(value: float) -> int:
    """Match Jinja's default `round` filter (`method='common'`): halves round
    away from zero, unlike Python's banker's-rounding builtin `round()`."""
    import math

    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def _media_ref_is_image(media_ref: Optional[Dict[str, Any]]) -> bool:
    """A resolved `{path, type, ...}` media reference is image-typed unless it
    explicitly says `type: "video"` (the default when `type` is absent, per
    the wire document's MediaRef contract)."""
    return ((media_ref or {}).get("type") or "image") != "video"


def derive_ltx_media_fields(
    media: List[Dict[str, Any]], ic_lora: List[Dict[str, Any]], fps: Any
) -> Dict[str, Any]:
    """Precompute the three lists the native LTX-2 pipeline
    (`content/presets/marketplace/LTX-2/standard/modes/video/pipeline.yml`) needs but can no
    longer build itself: the strict/native template evaluator requires
    `@loop`'s `items:` to already BE a list, so the imperative
    `{% set ns = namespace() %}{% for %}...{{ ns.items | tojson }}` idiom that
    used to build one at render time can't run anymore (the boundary owns
    materialization, the YAML stays declarative).

    `media_images`: ordered list of resolved paths for image-typed 'first',
    then image-typed 'last', then image-typed 'keyframe' entries sorted by
    `at` (stable sort keeps original order for ties), then image-typed
    `ic_lora[].reference` entries (document order). Video-typed first/last/
    keyframe entries are the v1 cut documented in the pipeline header comment
    -- dropped here, not loaded, not placed.

    `media_videos`: ordered list of resolved paths for video-typed
    `ic_lora[].reference` entries (document order) -- IC-LoRA reference clips
    only; nothing else feeds this loader.

    `media_placements`: the SAME traversals (so each `index` stays aligned
    with the load order of whichever of `media_images`/`media_videos` its
    `source` names), each entry pre-shaped to `generator/video_ltx`'s
    `media_placements` config (`source`/`index`/`frame`/`strength`/`role`).
    Every `ic_lora` entry carrying a `reference` gets one `role: "reference"`
    placement, routed by ITS OWN media type -- an image reference is a
    `source: "image"` placement indexing into `media_images` (appended after
    the keyframe entries above); a video reference is a `source: "video"`
    placement indexing into `media_videos`. This is what makes an
    image-typed IC-LoRA reference work at all: the video loader
    (`_load_video_frames`, cv2-backed) cannot read a still image file, so a
    reference clip MUST be routed to the loader that matches its actual
    media type rather than being hardcoded to "video".
    """

    firsts = [m for m in media if m.get("role") == "first" and _media_ref_is_image(m.get("media"))]
    lasts = [m for m in media if m.get("role") == "last" and _media_ref_is_image(m.get("media"))]
    keyframes = sorted(
        (m for m in media if m.get("role") == "keyframe" and _media_ref_is_image(m.get("media"))),
        key=lambda m: m.get("at") or 0,
    )

    media_images = [m["media"]["path"] for m in firsts + lasts + keyframes]

    placements: List[Dict[str, Any]] = []
    index = 0
    for m in firsts:
        placements.append({"source": "image", "index": index, "frame": "first", "strength": m.get("strength", 1.0), "role": "keyframe"})
        index += 1
    for m in lasts:
        placements.append({"source": "image", "index": index, "frame": "last", "strength": m.get("strength", 1.0), "role": "keyframe"})
        index += 1
    for m in keyframes:
        frame = round_half_up_common((m.get("at") or 0) * (fps or 0))
        placements.append({"source": "image", "index": index, "frame": frame, "strength": m.get("strength", 1.0), "role": "keyframe"})
        index += 1

    reference_clips = [ic for ic in ic_lora if ic.get("reference")]
    media_videos: List[str] = []
    for ic in reference_clips:
        reference = ic["reference"]
        strength = ic.get("strength", 1.0)
        if _media_ref_is_image(reference):
            media_images.append(reference["path"])
            placements.append({"source": "image", "index": index, "frame": "first", "strength": strength, "role": "reference"})
            index += 1
        else:
            placements.append({"source": "video", "index": len(media_videos), "frame": "first", "strength": strength, "role": "reference"})
            media_videos.append(reference["path"])

    return {"media_images": media_images, "media_videos": media_videos, "media_placements": placements}


def _normalize_settings(
    settings: Dict[str, Any],
    chain_style: bool,
    limits: Dict[str, Any],
    mode_caps: Dict[str, Any],
    errors: List[str],
    *,
    continuation_disabled: bool = False,
) -> tuple:
    out: Dict[str, Any] = {}

    fps = settings.get("fps")
    if fps is None:
        fps = limits.get("default_fps", 24)
    fps_valid = isinstance(fps, (int, float)) and (_FPS_RANGE[0] <= fps <= _FPS_RANGE[1])
    if not fps_valid:
        errors.append(f"settings.fps must be between {_FPS_RANGE[0]} and {_FPS_RANGE[1]}, got {fps!r}")
    out["fps"] = fps

    duration = settings.get("duration")
    # A chain-style director derives its total duration from per-segment frame
    # counts, so its top-level duration is advisory (and may be absent); every
    # other shape validates a single clip duration against the preset's cap,
    # and (below) against the generator's real frame-count ceiling.
    if not chain_style:
        if duration is None:
            duration = limits.get("default_duration", 5.0)
        max_duration = limits.get("max_duration")
        duration_valid = isinstance(duration, (int, float)) and duration > 0
        if not duration_valid:
            errors.append(f"settings.duration must be > 0, got {duration!r}")
        elif max_duration is not None and duration > max_duration:
            errors.append(f"settings.duration {duration} exceeds the allowed maximum {max_duration}")
            duration_valid = False

        max_frames = limits.get("max_frames")
        if duration_valid and fps_valid and max_frames is not None:
            raw_frames = round_half_up_common(duration * fps)
            if raw_frames > max_frames:
                max_seconds = max_frames / fps
                errors.append(
                    f"settings.duration {duration} at settings.fps {fps} needs {raw_frames} frames, "
                    f"exceeding this preset's generator cap of {max_frames} frames "
                    f"(use a duration of {max_seconds:.2f}s or less at this fps, or lower fps)"
                )
            else:
                # The generator itself snaps to this same 1 + k*8 lattice
                # (with a log warning) before it ever runs -- computing it
                # here lets the caller show the ACTUAL frame count/duration
                # the generation will use, before submitting. Imported at the
                # use site: the module rides the boot import chain and
                # src.platform.runtime.native's package init pulls torch.
                from src.platform.runtime.native.resolution import snap_frame_count

                frame_count = snap_frame_count(raw_frames, _LTX_TEMPORAL_DOWNSCALE)
                out["frame_count"] = frame_count
                out["effective_duration"] = frame_count / fps
    out["duration"] = duration

    out["resolution"] = settings.get("resolution") or ""

    seed = settings.get("seed")
    if seed is None or seed == -1:
        seed = generate_seed(-1)
    out["seed"] = seed

    continuation = settings.get("continuation")
    if continuation is not None and continuation_disabled:
        errors.append(
            "settings.continuation: this preset's current mode has no layout that combines a "
            "reference-conditioned prefix with continuation's own condition-row overlay -- drop "
            "settings.continuation (every shot becomes an independent cut) or switch out of this mode"
        )
        out["continuation"] = None
    elif continuation is not None:
        if not isinstance(continuation, dict):
            errors.append("settings.continuation must be an object")
        else:
            source = continuation.get("source")
            if source is not None and source not in _CONTINUATION_SOURCES:
                errors.append(f"settings.continuation.source must be one of {sorted(_CONTINUATION_SOURCES)}, got {source!r}")
            overlap_frames = continuation.get("overlap_frames", 0)
            max_overlap_frames = mode_caps.get("max_overlap_frames")
            if not isinstance(overlap_frames, int) or overlap_frames < 0:
                errors.append(f"settings.continuation.overlap_frames must be a non-negative int, got {overlap_frames!r}")
            elif max_overlap_frames is not None and overlap_frames > max_overlap_frames:
                errors.append(
                    f"settings.continuation.overlap_frames {overlap_frames} exceeds this mode's "
                    f"max_overlap_frames {max_overlap_frames}"
                )
            out["continuation"] = {
                "source": source,
                "overlap_frames": overlap_frames,
                "stitch": bool(continuation.get("stitch", True)),
            }
    else:
        out["continuation"] = None

    return out, out["duration"]


def _normalize_loras(loras: Any, allow: bool, context: str, errors: List[str]) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    if loras is None:
        return None
    if not allow:
        errors.append(f"{context}: loras are not supported by this preset")
        return None
    if not isinstance(loras, dict):
        errors.append(f"{context}: loras must be an object with 'high'/'low' lists")
        return None

    out: Dict[str, List[Dict[str, Any]]] = {}
    for bucket in ("high", "low"):
        items = loras.get(bucket) or []
        if not isinstance(items, list):
            errors.append(f"{context}: loras.{bucket} must be a list")
            continue
        normalized_items = []
        for i, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("model"):
                errors.append(f"{context}: loras.{bucket}[{i}] requires a 'model'")
                continue
            strength = item.get("strength", 1.0)
            if not isinstance(strength, (int, float)):
                errors.append(f"{context}: loras.{bucket}[{i}].strength must be numeric")
                strength = 1.0
            normalized_items.append({"model": item["model"], "strength": max(0.0, min(4.0, float(strength)))})
        out[bucket] = normalized_items
    return out


def _normalize_segments(
    segments: List[Dict[str, Any]],
    chain_style: bool,
    timeline_style: bool,
    mode_caps: Dict[str, Any],
    duration: Any,
    errors: List[str],
    routing_enabled: bool = False,
    *,
    references_capability: Optional[str] = None,
    reference_fields: Optional[List[str]] = None,
    reference_pool: Optional[List[Dict[str, Any]]] = None,
    form_data: Optional[Dict[str, Any]] = None,
    storage_path: Optional[Path] = None,
    continuation_disabled: bool = False,
) -> List[Dict[str, Any]]:
    reference_fields = reference_fields or []
    reference_pool = reference_pool or []
    form_data = form_data or {}
    if not segments:
        errors.append("segments: at least one segment is required")
        return []

    is_chain = chain_style
    is_director = timeline_style
    max_segments = mode_caps.get("max_segments", 8) if is_chain else None
    max_frames_per_segment = mode_caps.get("max_frames_per_segment") if is_chain else None
    per_segment_loras = bool(mode_caps.get("per_segment_loras")) if is_chain else False

    if is_chain and max_segments is not None and len(segments) > max_segments:
        errors.append(f"segments: chain mode allows at most {max_segments} segments, got {len(segments)}")

    out: List[Dict[str, Any]] = []
    for i, segment in enumerate(segments):
        context = f"segments[{i}]"
        if not isinstance(segment, dict):
            errors.append(f"{context}: must be an object")
            continue

        segment_id = segment.get("id") or f"seg-{i}"
        normalized: Dict[str, Any] = {
            "id": segment_id,
            "prompt": segment.get("prompt") or "",
            "negative_prompt": segment.get("negative_prompt") or "",
            "start": segment.get("start"),
            "end": segment.get("end"),
            "frames": segment.get("frames"),
            "seed": segment.get("seed"),
            "steps": segment.get("steps"),
            "cfg": segment.get("cfg"),
            "loras": segment.get("loras"),
        }

        if routing_enabled:
            # Per-segment sub-type OVERRIDE only; the final resolved value is
            # filled by derive_segment_routing() once media is known. Preserve
            # None here so the derivation runs for un-overridden segments.
            override = segment.get("sub_type")
            if override is not None and override not in _SUB_TYPES:
                errors.append(f"{context}: sub_type must be one of {list(_SUB_TYPES)}, got {override!r}")
                override = None
            elif continuation_disabled and override == "chain":
                errors.append(
                    f"{context}: continues the previous window (sub_type 'chain'), but this preset's "
                    "current mode has no layout that combines a reference-conditioned prefix with "
                    "continuation's own condition-row overlay -- make this segment a cut (drop 'chain') "
                    "or switch out of this mode"
                )
            normalized["sub_type"] = override

        if is_director:
            start, end = segment.get("start"), segment.get("end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                errors.append(f"{context}: start/end are required numbers in director mode")
            elif not (0 <= start < end):
                errors.append(f"{context}: start must be >= 0 and < end, got start={start} end={end}")
            elif isinstance(duration, (int, float)) and end > duration:
                errors.append(f"{context}: end {end} exceeds settings.duration {duration}")

        if is_chain:
            frames = segment.get("frames")
            # A mode that declares max_frames_per_segment owns its per-segment
            # ceiling outright (H3 windows legitimately run past Wan's 257);
            # the Wan-derived hard cap only bounds modes that declare nothing.
            frames_cap = max_frames_per_segment if max_frames_per_segment is not None else _CHAIN_MAX_FRAMES_HARD_CAP
            if not isinstance(frames, int) or not (1 <= frames <= frames_cap):
                errors.append(f"{context}: frames must be an int between 1 and {frames_cap}, got {frames!r}")

            steps = segment.get("steps")
            if steps is not None and (not isinstance(steps, int) or not (_CHAIN_STEPS_RANGE[0] <= steps <= _CHAIN_STEPS_RANGE[1])):
                errors.append(f"{context}: steps must be between {_CHAIN_STEPS_RANGE[0]} and {_CHAIN_STEPS_RANGE[1]}, got {steps!r}")

            cfg = segment.get("cfg")
            if cfg is not None and (not isinstance(cfg, (int, float)) or not (_CHAIN_CFG_RANGE[0] <= cfg <= _CHAIN_CFG_RANGE[1])):
                errors.append(f"{context}: cfg must be between {_CHAIN_CFG_RANGE[0]} and {_CHAIN_CFG_RANGE[1]}, got {cfg!r}")

            normalized["loras"] = _normalize_loras(segment.get("loras"), per_segment_loras, context, errors)
        else:
            for field in ("loras", "frames", "steps", "cfg"):
                if segment.get(field) is not None:
                    errors.append(f"{context}: '{field}' is only valid in a segment-chained director mode")
            normalized["loras"] = None
            normalized["frames"] = None
            normalized["steps"] = None
            normalized["cfg"] = None

        normalized["references"], normalized["reference_indices"] = _normalize_segment_references(
            segment.get("references"), references_capability, reference_fields, reference_pool,
            form_data, storage_path, context, errors,
        )

        out.append(normalized)

    if is_director:
        # Sort by start; touching edges are fine, overlaps are not.
        sortable = [s for s in out if isinstance(s.get("start"), (int, float))]
        sortable.sort(key=lambda s: s["start"])
        previous_end = None
        for s in sortable:
            if previous_end is not None and s["start"] < previous_end:
                errors.append(f"segments: '{s['id']}' overlaps the previous segment (starts at {s['start']} before {previous_end} ends)")
            previous_end = s.get("end", previous_end)
        unsortable = [s for s in out if not isinstance(s.get("start"), (int, float))]
        out = sortable + unsortable

    return out


def _field_pool_items(field: str, form_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The media item(s) sitting on `form_data[field]` -- a single field's
    value, or every entry of a `multiple` field's array. Mirrors the chat
    tool's `_form_media_items` (`video_director_tool.py`) -- reimplemented
    here since this module cannot import the LLM tools package."""
    raw = form_data.get(field)
    items = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
    return [i for i in items if isinstance(i, dict) and (i.get("path") or i.get("relative_path"))]


def _packed_reference_pool(reference_fields: List[str], form_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The whole-film reference pool in PACKED order -- every item of the
    first `reference_fields` field, then the second, etc, each field's own
    item order preserved. This is the exact order/numbering MiniMax-H3's
    generator packs and labels its reference rows in
    (`content/presets/marketplace/MiniMax-H3/modes/refs/tabs/references.yml`: images, then
    videos, then audio; `reference_order.pack_references` in the pipe), so a
    segment's `reference_indices` (see `_normalize_segment_references`)
    indexes into exactly this list."""
    pool: List[Dict[str, Any]] = []
    for field in reference_fields:
        pool.extend(_field_pool_items(field, form_data))
    return pool


def _pool_index_of(item: Dict[str, Any], pool: List[Dict[str, Any]]) -> Optional[int]:
    """`item`'s position in the packed pool: identity match first (a
    `form_media`-resolved item IS one of the pool's own objects), falling
    back to a path/relative_path match (a direct `{path}` entry is a
    caller-constructed dict, never the same object as the pool's)."""
    for idx, candidate in enumerate(pool):
        if candidate is item:
            return idx
    target = item.get("path") or item.get("relative_path")
    if target is None:
        return None
    for idx, candidate in enumerate(pool):
        if (candidate.get("path") or candidate.get("relative_path")) == target:
            return idx
    return None


def _normalize_segment_references(
    raw_references: Any,
    references_capability: Optional[str],
    reference_fields: List[str],
    reference_pool: List[Dict[str, Any]],
    form_data: Dict[str, Any],
    storage_path: Optional[Path],
    context: str,
    errors: List[str],
) -> tuple:
    """A segment's `references` -- a per-shot SELECTION from the preset's
    whole-film reference pool (the pool itself lives on the form fields named
    in `capabilities.reference_fields`, never duplicated into the document) --
    and the derived `reference_indices` the generator actually consumes
    (`segment.get("reference_indices")`): each selected entry's position in
    the PACKED pool (`_packed_reference_pool`), deduplicated but in SELECTION
    order (never sorted -- a family's generator re-labels a subset in its
    presented order, e.g. MiniMax-H3's `<Picture i>` renumbering, so which
    entry came first is meaningful).

    `None`/absent `references` means "inherit the full pool" and returns
    `(None, None)` for both -- the generator's own convention for the same
    thing (`WindowPlan.reference_indices is None`). Gated by
    `capabilities.references` exactly like every other optional document
    shape: off (`None`) rejects the key outright; `"whole"` rejects it too --
    the pool is implicit for every segment, there is nothing per-segment to
    select; only `"per_shot"` accepts a selection. An explicitly EMPTY list
    is also rejected -- selecting zero references has no defined meaning
    (mirrors the generator's own `_validate_refs_director_plan` backstop).
    """
    if raw_references is None:
        return None, None

    if references_capability != "per_shot":
        reason = (
            "references are not supported by this preset" if references_capability is None
            else "this preset's references capability is 'whole' -- segments inherit the full reference "
            "pool and cannot select a subset (omit segments[].references)"
        )
        errors.append(f"{context}.references: {reason}")
        return None, None

    if not isinstance(raw_references, list):
        errors.append(f"{context}.references: must be a list")
        return None, None

    if not raw_references:
        errors.append(
            f"{context}.references: is empty -- omit the field to use every reference, or list at least "
            "one selection"
        )
        return None, None

    resolved: List[Dict[str, Any]] = []
    indices: List[int] = []
    seen_indices: set = set()
    for i, entry in enumerate(raw_references):
        result = _resolve_reference_entry(
            entry, reference_fields, reference_pool, form_data, storage_path, f"{context}.references[{i}]", errors,
        )
        if result is None:
            continue
        media_ref, pool_index = result
        resolved.append(media_ref)
        if pool_index not in seen_indices:
            seen_indices.add(pool_index)
            indices.append(pool_index)
    return resolved, indices


def _resolve_reference_entry(
    entry: Any,
    reference_fields: List[str],
    reference_pool: List[Dict[str, Any]],
    form_data: Dict[str, Any],
    storage_path: Optional[Path],
    context: str,
    errors: List[str],
) -> Optional[tuple]:
    """Resolve one `segments[].references[]` entry to `(media_ref, pool_index)`,
    or `None` on any failure (the error is appended to `errors`). An entry is
    either a direct `{path|relative_path}` media reference or a `{form_media:
    {field, label?|path?}}` pointer into one of the preset's `reference_fields`
    pool fields on the SUBMITTED form -- the same `field`+`label`/`path`
    addressing the chat tool's `upsert_media.form_media` uses
    (`src/features/llm/tools/builtin/video_director_tool.py`'s
    `_resolve_form_media`), reimplemented here against `form_data` rather than
    the live editor state, since this module cannot import the LLM tools
    package.

    Either shape MUST resolve to an item already sitting in the packed
    reference pool -- a `references` selection is a pick from the pool, not
    an escape hatch to condition on an arbitrary extra file the pool never
    embedded. An entry that resolves to a real, on-disk file but isn't part
    of the pool is still rejected, naming the pool.
    """
    if not isinstance(entry, dict):
        errors.append(f"{context}: must be an object")
        return None

    raw_path = entry.get("path") or entry.get("relative_path")
    form_media = entry.get("form_media")
    if raw_path and form_media is not None:
        errors.append(f"{context}: give either 'path'/'relative_path' or 'form_media', not both")
        return None

    pool_source: Optional[Dict[str, Any]] = None

    if form_media is not None:
        if not isinstance(form_media, dict):
            errors.append(f"{context}: 'form_media' must be an object")
            return None

        field = form_media.get("field")
        if not isinstance(field, str) or field not in reference_fields:
            errors.append(
                f"{context}: form_media.field must be one of this preset's reference_fields "
                f"{reference_fields}, got {field!r}"
            )
            return None

        label, path = form_media.get("label"), form_media.get("path")
        if (label is None) == (path is None):
            errors.append(f"{context}: form_media needs exactly one of 'label' or 'path'")
            return None

        items = _field_pool_items(field, form_data)
        if not items:
            errors.append(f"{context}: form field {field!r} has no media on it")
            return None

        if path is not None:
            match = next((i for i in items if i.get("path") == path or i.get("relative_path") == path), None)
            if match is None:
                errors.append(f"{context}: no item at path {path!r} on form field {field!r}")
                return None
        else:
            needle = label.strip().lower() if isinstance(label, str) else ""
            matches = [i for i in items if str(i.get("label") or i.get("name") or "").strip().lower() == needle]
            if len(matches) != 1:
                errors.append(
                    f"{context}: {'no' if not matches else 'ambiguous'} item on form field {field!r} labeled {label!r}"
                )
                return None
            match = matches[0]

        pool_source = match
    elif raw_path:
        pool_source = entry
    else:
        errors.append(f"{context}: provide either 'path'/'relative_path' or 'form_media'")
        return None

    pool_index = _pool_index_of(pool_source, reference_pool)
    if pool_index is None:
        errors.append(f"{context}: is not part of this preset's reference pool")
        return None

    media_ref = _resolve_media_ref(pool_source, storage_path, context, errors)
    if media_ref is None:
        return None
    return media_ref, pool_index


def _normalize_media(
    media: List[Dict[str, Any]],
    mode: str,
    chain_style: bool,
    timeline_style: bool,
    mode_caps: Dict[str, Any],
    duration: Any,
    fps: Any,
    segments: List[Dict[str, Any]],
    segment_ids: set,
    storage_path: Path,
    errors: List[str],
    *,
    i2v_declared: bool = False,
    flf_declared: bool = False,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_first: Dict[str, str] = {}
    seen_last: Dict[str, str] = {}
    keyframe_count = 0
    chain_keyframes_first_only = chain_style and mode_caps.get("keyframes") == "first_only"
    # A chain-style director places keyframes only when its preset says the
    # family can honour one anywhere along the chain, not just at the opening
    # shot ("first_only"). Its timeline is the concatenation of the segments, so
    # the window a keyframe's `at` falls in comes from the per-segment frame
    # counts rather than settings.duration (which chain style never validates).
    chain_keyframes_anywhere = chain_style and mode_caps.get("keyframes") == "anywhere"
    keyframes_allowed = timeline_style or chain_keyframes_anywhere
    max_keyframes = (mode_caps.get("max_keyframes", 8) if keyframes_allowed else None)
    # Per-segment index/dict, needed by the join-aware edge-role checks below
    # (chain style only -- a fresh open/close is a property of a segment's
    # RESOLVED sub_type, not its position in the list).
    segment_by_id = {s.get("id"): s for s in segments if isinstance(s, dict)}
    index_by_id = {s.get("id"): i for i, s in enumerate(segments) if isinstance(s, dict)}

    # Edge-role capability gate for a `director`-mode document (chain or
    # timeline): `keyframes_allowed` above IS freePlacementAllowed (docs/
    # video-director.md) -- a director that admits a keyframe anywhere along
    # its timeline trivially admits the two fixed positions too. `i2v`/`flf`
    # declared each independently grant a leading edge (the family already
    # conditions on a start image for those single-shot modes); only `flf`
    # grants a trailing edge (a chain segment resolving to the `flf` sub-type
    # needs both -- see derive_segment_sub_type). Only meaningful for
    # mode == "director": t2v/i2v/flf documents are governed by the
    # mode-specific count/role rules above and below instead, and reaching
    # THIS mode at all already implies the matching capability is declared.
    #
    # This is a DOCUMENT-level "is the role admitted AT ALL" gate -- which
    # SEGMENT may actually carry one, for chain style, is a join-aware,
    # per-segment question the checks after the main loop below answer
    # (`derive_segment_sub_type`-driven, not tied to segment index -- see
    # their comment). `first_only` no longer means "segment 0 only": it means
    # "no free-floating keyframe timeline" (that's `chain_keyframes_anywhere`
    # /`keyframes_allowed`), distinct from where a segment's OWN edges land.
    leading_edge_allowed = i2v_declared or flf_declared or keyframes_allowed or chain_keyframes_first_only
    trailing_edge_allowed = flf_declared or keyframes_allowed

    keyframe_window = duration
    keyframe_window_label = "settings.duration"
    if chain_keyframes_anywhere:
        chain_frames = sum(s["frames"] for s in segments if isinstance(s.get("frames"), int))
        usable_fps = fps if isinstance(fps, (int, float)) and fps > 0 else None
        keyframe_window = (chain_frames / usable_fps) if usable_fps else None
        keyframe_window_label = "the chain's total duration"

    if mode == "t2v" and media:
        errors.append("media: t2v mode does not accept media references")
    if mode == "i2v" and len(media) != 1:
        errors.append(f"media: i2v mode requires exactly one media reference, got {len(media)}")
    if mode == "flf" and len(media) != 2:
        errors.append(f"media: flf mode requires exactly two media references (first + last), got {len(media)}")

    for i, item in enumerate(media):
        context = f"media[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{context}: must be an object")
            continue

        role = item.get("role")
        if role not in _MEDIA_ROLES:
            errors.append(f"{context}: role must be one of {sorted(_MEDIA_ROLES)}, got {role!r}")
            continue

        segment_id = item.get("segment_id")

        if role == "keyframe":
            if not keyframes_allowed:
                errors.append(f"{context}: keyframe media is only valid in a timeline director mode")
            at = item.get("at")
            if not isinstance(at, (int, float)) or at < 0 or (isinstance(keyframe_window, (int, float)) and at > keyframe_window):
                errors.append(f"{context}: at must be within [0, {keyframe_window_label}], got {at!r}")
            keyframe_count += 1
        else:
            if segment_id not in segment_ids:
                errors.append(f"{context}: segment_id {segment_id!r} does not reference an existing segment")
            elif role == "first":
                if mode == "director" and not leading_edge_allowed:
                    errors.append(f"{context}: this preset's director capabilities do not allow 'first' media")
                if segment_id in seen_first:
                    errors.append(f"{context}: segment {segment_id!r} already has a 'first' media reference")
                seen_first[segment_id] = item.get("id", context)
            elif role == "last":
                if mode == "director" and not trailing_edge_allowed:
                    errors.append(f"{context}: this preset's director capabilities do not allow 'last' media")
                if segment_id in seen_last:
                    errors.append(f"{context}: segment {segment_id!r} already has a 'last' media reference")
                seen_last[segment_id] = item.get("id", context)

        if mode == "i2v" and role != "first":
            errors.append(f"{context}: i2v mode only accepts the 'first' role")

        if mode == "flf" and role not in {"first", "last"}:
            errors.append(f"{context}: flf mode only accepts 'first'/'last' roles")

        strength = item.get("strength", 1.0)
        if not isinstance(strength, (int, float)):
            strength = 1.0
        strength = max(0.0, min(1.0, float(strength)))

        media_ref = _resolve_media_ref(item.get("media"), storage_path, context, errors)

        # `derive_ltx_media_fields` only ever LOADS image-typed keyframe/first/
        # last entries (see its docstring) -- a video-typed one used to pass
        # validation, then get silently dropped at pipeline-build time with no
        # feedback to the caller. Reject it here instead: video keyframes/
        # first/last frames aren't implemented (v1 cut).
        if role in {"keyframe", "first", "last"} and media_ref is not None and not _media_ref_is_image(media_ref):
            errors.append(
                f"{context}: {role} media type {media_ref.get('type')!r} is not supported -- "
                "only image media is supported today"
            )

        out.append({
            "id": item.get("id"),
            "role": role,
            "segment_id": segment_id,
            "at": item.get("at"),
            "strength": strength,
            "media": media_ref,
        })

    if mode == "flf" and out:
        roles = {entry["role"] for entry in out}
        seg_ids = {entry["segment_id"] for entry in out}
        if roles != {"first", "last"} or len(seg_ids) != 1:
            errors.append("media: flf mode requires one 'first' and one 'last' media reference on the same segment")

    # Join-aware edge-role checks for a routed chain (Wan/H3-style): a segment
    # is not pinned by INDEX to hold 'first'/'last' media -- any segment may,
    # as long as the media it's given is actually consumed. `derive_segment_sub_type`
    # is the single source of truth both here and in `derive_segment_routing`
    # (run later, on the same document) -- these two checks are the dead-knob
    # guard for the two ways this can go wrong:
    #  - 'first' media on a segment an explicit `sub_type: "chain"` override
    #    forces to continue instead -- the override always wins in
    #    `derive_segment_sub_type`, so the attached start image would be
    #    silently ignored.
    #  - 'last' media on a segment that won't resolve to 'flf' -- a trailing
    #    frame is only ever consumed paired with a leading one on the SAME
    #    segment (see generator/chain_video_wan22/main.py's `end = (...  if
    #    sub_type == "flf" and has_last else None)`); a segment carrying only
    #    the 'last' role has no effect at all.
    # Both are independent of `chain_keyframes_first_only` vs `_anywhere`: once
    # a mode admits an-edge-at-all (`leading_edge_allowed`/`trailing_edge_allowed`
    # above), which SEGMENT may carry it is a property of that segment's own
    # resolved sub_type, not its position in the list.
    if chain_style:
        for segment_id in seen_first:
            seg = segment_by_id.get(segment_id) or {}
            resolved = derive_segment_sub_type(
                index=index_by_id.get(segment_id, 0),
                has_first_media=True,
                has_last_media=segment_id in seen_last,
                override=seg.get("sub_type"),
            )
            if resolved == "chain":
                errors.append(
                    f"media: segment {segment_id!r} carries 'first' media but its sub_type is explicitly "
                    "'chain' -- a segment forced to continue from the previous one cannot also open on "
                    "its own frame"
                )
        for segment_id in seen_last:
            seg = segment_by_id.get(segment_id) or {}
            resolved = derive_segment_sub_type(
                index=index_by_id.get(segment_id, 0),
                has_first_media=segment_id in seen_first,
                has_last_media=True,
                override=seg.get("sub_type"),
            )
            if resolved != "flf":
                errors.append(
                    f"media: segment {segment_id!r} carries 'last' media but does not resolve to the "
                    "'flf' sub-type -- a trailing frame only has an effect on a segment that ALSO carries "
                    "a 'first' media reference"
                )

    if max_keyframes is not None and keyframe_count > max_keyframes:
        errors.append(f"media: at most {max_keyframes} keyframes are allowed, got {keyframe_count}")

    return out


def _normalize_audio(
    audio: List[Dict[str, Any]],
    mode_caps: Dict[str, Any],
    storage_path: Path,
    errors: List[str],
) -> List[Dict[str, Any]]:
    if not audio:
        return []

    if not mode_caps.get("audio"):
        errors.append("audio: audio tracks are only supported in a mode declaring the 'audio' capability")
        return []

    out: List[Dict[str, Any]] = []
    for i, item in enumerate(audio):
        context = f"audio[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{context}: must be an object")
            continue

        start = item.get("start", 0.0)
        if not isinstance(start, (int, float)) or start < 0:
            errors.append(f"{context}: start must be >= 0, got {start!r}")

        length = item.get("length")
        if not isinstance(length, (int, float)) or length <= 0:
            errors.append(f"{context}: length must be > 0, got {length!r}")

        trim_start = item.get("trim_start", 0.0)
        if not isinstance(trim_start, (int, float)) or trim_start < 0:
            errors.append(f"{context}: trim_start must be >= 0, got {trim_start!r}")

        role = item.get("role")
        if role is None:
            role = "condition"
        elif role not in _AUDIO_ROLES:
            errors.append(f"{context}: role must be one of {sorted(_AUDIO_ROLES)}, got {role!r}")
            role = "condition"

        media_ref = _resolve_media_ref(item.get("media"), storage_path, context, errors)
        if media_ref is None:
            errors.append(f"{context}: media is required")

        out.append({
            "id": item.get("id"),
            "role": role,
            "start": start,
            "trim_start": trim_start,
            "length": length,
            "media": media_ref,
        })

    return out


def _normalize_ic_lora(
    ic_lora: List[Dict[str, Any]],
    timeline_style: bool,
    mode_caps: Dict[str, Any],
    storage_path: Path,
    errors: List[str],
) -> List[Dict[str, Any]]:
    if not ic_lora:
        return []

    if not timeline_style or not mode_caps.get("ic_lora"):
        errors.append("ic_lora: ic_lora is only supported in a timeline director mode with the 'ic_lora' capability")
        return []

    out: List[Dict[str, Any]] = []
    for i, item in enumerate(ic_lora):
        context = f"ic_lora[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{context}: must be an object")
            continue

        lora = item.get("lora") or {}
        if not isinstance(lora, dict) or not lora.get("model"):
            errors.append(f"{context}: lora.model is required")
            model, lora_strength = None, 1.0
        else:
            model = lora["model"]
            lora_strength = lora.get("strength", 1.0)
            if not isinstance(lora_strength, (int, float)):
                lora_strength = 1.0
            lora_strength = max(0.0, min(1.0, float(lora_strength)))

        reference = item.get("reference")
        reference_ref = None
        if reference is not None:
            reference_ref = _resolve_media_ref(reference, storage_path, f"{context}.reference", errors)

        strength = item.get("strength", 1.0)
        if not isinstance(strength, (int, float)):
            strength = 1.0
        strength = max(0.0, min(1.0, float(strength)))

        out.append({
            "id": item.get("id"),
            "lora": {"model": model, "strength": lora_strength},
            "reference": reference_ref,
            "strength": strength,
        })

    return out
