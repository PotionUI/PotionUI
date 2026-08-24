"""Chat tools for reading and editing the generation form's Video Director document.

The frontend sends the live editor state as `form_state.video_director` on the
session metadata: `{"active": bool, "doc": <VideoDirectorValue|None>,
"capabilities": <raw preset var vars.video_director|None>}`. `doc` mirrors
`frontend/src/lib/types/videoDirector.ts` (VideoDirectorValue) -- its
segments/media are split across `simple`/`timeline`/`chain` depending on mode.
`_flatten` collapses that into one mode/style-agnostic read model shaped like
the wire document (`src/features/video_director/normalize.py`'s contract),
which both tools and this module's own validation work against.

get_video_director is the only editing surface left on the model's side: the
document is otherwise user-only (shot count, durations, media, mode,
settings). The model may still suggest per-shot prompt VERSIONS through the
frontend's `<tool_action type="update_director_segment">` markup convention
(never a registered tool, so `tool_call_rescue` never touches it) -- see
`_HOW_TO_EDIT` below, taught back to the model via `get_video_director`'s
`how_to_edit` field.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Sequence

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import video_director_active
from src.features.video_director import apply_preset_mode_overlay

logger = logging.getLogger(__name__)

_MODE_ORDER = ("t2v", "i2v", "flf", "director")
_AUDIO_ROLES = ("condition", "mux")
_BASE_OPS = (
    "set_mode", "set_settings", "set_prompt", "set_negative_prompt",
    "upsert_segment", "remove_segment", "reorder_segments", "upsert_media", "remove_media",
)

# Mirrors the bounds `src/features/video_director/normalize.py` enforces on a
# submitted document -- reported here purely as informational metadata
# (`_capability_summary`); nothing on the model's side can act on it.
_CHAIN_MAX_FRAMES_DEFAULT = 257
_DEFAULT_MAX_SEGMENTS = 8
_DEFAULT_MAX_KEYFRAMES = 8
# The only override the chain editor round-trips: forcing a prompt-only later
# shot to a fresh cut instead of continuing the previous one. Every other
# sub-type is derived from the segment's own media (derive_segment_sub_type).
_SUB_TYPE_OVERRIDES = ("t2v",)


def _style_for(mode: str, capabilities: Dict[str, Any]) -> str:
    return "chain" if mode == "director" and bool(capabilities.get("segment_routing")) else "timeline"


def _mode_caps(mode: str, capabilities: Dict[str, Any]) -> Dict[str, Any]:
    return (capabilities.get("modes") or {}).get(mode) or {}


def _continuation_disabled(mode: str, style: str, capabilities: Dict[str, Any]) -> bool:
    """Mirror of normalize.py's `chain_continuation_disabled`: true only when
    this mode's capability declares `continuation` EXPLICITLY as `null` (the
    key present, not merely absent -- most chain presets never declare it at
    all and keep their normal continuation behaviour). See
    `apply_preset_mode_overlay` and MiniMax-H3's `refs` override."""
    if style != "chain":
        return False
    mode_caps = _mode_caps(mode, capabilities)
    return "continuation" in mode_caps and mode_caps.get("continuation") is None


def _derive_chain_sub_type(
    index: int, has_first_media: bool, override: Optional[str], continuation_disabled: bool = False,
    has_last_media: bool = False,
) -> str:
    """Mirror of `derive_segment_sub_type` (normalize.py) and
    `deriveChainSegmentSubType` (frontend/src/lib/utils/videoDirector.ts):
    any segment may carry its own leading (and, paired with it, trailing)
    image -- not just segment 0. `continuation_disabled` coerces the derived
    "continues the previous shot" result to a hard cut -- the tool-side
    mirror of `derive_segment_routing`'s same coercion."""
    if override in _SUB_TYPE_OVERRIDES:
        return override
    if has_first_media and has_last_media:
        return "flf"
    if has_first_media:
        return "i2v"
    if index == 0:
        return "t2v"
    return "t2v" if continuation_disabled else "chain"


def _new_segment_id() -> str:
    return f"seg_{uuid.uuid4().hex[:8]}"


def _new_media_id() -> str:
    return f"media_{uuid.uuid4().hex[:8]}"


def _new_audio_id() -> str:
    return f"audio_{uuid.uuid4().hex[:8]}"


def _truncate(text: Any, limit: int = 160) -> str:
    text = text if isinstance(text, str) else ""
    return text[:limit] + ("..." if len(text) > limit else "")


def _form_media_items(field: Any, form_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The media item(s) sitting on `form_data[field]` -- a single media-loader
    field's value, or every entry of a `multiple` field's array. Non-media
    values (a model field's `modelPath`, a LoRA row, ...) don't carry `path`/
    `relative_path` and are filtered out."""
    if not isinstance(field, str):
        return []
    raw = form_data.get(field)
    items = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
    return [i for i in items if isinstance(i, dict) and (i.get("path") or i.get("relative_path"))]


def _media_ref_path(value: Any, form_data: Dict[str, Any]) -> Optional[str]:
    """The concrete storage path for a Director media slot's editor value,
    resolving a `form_ref` pointer (Stage B reference media) against the live
    form -- mirrors `resolveFormMediaItem` in
    frontend/src/lib/utils/videoDirector.ts. Returns None for an empty,
    malformed, or unresolvable (item no longer on the form) value.
    """
    if not isinstance(value, dict):
        return None
    if value.get("path"):
        return value["path"]
    form_ref = value.get("form_ref")
    if not isinstance(form_ref, dict):
        return None
    field, target = form_ref.get("field"), form_ref.get("path")
    if not isinstance(field, str) or not isinstance(target, str):
        return None
    for item in _form_media_items(field, form_data):
        if item.get("path") == target or item.get("relative_path") == target:
            return item.get("path") or item.get("relative_path")
    return None


def _flatten(doc: Dict[str, Any], capabilities: Dict[str, Any], form_data: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse the editor's per-mode `simple`/`timeline`/`chain` storage into a
    single wire-shaped read model. Mirrors the editor->wire mapping in
    `frontend/src/lib/utils/videoDirector.ts` (buildDirectorSubmission).
    """
    mode = doc.get("mode") if doc.get("mode") in _MODE_ORDER else "t2v"
    style = _style_for(mode, capabilities)
    global_prompt = doc.get("global_prompt") or ""
    negative_prompt = doc.get("negative_prompt") or ""

    segments: List[Dict[str, Any]] = []
    media: List[Dict[str, Any]] = []
    audio: List[Dict[str, Any]] = []
    continuation: Optional[Dict[str, Any]] = None
    fps = duration = None

    if style == "chain":
        chain = doc.get("chain") or {}
        fps = chain.get("fps")
        chain_segments = chain.get("segments") or []
        total = sum((s.get("duration") or 0) for s in chain_segments)
        duration = total or None
        continuation_disabled = _continuation_disabled(mode, style, capabilities)
        for index, s in enumerate(chain_segments):
            seg_id = s.get("id") or _new_segment_id()
            seg_duration = s.get("duration") or 0
            frames = round(seg_duration * fps) if fps else None
            keyframe_path = _media_ref_path(s.get("keyframe"), form_data)
            last_keyframe_path = _media_ref_path(s.get("last_keyframe"), form_data)
            # Any segment's own start/end image reaches the wire now (not just
            # segment 0) -- see buildChainDirectorSubmission.
            has_first = keyframe_path is not None
            has_last = last_keyframe_path is not None
            override = s.get("sub_type_override")
            override = override if override in _SUB_TYPE_OVERRIDES else None
            segments.append({
                "id": seg_id, "prompt": s.get("prompt") or "", "negative_prompt": None,
                "start": None, "end": None, "frames": frames,
                "duration": seg_duration or None,
                "seed": None, "steps": None, "cfg": None,
                "sub_type_override": override,
                "sub_type": _derive_chain_sub_type(index, has_first, override, continuation_disabled, has_last),
                "references": s.get("references"),
            })
            if has_first:
                media.append({
                    "id": f"kf-{seg_id}", "role": "first", "segment_id": seg_id, "at": 0,
                    "strength": s.get("keyframe_strength", 1.0), "path": keyframe_path,
                })
            if has_last:
                media.append({
                    "id": f"kf-last-{seg_id}", "role": "last", "segment_id": seg_id, "at": seg_duration or None,
                    "strength": s.get("last_keyframe_strength", 1.0), "path": last_keyframe_path,
                })
        for kf in chain.get("keyframes") or []:
            kf_path = _media_ref_path(kf.get("media"), form_data)
            if kf_path:
                media.append({
                    "id": kf.get("id") or _new_media_id(), "role": "keyframe", "segment_id": None,
                    "at": kf.get("at"), "strength": kf.get("strength", 1.0), "path": kf_path,
                })
        audio = _flatten_audio(chain.get("audio"), form_data)
        raw_continuation = chain.get("continuation")
        if isinstance(raw_continuation, dict):
            continuation = {
                "overlap_frames": raw_continuation.get("overlap_frames", 0),
                "stitch": bool(raw_continuation.get("stitch", True)),
            }
    elif mode == "director":
        timeline = doc.get("timeline") or {}
        fps = timeline.get("fps")
        duration = timeline.get("duration")
        for s in timeline.get("segments") or []:
            segments.append({
                "id": s.get("id") or _new_segment_id(), "prompt": s.get("text") or "",
                "negative_prompt": None, "start": s.get("start"), "end": s.get("end"),
                "frames": None, "seed": None, "steps": None, "cfg": None,
                "references": s.get("references"),
            })
        for kf in timeline.get("keyframes") or []:
            role = "keyframe" if kf.get("role") == "free" else kf.get("role")
            kf_path = _media_ref_path(kf.get("media"), form_data)
            if kf_path:
                media.append({
                    "id": kf.get("id") or _new_media_id(), "role": role, "segment_id": None,
                    "at": kf.get("start"), "strength": kf.get("strength", 1.0),
                    "path": kf_path,
                })
        audio = _flatten_audio(timeline.get("audio"), form_data)
    else:
        simple = doc.get("simple") or {}
        fps = simple.get("fps")
        duration = simple.get("duration")
        segments.append({
            "id": "seg-1", "prompt": global_prompt, "negative_prompt": negative_prompt,
            "start": 0, "end": duration, "frames": None,
            "seed": None, "steps": None, "cfg": None,
            "references": simple.get("references"),
        })
        if mode == "i2v":
            start_image_path = _media_ref_path(simple.get("start_image"), form_data)
            if start_image_path:
                media.append({"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1.0, "path": start_image_path})
        elif mode == "flf":
            first_frame_path = _media_ref_path(simple.get("first_frame"), form_data)
            last_frame_path = _media_ref_path(simple.get("last_frame"), form_data)
            if first_frame_path:
                media.append({"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1.0, "path": first_frame_path})
            if last_frame_path:
                media.append({"id": "m-2", "role": "last", "segment_id": "seg-1", "at": duration, "strength": 1.0, "path": last_frame_path})

    return {
        "mode": mode,
        "style": style,
        "settings": {
            "fps": fps, "duration": duration, "resolution": doc.get("resolution"),
            "seed": doc.get("seed"), "continuation": continuation,
        },
        "segments": segments,
        "media": media,
        "audio": audio,
        "global_prompt": global_prompt,
        "negative_prompt": negative_prompt,
    }


def _flatten_audio(entries: Any, form_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Editor audio tracks (`DirectorAudioSegment`) as wire-shaped read entries.
    An absent role means "condition" on the wire, so it is spelled out here."""
    out: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry_path = _media_ref_path(entry.get("media"), form_data)
        if not entry_path:
            continue
        role = entry.get("role")
        out.append({
            "id": entry.get("id") or _new_audio_id(),
            "role": role if role in _AUDIO_ROLES else "condition",
            "start": entry.get("start", 0.0),
            "trim_start": entry.get("trim_start", 0.0),
            "length": entry.get("length"),
            "path": entry_path,
        })
    return out


# Caps for render_context_summary -- a chat context block, not the full
# document (get_video_director returns that), so it stays budget-conscious
# the same way _render_music_director_summary does for Music Director.
_CONTEXT_SUMMARY_MAX_SHOTS = 30
_CONTEXT_SUMMARY_PROMPT_CHARS = 80
_CONTEXT_SUMMARY_MAX_POOL_ITEMS = 20


def _media_basename(path: Optional[str]) -> str:
    return path.rsplit("/", 1)[-1] if isinstance(path, str) else "?"


def render_context_summary(
    doc: Dict[str, Any],
    capabilities: Dict[str, Any],
    form_data: Dict[str, Any],
    preset_mode: Optional[str],
    media_fields: Sequence[str] = (),
) -> List[str]:
    """Compact snapshot of the active Video Director document for the chat
    workspace block: a capped shot list (id, prompt, length, sub_type in
    chain style, its own attached media) plus any form media not yet
    attached to a shot -- the same pool `upsert_media`'s `form_media`
    addresses (mirrors `_resolve_form_media`/`_form_media_items`).
    `media_fields` names the form's media-loader fields (from the active
    schema); an empty sequence just skips that section. Called by
    `ChatContextBuilder._render_video_director_summary`, parallel to
    `_render_music_director_summary` for Music Director.
    """
    flat = _flatten(doc, apply_preset_mode_overlay(capabilities, preset_mode), form_data)
    style = flat["style"]

    media_by_segment: Dict[Optional[str], List[Dict[str, Any]]] = {}
    attached_paths = set()
    for m in flat["media"]:
        media_by_segment.setdefault(m.get("segment_id"), []).append(m)
        if m.get("path"):
            attached_paths.add(m["path"])

    lines: List[str] = [f"  Mode: {flat['mode']} ({style} style)"]

    segments = flat["segments"]
    shown = segments[:_CONTEXT_SUMMARY_MAX_SHOTS]
    if shown:
        lines.append(f"  Shots ({len(segments)}):")
        for seg in shown:
            prompt = _truncate(seg.get("prompt") or "(no prompt)", _CONTEXT_SUMMARY_PROMPT_CHARS)
            if seg.get("duration") is not None:
                timing = f"{seg['duration']}s"
            elif seg.get("frames") is not None:
                timing = f"{seg['frames']}f"
            else:
                timing = f"{seg.get('start')}s-{seg.get('end')}s"
            detail = f"{timing}, {seg['sub_type']}" if style == "chain" else timing
            line = f'    - id={seg["id"]}: "{prompt}" ({detail})'
            seg_media = media_by_segment.get(seg["id"]) or []
            if seg_media:
                line += " -- media: " + ", ".join(
                    f"{m['role']}={_media_basename(m.get('path'))}" for m in seg_media
                )
            lines.append(line)
        overflow = len(segments) - len(shown)
        if overflow > 0:
            lines.append(f"    …and {overflow} more shots -- call get_video_director for the rest.")

    loose_media = media_by_segment.get(None) or []
    if loose_media:
        lines.append(
            "  Other media: " + ", ".join(f"{m['role']}={_media_basename(m.get('path'))}" for m in loose_media)
        )

    pool: List[str] = []
    for field_name in media_fields:
        for item in _form_media_items(field_name, form_data):
            path = item.get("path") or item.get("relative_path")
            if not path or path in attached_paths:
                continue
            label = item.get("label") or item.get("name") or _media_basename(path)
            pool.append(f'"{label}" on {field_name!r}')
    if pool:
        shown_pool = pool[:_CONTEXT_SUMMARY_MAX_POOL_ITEMS]
        line = "  Available media not yet attached: " + ", ".join(shown_pool)
        overflow_pool = len(pool) - len(shown_pool)
        if overflow_pool > 0:
            line += f", +{overflow_pool} more"
        lines.append(line)

    return lines


def _capability_summary(capabilities: Dict[str, Any], mode: str, style: str) -> Dict[str, Any]:
    """What this preset declares about the document's shape and limits,
    derived from the preset's declared capabilities -- never hardcoded prose
    about a family. Informational only: nothing on the model's side can act
    on `available_operations` or any other field here (see `_HOW_TO_EDIT`)."""
    modes_raw = capabilities.get("modes") or {}
    allowed_modes = [m for m in _MODE_ORDER if isinstance(modes_raw.get(m), dict)]
    limits = capabilities.get("limits") or {}
    routing = bool(capabilities.get("segment_routing"))
    director_caps = modes_raw.get("director") or {}

    media_rules = {
        "t2v": "no media allowed",
        "i2v": "exactly one media entry, role 'first'",
        "flf": "exactly two media entries, one 'first' and one 'last'",
    }
    if "director" in allowed_modes:
        max_kf = director_caps.get("max_keyframes", _DEFAULT_MAX_KEYFRAMES)
        if routing:
            flf_declared = "flf" in allowed_modes
            trailing_allowed = flf_declared or director_caps.get("keyframes") == "anywhere"
            leading_rule = (
                "a start image (role 'first') on ANY shot that opens fresh -- the first shot, or one whose "
                "sub_type_override is 't2v' (a hard cut) instead of continuing the previous shot"
            )
            trailing_rule = (
                "; an end image (role 'last') is only ever honoured PAIRED with 'first' on the SAME shot "
                "(that combination is the 'flf' sub-type) -- adding 'last' alone has no effect and is rejected"
                if trailing_allowed
                else "; this preset does not admit an end image (role 'last') at all"
            )
            if director_caps.get("keyframes") == "anywhere":
                media_rules["director"] = (
                    f"chain style: {leading_rule}{trailing_rule}, plus up to {max_kf} images placed anywhere "
                    "along the chain (role 'keyframe', 'at' in seconds from the start of the chain, bounded "
                    "by the chain's total duration)"
                )
            else:
                media_rules["director"] = (
                    f"chain style: {leading_rule}{trailing_rule}; this preset does not declare "
                    "keyframes: 'anywhere', so role 'keyframe' is not available"
                )
        else:
            media_rules["director"] = (
                f"timeline style: any number of 'first'/'last'/'keyframe' media entries (max {max_kf} keyframes)"
            )

    summary: Dict[str, Any] = {
        "allowed_modes": allowed_modes,
        "director_style": ("chain" if routing else "timeline") if "director" in allowed_modes else None,
        "default_fps": limits.get("default_fps"),
        "default_duration": limits.get("default_duration"),
        "max_duration": limits.get("max_duration"),
        "media_rules": media_rules,
        "segment_fields_by_style": {
            "chain": ["prompt", "negative_prompt", "duration", "frames", "sub_type_override", "seed", "steps", "cfg"],
            "timeline": ["prompt", "negative_prompt", "start", "end"],
        },
        "audio": _audio_capability(mode, capabilities),
        "references": _references_capability(capabilities),
        "available_operations": _available_ops(mode, style, capabilities),
    }

    if style == "chain":
        continuation = director_caps.get("continuation") or {}
        continuation_disabled = _continuation_disabled(mode, style, capabilities)
        summary["chain"] = {
            "max_segments": director_caps.get("max_segments", _DEFAULT_MAX_SEGMENTS),
            "max_frames_per_segment": director_caps.get("max_frames_per_segment", _CHAIN_MAX_FRAMES_DEFAULT),
            "keyframes": director_caps.get("keyframes"),
            "max_keyframes": director_caps.get("max_keyframes", _DEFAULT_MAX_KEYFRAMES),
            "continuation_source": continuation.get("source"),
            "max_overlap_frames": director_caps.get("max_overlap_frames"),
            "join_rule": (
                "shots do not continue under references -- every shot is generated as an independent cut "
                "regardless of sub_type_override; there is no tail-frame handoff between shots"
                if continuation_disabled else
                "a prompt-only segment after the first CONTINUES the previous shot (sub_type 'chain'); "
                "sub_type_override 't2v' makes it a hard cut; a start image on the first segment makes "
                "that segment 'i2v'"
            ),
        }
    return summary


def _audio_capability(mode: str, capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """Audio is declared per mode (`modes.<mode>.audio`), independent of style."""
    if not _mode_caps(mode, capabilities).get("audio"):
        return {"supported": False}
    return {
        "supported": True,
        "roles": list(_AUDIO_ROLES),
        "recommended_role": "mux",
        "role_reality": (
            "'mux' lays the track over the finished video and is what the generators implement today. "
            "'condition' (the picture following the audio) is a valid value in the document format, but "
            "no generator conditions on it yet -- a generator either refuses the run outright or muxes "
            "the track anyway. Use 'mux' unless the user explicitly asks for audio-conditioned generation."
        ),
    }


def _references_capability(capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """`capabilities.references`/`reference_fields` -- generated from the
    preset's declaration, never hardcoded family prose. `None` means this
    preset has no whole-film reference pool at all."""
    references = capabilities.get("references")
    if not references:
        return {"supported": False}
    return {
        "supported": True,
        "selection": references,
        "fields": capabilities.get("reference_fields") or [],
    }


def _available_ops(mode: str, style: str, capabilities: Dict[str, Any]) -> List[str]:
    ops = list(_BASE_OPS)
    if _mode_caps(mode, capabilities).get("audio"):
        ops += ["upsert_audio", "remove_audio"]
    if style == "chain" and mode == "director" and not _continuation_disabled(mode, style, capabilities):
        ops.append("set_continuation")
    return ops


# The only editing action left on the model's side: this document is
# otherwise user-only, forever (shot count, durations, media, mode,
# settings). Returned verbatim as `get_video_director`'s `how_to_edit` field
# and taught with the same tag in the generation-mode system prompt
# (src/features/chat/modes/builtin.py) and the per-turn workspace summary
# (src/features/chat/context_builder.py) -- keep the three in sync.
_HOW_TO_EDIT = (
    "You cannot edit this document -- shot count, durations, media, mode, and "
    "settings are user-only, forever. The only thing you may do is offer prompt "
    "VERSIONS for one or more shots: emit "
    '<tool_action type="update_director_segment" segment_index="N" segment_id="ID">'
    "proposed prompt text</tool_action> tags in your reply text, one per version, "
    "plain replacement prompt text only -- no [Shot N]/[Scene N] markers, no JSON "
    "-- using a shot's index/id from the segments list above (or the per-turn "
    "Video Director summary). Never attempt or claim to change duration, media, "
    "mode, or shot count; there is no tool for that."
)


class GetVideoDirectorTool(BaseTool):
    """Reads the Video Director document currently loaded on the user's form."""

    modes = ["generation"]
    icon = "clapperboard"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return video_director_active(form_state)

    @property
    def name(self) -> str:
        return "get_video_director"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Reads the Video Director document on your generation form."

    @property
    def hint(self) -> str:
        return (
            "When the user asks about their video shots, segments, timeline, keyframes, "
            "or Video Director settings -- call this first. Its how_to_edit field explains "
            "the only change you may propose: prompt VERSIONS via the update_director_segment "
            "tag. Shot count, durations, media, mode, and settings are user-only."
        )

    @property
    def description(self) -> str:
        return (
            "Read the Video Director document currently loaded on the user's generation "
            "form, if the active preset/mode exposes one. Video Director composes a video "
            "generation in one of four modes: 't2v' (text only, no media), 'i2v' (one start "
            "image), 'flf' (a first+last frame pair), or 'director' -- a multi-part "
            "composition whose shape depends on the preset's capabilities rather than its "
            "name: 'chain' style (e.g. Wan, MiniMax-H3) is an ordered list of shots, each "
            "generated separately with its own prompt and length and then concatenated; "
            "'timeline' style (e.g. LTX) is a single generation with segments placed by "
            "start/end time along one total duration, plus optional keyframe images and "
            "IC-LoRA references. Returns the active mode and style, current settings (fps, "
            "duration, resolution, seed, and in chain style the continuation settings that "
            "join consecutive shots), every segment (in chain style with its length in both "
            "seconds and frames, its resolved 'sub_type' -- whether it continues the previous "
            "shot or cuts to a new one -- and its 'sub_type_override'), the media and audio "
            "tracks on the document, the global direction prompt and negative prompt, and a "
            "capability summary naming this preset's limits and shape. Also returns "
            "how_to_edit: the document is otherwise read-only from your side -- the only "
            "change you may propose is a prompt VERSION for one or more shots."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        form_state = context.session_metadata.get("form_state") or {}
        vd = form_state.get("video_director")
        if not vd or not vd.get("active"):
            return ToolResult(
                success=False,
                data="",
                error=(
                    "The current preset/mode has no Video Director document active. "
                    "Use get_form_state and update_form_settings for regular form fields instead."
                ),
            )

        try:
            doc = vd.get("doc") or {}
            capabilities = apply_preset_mode_overlay(vd.get("capabilities") or {}, form_state.get("mode"))
            flat = _flatten(doc, capabilities, form_state.get("form_data") or {})

            result = {
                "mode": flat["mode"],
                "style": flat["style"],
                "settings": flat["settings"],
                "global_prompt": flat["global_prompt"],
                "negative_prompt": flat["negative_prompt"],
                "segments": flat["segments"],
                "media": flat["media"],
                "audio": flat["audio"],
                "capabilities": _capability_summary(capabilities, flat["mode"], flat["style"]),
                "how_to_edit": _HOW_TO_EDIT,
            }
            return ToolResult(success=True, data=json.dumps(result))
        except Exception as e:
            logger.error(f"Error reading video director from session metadata: {e}")
            return ToolResult(success=False, data="", error=str(e))

