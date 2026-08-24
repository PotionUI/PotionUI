"""Chat tools for reading and editing the generation form's Video Director document.

The frontend sends the live editor state as `form_state.video_director` on the
session metadata: `{"active": bool, "doc": <VideoDirectorValue|None>,
"capabilities": <raw preset var vars.video_director|None>}`. `doc` mirrors
`frontend/src/lib/types/videoDirector.ts` (VideoDirectorValue) -- its
segments/media are split across `simple`/`timeline`/`chain` depending on mode.
`_flatten` collapses that into one mode/style-agnostic read model shaped like
the wire document (`src/features/video_director/normalize.py`'s contract),
which both tools and this module's own validation work against.

These tools never mutate `doc` themselves: `update_video_director`'s confirmed
result carries only the requested operations (server-assigned ids filled in),
and the frontend applies them to its own editor state.
"""

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import video_director_active
from src.features.llm.tools.tool_call_rescue import decode_payload, demangle_quote_tokens
from src.features.video_director import apply_preset_mode_overlay

logger = logging.getLogger(__name__)

_MODE_ORDER = ("t2v", "i2v", "flf", "director")
_MEDIA_ROLES = ("first", "last", "keyframe")
_AUDIO_ROLES = ("condition", "mux")
_SETTINGS_FIELDS = ("fps", "duration", "resolution", "seed")
_BASE_OPS = (
    "set_mode", "set_settings", "set_prompt", "set_negative_prompt",
    "upsert_segment", "remove_segment", "reorder_segments", "upsert_media", "remove_media",
)
_ALL_OPS = _BASE_OPS + ("upsert_audio", "remove_audio", "set_continuation")

# Mirrors the bounds `src/features/video_director/normalize.py` enforces on a
# submitted document, so the model is told its mistake here instead of having
# the whole generation rejected after approval.
_CHAIN_MAX_FRAMES_DEFAULT = 257
_CHAIN_STEPS_RANGE = (1, 150)
_CHAIN_CFG_RANGE = (0, 30)
# Every native video generator's PipeConfigSpec("fps", ...) agrees on this, so
# normalize.py enforces it regardless of preset/mode -- and so does this.
_FPS_RANGE = (1.0, 60.0)
_DEFAULT_MAX_SEGMENTS = 8
_DEFAULT_MAX_KEYFRAMES = 8
# The only override the chain editor round-trips: forcing a prompt-only later
# shot to a fresh cut instead of continuing the previous one. Every other
# sub-type is derived from the segment's own media (derive_segment_sub_type).
_SUB_TYPE_OVERRIDES = ("t2v",)


# A shot marker inside a prompt is the signature of a whole multi-shot script
# stuffed into one operation: the segment IS the shot, so the marker never
# belongs in its prompt.
_SHOT_MARKER_RE = re.compile(r"\[\s*(?:shot|scene|segment|clip|part)\s*#?\s*\d+\s*\]", re.IGNORECASE)

# Every field a number may arrive in. A local model routinely quotes them
# ("duration": "4"), which no downstream check would accept.
_NUMERIC_FIELDS = frozenset({
    "fps", "duration", "frames", "seed", "steps", "cfg", "start", "end", "at",
    "strength", "length", "trim_start", "overlap_frames",
})

# The payload object each op carries, and the fields that belong in it. A model
# that flattened those fields onto the operation itself is read as if it had
# nested them -- no other operation field could claim those names.
_OP_PAYLOADS = {
    "set_settings": ("settings", ("fps", "duration", "resolution", "seed")),
    "upsert_segment": ("segment", (
        "id", "prompt", "negative_prompt", "start", "end", "duration", "frames",
        "sub_type_override", "seed", "steps", "cfg", "references",
    )),
    "upsert_media": ("media", ("id", "role", "segment_id", "at", "strength", "path", "form_media")),
    "upsert_audio": ("audio", ("id", "role", "start", "trim_start", "length", "path")),
    "set_continuation": ("continuation", ("overlap_frames", "stitch")),
}

# Shows BOTH shapes upsert_segment accepts: an "id" updates that existing
# shot (a model shown only add-shaped examples imitates them and adds
# duplicate shots instead of updating -- repo lore), an omitted "id" adds a
# new one.
_OPERATIONS_EXAMPLE = (
    '[{"op": "upsert_segment", "segment": {"id": "seg-a", "prompt": "a wide shot of the desert", "duration": 4}}, '
    '{"op": "upsert_segment", "segment": {"prompt": "close-up on the rider", "duration": 3}}]'
)
# Referenced from the tool's `hint`, not `description` -- `hint` isn't part
# of the reshipped-every-turn tool schema `test_tool_schema_sizes.py` caps.
_MEDIA_OPERATION_EXAMPLE = (
    '{"op": "upsert_media", "media": {"role": "first", "segment_id": "seg-a", '
    '"form_media": {"field": "start_image", "label": "sunset.png"}}}'
)
# Every example the model READS is the whole call, never a bare
# `operations = [...]` assignment: sitting in the same context as the
# `<tool_action type="update_segment" ...>` markup that
# src/features/chat/modes/builtin.py teaches for prompt edits, an assignment
# is what a local model concatenated into
# `<tool_action type="update_video_director" operations=[{op:...}]>`.
_CALL_EXAMPLE = (
    '<tool_call>{"name": "update_video_director", "arguments": {"operations": '
    f"{_OPERATIONS_EXAMPLE}"
    "}}</tool_call>"
)


class _OperationError(ValueError):
    """Raised with every structural problem found, not just the first."""


def _as_number(value: Any) -> Any:
    """A quoted number becomes a number; anything else is returned untouched so
    the field's own validation reports it."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        return int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
    except ValueError:
        return value


def _normalize_op(raw_op: Any) -> Any:
    """Make one operation readable without loosening the contract: accept
    `operation` as a spelling of `op`, un-quote numbers, and lift a flattened
    payload back under its own key."""
    if not isinstance(raw_op, dict):
        return raw_op

    op = dict(raw_op)
    if "op" not in op and isinstance(op.get("operation"), str):
        op["op"] = op.pop("operation")

    payload_spec = _OP_PAYLOADS.get(op.get("op"))
    if payload_spec:
        key, fields = payload_spec
        if not isinstance(op.get(key), dict):
            flattened = {f: op[f] for f in fields if f in op}
            if flattened:
                op[key] = flattened

    for key, value in list(op.items()):
        if key in _NUMERIC_FIELDS:
            op[key] = _as_number(value)
        elif isinstance(value, dict):
            op[key] = {
                k: (_as_number(v) if k in _NUMERIC_FIELDS else v) for k, v in value.items()
            }
    return op


def coerce_operations(operations: Any) -> Tuple[Optional[List[Any]], Optional[str]]:
    """The `operations` argument as a list, or `(None, <corrective error>)`.

    A weak model sends the array as TEXT often enough (a JSON string, or a
    Python repr from a rescued `<tool_action operations="...">` tag) that
    iterating a string's characters is the failure mode to design against.
    """
    if isinstance(operations, dict):
        return [operations], None
    if isinstance(operations, list):
        return operations, None
    if isinstance(operations, str):
        # A tool argument is a payload, not prose, so the tokenizer's mangled
        # quotes are safe to restore here too -- the same local model writes
        # them inside a native call as well as inside a <tool_action> tag.
        decoded, problem = decode_payload(demangle_quote_tokens(operations))
        if isinstance(decoded, list):
            return decoded, None
        if isinstance(decoded, dict):
            return [decoded], None
        return None, (
            "'operations' must be an ARRAY of operation objects, not text. "
            + (f"The text you sent could not be read: {problem}. " if problem else "")
            + f"Send the whole call as JSON with double quotes: {_CALL_EXAMPLE}"
        )
    return None, (
        f"'operations' must be an array of operation objects, got {type(operations).__name__}. "
        f"The whole call looks like this: {_CALL_EXAMPLE}"
    )


def _shot_marker_problem(text: Any, context: str, field: str) -> Optional[str]:
    markers = _SHOT_MARKER_RE.findall(text if isinstance(text, str) else "")
    if not markers:
        return None
    if len(markers) > 1:
        return (
            f"{context}: {field} carries {len(markers)} shot markers ({', '.join(markers[:3])}) -- that is a "
            "whole script in one prompt. Split this into one upsert_segment per shot, each with only that "
            "shot's own description and its own duration, and drop the markers"
        )
    return (
        f"{context}: {field} carries the shot marker {markers[0]!r} -- a segment IS one shot, so the marker "
        "does not belong in its prompt. Remove it, and if the text describes more than one shot, split it "
        "into one upsert_segment per shot"
    )


# A model shown only add-shaped examples imitates them and re-describes an
# existing shot as a new upsert_segment instead of updating it by id -- this
# catches the clearest case of that (kept conservative: a short coincidental
# prefix match is not flagged).
_DUPLICATE_PROMPT_MIN_CHARS = 20


def _duplicate_segment_id(prompt: Any, segments: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The id of an existing segment whose prompt reads as the same shot as
    `prompt` (normalized equality, or one is a prefix of the other and the
    shorter side is long enough that a coincidental match is unlikely), or
    None."""
    needle = (prompt or "").strip().lower() if isinstance(prompt, str) else ""
    if not needle:
        return None
    for seg_id, seg in segments.items():
        existing = (seg.get("prompt") or "").strip().lower()
        if not existing:
            continue
        if existing == needle:
            return seg_id
        shorter, longer = (existing, needle) if len(existing) <= len(needle) else (needle, existing)
        if len(shorter) >= _DUPLICATE_PROMPT_MIN_CHARS and longer.startswith(shorter):
            return seg_id
    return None


def _style_for(mode: str, capabilities: Dict[str, Any]) -> str:
    return "chain" if mode == "director" and bool(capabilities.get("segment_routing")) else "timeline"


def _mode_caps(mode: str, capabilities: Dict[str, Any]) -> Dict[str, Any]:
    return (capabilities.get("modes") or {}).get(mode) or {}


def _chain_keyframes_anywhere(mode: str, style: str, capabilities: Dict[str, Any]) -> bool:
    return style == "chain" and _mode_caps(mode, capabilities).get("keyframes") == "anywhere"


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


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


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


def _resolve_form_media(
    form_media: Dict[str, Any], form_data: Dict[str, Any], context: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolves an `upsert_media.media.form_media` addressing object
    (`{field, label?, path?}`) against the live form to the concrete media
    item it names. Exactly one of `label`/`path` identifies the item within
    the field; `label` matches case-insensitively on the trimmed label (or
    `name` where no label was set). Returns `(item, None)` or `(None, error)`
    -- the error lists the field's available labels so the model can retry
    with a valid one instead of guessing again blind.
    """
    field = form_media.get("field")
    if not isinstance(field, str) or not field:
        return None, f"{context}: form_media.field is required (the name of a media-loader field on the form)"

    label = form_media.get("label")
    path = form_media.get("path")
    if (label is None) == (path is None):
        return None, (
            f"{context}: form_media needs exactly one of 'label' or 'path' to identify the item on field "
            f"{field!r} -- got {'neither' if label is None else 'both'}"
        )

    items = _form_media_items(field, form_data)
    if not items:
        return None, f"{context}: form field {field!r} has no media on it right now"

    def item_label(item: Dict[str, Any]) -> str:
        return str(item.get("label") or item.get("name") or "(untitled)")

    if path is not None:
        for item in items:
            if item.get("path") == path or item.get("relative_path") == path:
                return item, None
        return None, (
            f"{context}: no item at path {path!r} on form field {field!r}. Available items: "
            f"{[item_label(i) for i in items]}"
        )

    needle = label.strip().lower() if isinstance(label, str) else ""
    matches = [i for i in items if item_label(i).strip().lower() == needle]
    available = [item_label(i) for i in items]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, (
            f"{context}: no item on form field {field!r} labeled {label!r}. Available labels: {available}. "
            f"Example: {{\"op\": \"upsert_media\", \"media\": {{\"role\": \"first\", \"form_media\": "
            f"{{\"field\": {field!r}, \"label\": {available[0]!r}}}}}}}"
        )
    return None, f"{context}: {len(matches)} items on form field {field!r} are labeled {label!r} -- ambiguous. Available: {available}"


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


def _validate_references_patch(
    raw_references: Any,
    capabilities: Dict[str, Any],
    form_data: Dict[str, Any],
    context: str,
    errors: List[str],
) -> Any:
    """Structural/capability validation for `upsert_segment.segment.references`
    -- mirrors the rules `normalize_video_director` enforces at submission time
    (`src/features/video_director/normalize.py`'s `_normalize_segment_references`/
    `_resolve_reference_entry`), so a bad selection is caught here instead of
    surfacing only when the whole generation is rejected. A `form_media` entry
    is resolved against the live form the same way `upsert_media.form_media` is
    (`_resolve_form_media`); a `path` entry is passed through untouched --
    normalize.py is what checks it actually exists on disk.
    """
    if raw_references is None:
        return None

    references_capability = capabilities.get("references")
    if references_capability != "per_shot":
        reason = (
            "references are not supported by this preset" if references_capability is None
            else "this preset's references capability is 'whole' -- segments inherit the full reference "
            "pool, per-segment references are not accepted"
        )
        errors.append(f"{context}.references: {reason}")
        return None

    if not isinstance(raw_references, list):
        errors.append(f"{context}.references: must be a list")
        return None

    reference_fields = capabilities.get("reference_fields") or []
    resolved: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw_references):
        entry_context = f"{context}.references[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{entry_context}: must be an object")
            continue
        raw_path = entry.get("path")
        form_media = entry.get("form_media")
        if raw_path and form_media is not None:
            errors.append(f"{entry_context}: give either 'path' or 'form_media', not both")
            continue
        if form_media is not None:
            if not isinstance(form_media, dict) or form_media.get("field") not in reference_fields:
                errors.append(
                    f"{entry_context}: form_media.field must be one of this preset's reference_fields "
                    f"{reference_fields}"
                )
                continue
            item, problem = _resolve_form_media(form_media, form_data, entry_context)
            if problem:
                errors.append(problem)
                continue
            resolved.append({
                "form_media": {"field": form_media["field"], "path": item.get("path") or item.get("relative_path")},
            })
        elif raw_path:
            resolved.append({"path": raw_path})
        else:
            errors.append(f"{entry_context}: provide either 'path' or 'form_media'")
    return resolved


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
    """Everything the model may do to THIS document, derived from the preset's
    declared capabilities -- never hardcoded prose about a family."""
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


def _how_to_edit(mode: str, style: str, capabilities: Dict[str, Any]) -> str:
    lines = [
        "Call update_video_director with one or more operations to change this document: "
        + ", ".join(_available_ops(mode, style, capabilities))
        + ". To offer prompt VERSIONS for one shot instead of changing the document, emit "
        '<tool_action type="update_director_segment" segment_index="N" segment_id="ID">prompt'
        "</tool_action> tags in your reply text -- one per version, using a segment's index/id "
        "above -- rather than calling update_video_director."
    ]
    if mode == "director" and style == "chain":
        director_caps = _mode_caps("director", capabilities)
        continuation_disabled = _continuation_disabled(mode, style, capabilities)
        if continuation_disabled:
            lines.append(
                "Chain style: the document is an ordered list of shots, each generated separately. Give a "
                "shot's length as 'duration' in SECONDS on upsert_segment (frames are derived as duration x "
                "fps); settings.duration is not settable here -- the total is the sum of the shots. Shots "
                "do not continue under references -- every shot is generated as an independent cut "
                "regardless of sub_type_override, there is no tail-frame handoff between shots, and "
                "set_continuation is not available."
            )
        else:
            lines.append(
                "Chain style: the document is an ordered list of shots, each generated separately and "
                "concatenated. Give a shot's length as 'duration' in SECONDS on upsert_segment (frames are "
                "derived as duration x fps); settings.duration is not settable here -- the total is the sum "
                "of the shots. A prompt-only shot after the first continues the previous shot from its tail "
                "frames; pass sub_type_override 't2v' to cut to a new shot instead, or null to go back to "
                "continuing. ANY shot that opens fresh (the first shot, or one cut via sub_type_override "
                "'t2v') can carry its own start image (upsert_media role 'first'); pairing 'first' with "
                "'last' on that SAME shot makes it end on a chosen frame too ('last' alone has no effect)."
            )
        if director_caps.get("keyframes") == "anywhere":
            lines.append(
                "This preset also places keyframe images anywhere along the chain: upsert_media with "
                "role 'keyframe' and 'at' in seconds from the start of the chain."
            )
        if not continuation_disabled and (
            director_caps.get("continuation") or director_caps.get("max_overlap_frames") is not None
        ):
            max_overlap = director_caps.get("max_overlap_frames")
            lines.append(
                "set_continuation controls how consecutive shots are joined: 'overlap_frames' is how many "
                "tail frames the next shot re-generates from"
                + (f" (max {max_overlap})" if max_overlap is not None else "")
                + ", 'stitch' whether the shots are concatenated into one clip."
            )
    else:
        lines.append(
            "Timeline style: one generation; segment timing uses start/end seconds inside settings.duration."
        )
    if _mode_caps(mode, capabilities).get("audio"):
        lines.append(
            "Audio tracks go through upsert_audio/remove_audio; use role 'mux' (see capabilities.audio)."
        )
    references = capabilities.get("references")
    if references == "per_shot":
        fields = capabilities.get("reference_fields") or []
        lines.append(
            f"This preset also holds a whole-film reference pool on form fields {fields} (see get_form_state). "
            "Each segment can select a subset of that pool with upsert_segment's 'references': a list of "
            "{form_media: {field, label?, path?}} (address an item already on one of those pool fields, same "
            "shape as upsert_media.form_media) or {path} entries; omit or set 'references' to null to have "
            "that segment inherit the full pool instead."
        )
    elif references == "whole":
        lines.append(
            "This preset holds a whole-film reference pool (see capabilities.references) shared by every "
            "segment -- there is no per-segment references selection."
        )
    lines.append(
        "upsert_media can point at an item already sitting on the user's form instead of a raw path -- "
        "give 'form_media': {field, label?} instead of 'path' (see get_form_state for field names)."
    )
    return " ".join(lines)


def _apply_operations(
    operations: List[Dict[str, Any]], flat: Dict[str, Any], capabilities: Dict[str, Any], form_data: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    """Apply `operations` to a working copy derived from `flat`, validating the
    END state (not each operation in isolation). Returns
    `(filled_operations, summary_lines, changes)` or raises `_OperationError`
    carrying every problem found. `changes` is the full-fidelity counterpart
    to `summary` -- one `{op, summary, before, after}` per applied operation,
    `before`/`after` None for an add/remove -- for a ToolApprovalPreview that
    needs more than the prose summary line to render a real diff.
    """
    modes_caps = capabilities.get("modes") or {}
    limits = capabilities.get("limits") or {}
    allowed_modes = [m for m in _MODE_ORDER if isinstance(modes_caps.get(m), dict)]

    mode = flat["mode"]
    style = flat["style"]
    settings = dict(flat["settings"])
    continuation = settings.pop("continuation", None)
    continuation = dict(continuation) if isinstance(continuation, dict) else None
    segments: Dict[str, Dict[str, Any]] = {s["id"]: dict(s) for s in flat["segments"]}
    segment_order: List[str] = [s["id"] for s in flat["segments"]]
    media: Dict[str, Dict[str, Any]] = {m["id"]: dict(m) for m in flat["media"]}
    audio: Dict[str, Dict[str, Any]] = {a["id"]: dict(a) for a in flat.get("audio") or []}
    global_prompt = flat.get("global_prompt") or ""
    global_negative_prompt = flat.get("negative_prompt") or ""

    def total_duration() -> Optional[float]:
        """How long the finished composition runs, in seconds: settings.duration
        for a single generation, the sum of the shots for a chain."""
        if style != "chain":
            duration = settings.get("duration")
            return duration if isinstance(duration, (int, float)) and duration > 0 else None
        fps = settings.get("fps")
        if not isinstance(fps, (int, float)) or fps <= 0:
            return None
        frames = sum(segments[sid].get("frames") or 0 for sid in segment_order)
        return frames / fps if frames else None

    filled_ops: List[Dict[str, Any]] = []
    summary: List[str] = []
    changes: List[Dict[str, Any]] = []
    errors: List[str] = []

    for i, raw_op in enumerate(operations):
        context = f"operations[{i}]"
        raw_op = _normalize_op(raw_op)
        if not isinstance(raw_op, dict) or not raw_op.get("op"):
            errors.append(
                f"{context}: every operation needs an 'op' naming what it does, one of "
                f"{list(_available_ops(mode, style, capabilities))}"
            )
            continue
        op = raw_op["op"]

        if op == "set_mode":
            new_mode = raw_op.get("mode")
            if new_mode not in allowed_modes:
                errors.append(f"{context}: mode {new_mode!r} is not enabled for this preset (allowed: {allowed_modes})")
                continue
            old_mode = mode
            mode = new_mode
            style = _style_for(mode, capabilities)
            filled_ops.append({"op": "set_mode", "mode": mode})
            summary.append(f"Set mode: {mode}")
            changes.append({
                "op": "set_mode", "summary": summary[-1],
                "before": {"mode": old_mode}, "after": {"mode": mode},
            })

        elif op == "set_settings":
            patch = raw_op.get("settings")
            if not isinstance(patch, dict):
                errors.append(f"{context}: 'settings' must be an object")
                continue
            applied = {k: patch[k] for k in _SETTINGS_FIELDS if k in patch}
            if not applied:
                errors.append(f"{context}: 'settings' has no recognized fields ({', '.join(_SETTINGS_FIELDS)})")
                continue
            fps = applied.get("fps")
            if fps is not None and (not isinstance(fps, (int, float)) or not (_FPS_RANGE[0] <= fps <= _FPS_RANGE[1])):
                errors.append(
                    f"{context}: 'fps' must be between {_FPS_RANGE[0]} and {_FPS_RANGE[1]}, got {fps!r}"
                )
                continue
            if style == "chain" and "duration" in applied:
                errors.append(
                    f"{context}: 'duration' is not settable in chain style -- the chain's total is the sum "
                    "of its shots; set each segment's own duration with upsert_segment instead"
                )
                continue
            old_settings = {k: settings.get(k) for k in applied}
            settings.update(applied)
            filled_ops.append({"op": "set_settings", "settings": applied})
            summary.append("Update settings: " + ", ".join(f"{k}={v}" for k, v in applied.items()))
            changes.append({
                "op": "set_settings", "summary": summary[-1],
                "before": old_settings, "after": dict(applied),
            })

        elif op == "set_prompt":
            prompt = raw_op.get("prompt")
            if not isinstance(prompt, str):
                errors.append(f"{context}: 'prompt' must be a string")
                continue
            marker_problem = _shot_marker_problem(prompt, context, "'prompt'")
            if marker_problem:
                errors.append(marker_problem)
                continue
            old_global_prompt = global_prompt
            global_prompt = prompt
            filled_ops.append({"op": "set_prompt", "prompt": prompt})
            summary.append(f'Set direction prompt: "{_truncate(prompt)}"')
            changes.append({
                "op": "set_prompt", "summary": summary[-1],
                "before": {"prompt": old_global_prompt}, "after": {"prompt": prompt},
            })

        elif op == "set_negative_prompt":
            negative_prompt = raw_op.get("negative_prompt")
            if not isinstance(negative_prompt, str):
                errors.append(f"{context}: 'negative_prompt' must be a string")
                continue
            old_global_negative_prompt = global_negative_prompt
            global_negative_prompt = negative_prompt
            filled_ops.append({"op": "set_negative_prompt", "negative_prompt": negative_prompt})
            summary.append(f'Set negative prompt: "{_truncate(negative_prompt)}"')
            changes.append({
                "op": "set_negative_prompt", "summary": summary[-1],
                "before": {"negative_prompt": old_global_negative_prompt},
                "after": {"negative_prompt": negative_prompt},
            })

        elif op == "upsert_segment":
            seg_patch = raw_op.get("segment")
            if not isinstance(seg_patch, dict):
                errors.append(f"{context}: 'segment' must be an object")
                continue

            requested_id = seg_patch.get("id")
            is_new = requested_id not in segments
            seg_id = requested_id if requested_id else _new_segment_id()

            before_segment = dict(segments[seg_id]) if not is_new else None
            current = dict(segments[seg_id]) if not is_new else {
                "id": seg_id, "prompt": "", "negative_prompt": None, "start": None,
                "end": None, "frames": None, "duration": None, "seed": None,
                "steps": None, "cfg": None, "sub_type_override": None, "references": None,
            }

            marker_problem = _shot_marker_problem(seg_patch.get("prompt"), context, "segment.prompt")
            if marker_problem:
                errors.append(marker_problem)
                continue

            for key in ("prompt", "negative_prompt", "seed"):
                if key in seg_patch:
                    current[key] = seg_patch[key]

            if is_new:
                dup_id = _duplicate_segment_id(current.get("prompt"), segments)
                if dup_id:
                    errors.append(
                        f"{context}: this reads as the same shot as existing segment {dup_id!r} "
                        f"(prompt: {_truncate(segments[dup_id].get('prompt') or '', 60)!r}) -- if you meant "
                        f"to change that shot, upsert_segment with \"id\": {dup_id!r} instead of adding a "
                        "new one"
                    )
                    continue

            if style == "chain":
                for key in ("start", "end"):
                    if seg_patch.get(key) is not None:
                        errors.append(f"{context}: '{key}' is only valid in timeline style")
                for key in ("steps", "cfg"):
                    if key in seg_patch:
                        current[key] = seg_patch[key]

                fps = settings.get("fps")
                fps_usable = isinstance(fps, (int, float)) and fps > 0
                duration = seg_patch.get("duration")
                frames = seg_patch.get("frames")
                if duration is not None and frames is not None:
                    errors.append(
                        f"{context}: give a shot's length as either 'duration' (seconds, preferred) or "
                        "'frames', not both"
                    )
                elif duration is not None:
                    if not isinstance(duration, (int, float)) or duration <= 0:
                        errors.append(f"{context}: 'duration' must be a positive number of seconds, got {duration!r}")
                    elif not fps_usable:
                        errors.append(f"{context}: cannot convert 'duration' to frames without a positive settings.fps")
                    else:
                        current["duration"] = duration
                        current["frames"] = round(duration * fps)
                elif frames is not None:
                    current["frames"] = frames
                    current["duration"] = (frames / fps) if fps_usable and isinstance(frames, int) else None
                elif is_new:
                    # A shot the model didn't size gets the preset's own default
                    # length rather than an unusable None the end-state check
                    # would only reject.
                    default_duration = _mode_caps(mode, capabilities).get(
                        "default_segment_duration", limits.get("default_duration")
                    )
                    if isinstance(default_duration, (int, float)) and fps_usable:
                        current["duration"] = default_duration
                        current["frames"] = round(default_duration * fps)

                if "sub_type_override" in seg_patch:
                    override = seg_patch["sub_type_override"]
                    if override is not None and override not in _SUB_TYPE_OVERRIDES:
                        errors.append(
                            f"{context}: 'sub_type_override' must be null (continue the previous shot) or "
                            f"one of {list(_SUB_TYPE_OVERRIDES)} (cut to a new shot), got {override!r}"
                        )
                    else:
                        current["sub_type_override"] = override
            else:
                for key in ("frames", "duration", "steps", "cfg", "sub_type_override"):
                    if seg_patch.get(key) is not None:
                        errors.append(f"{context}: '{key}' is only valid in chain style")
                if "start" in seg_patch or "end" in seg_patch:
                    start = seg_patch.get("start", current.get("start"))
                    end = seg_patch.get("end", current.get("end"))
                    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start >= end:
                        errors.append(f"{context}: 'start' must be < 'end', got start={start!r} end={end!r}")
                    current["start"], current["end"] = start, end

            if "references" in seg_patch:
                current["references"] = _validate_references_patch(
                    seg_patch["references"], capabilities, form_data, context, errors,
                )

            current["id"] = seg_id
            segments[seg_id] = current
            if seg_id not in segment_order:
                segment_order.append(seg_id)

            # `sub_type` is derived from the end state (media + position), never
            # sent: the backend owns that derivation.
            after_segment = {k: v for k, v in current.items() if k != "sub_type"}
            filled_ops.append({"op": "upsert_segment", "segment": after_segment})
            label = current.get("prompt") or seg_id
            timing = f"frames {current.get('frames')}" if style == "chain" else f"{current.get('start')}s-{current.get('end')}s"
            summary.append(f'{"Add" if is_new else "Update"} segment "{_truncate(label)}" ({timing})')
            changes.append({
                "op": "upsert_segment", "summary": summary[-1],
                "before": before_segment, "after": after_segment,
            })

        elif op == "remove_segment":
            seg_id = raw_op.get("id")
            if not seg_id or seg_id not in segments:
                errors.append(f"{context}: unknown segment id {seg_id!r}")
                continue
            removed_segment = segments.pop(seg_id)
            segment_order = [s for s in segment_order if s != seg_id]
            filled_ops.append({"op": "remove_segment", "id": seg_id})
            summary.append(f"Remove segment {seg_id}")
            changes.append({
                "op": "remove_segment", "summary": summary[-1],
                "before": removed_segment, "after": None,
            })

        elif op == "reorder_segments":
            ids = raw_op.get("ids")
            if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(map(str, segment_order)):
                errors.append(f"{context}: 'ids' must be a permutation of the current segment ids {segment_order}")
                continue
            old_order = segment_order
            segment_order = list(ids)
            filled_ops.append({"op": "reorder_segments", "ids": segment_order})
            summary.append("Reorder segments: " + ", ".join(segment_order))
            changes.append({
                "op": "reorder_segments", "summary": summary[-1],
                "before": {"ids": old_order}, "after": {"ids": list(segment_order)},
            })

        elif op == "upsert_media":
            media_patch = raw_op.get("media")
            if not isinstance(media_patch, dict):
                errors.append(f"{context}: 'media' must be an object")
                continue
            role = media_patch.get("role")
            if role not in _MEDIA_ROLES:
                errors.append(f"{context}: role must be one of {_MEDIA_ROLES}, got {role!r}")
                continue
            keyframes_anywhere = _chain_keyframes_anywhere(mode, style, capabilities)
            if role == "keyframe" and style != "timeline" and not keyframes_anywhere:
                errors.append(
                    f"{context}: role 'keyframe' needs a timeline-style director, or a chain mode "
                    "declaring keyframes: 'anywhere' -- this preset declares neither"
                )
                continue
            raw_path = media_patch.get("path")
            raw_form_media = media_patch.get("form_media")
            if raw_path and raw_form_media is not None:
                errors.append(f"{context}: give either 'path' or 'form_media' to address the item, not both")
                continue
            form_ref: Optional[Dict[str, str]] = None
            if raw_form_media is not None:
                if not isinstance(raw_form_media, dict):
                    errors.append(f"{context}: 'form_media' must be an object")
                    continue
                item, problem = _resolve_form_media(raw_form_media, form_data, context)
                if problem:
                    errors.append(problem)
                    continue
                path = item.get("path") or item.get("relative_path")
                form_ref = {"field": raw_form_media["field"], "path": path}
            elif raw_path:
                path = raw_path
            else:
                errors.append(f"{context}: provide either 'path' or 'form_media' to address the item")
                continue

            segment_id = media_patch.get("segment_id")
            if style == "chain" and role != "keyframe":
                # An omitted segment_id has always meant "the opening shot" --
                # kept for backward compatibility now that any segment may
                # legally carry one.
                if segment_id is None:
                    segment_id = segment_order[0] if segment_order else None
                if segment_id not in segments:
                    errors.append(f"{context}: segment_id {segment_id!r} does not reference an existing segment")
                    continue
                # 'last' is only ever consumed paired with 'first' on the SAME
                # segment -- that combination resolves to the 'flf' sub-type
                # (_derive_chain_sub_type); an unpaired 'last' would silently
                # have no effect, so it's rejected here rather than accepted
                # and dropped by normalize.py later.
                if role == "last" and not any(
                    m.get("role") == "first" and m.get("segment_id") == segment_id for m in media.values()
                ):
                    errors.append(
                        f"{context}: segment {segment_id!r} needs a 'first' (start) image before a 'last' "
                        "(end) one has any effect -- add that first"
                    )
                    continue

            requested_id = media_patch.get("id")
            is_new = requested_id not in media
            media_id = requested_id if requested_id else _new_media_id()
            before_media = dict(media[media_id]) if not is_new else None

            entry = {
                "id": media_id, "role": role, "segment_id": segment_id, "at": media_patch.get("at"),
                "strength": _clamp01(media_patch.get("strength", 1.0)), "path": path,
            }
            media[media_id] = entry
            # `path` is always the resolved, concrete storage path -- even a
            # `form_media`-addressed request -- so an op applied by a frontend
            # that predates `form_ref` support still lands working embedded
            # media. `form_ref` (when present) additionally tells the CURRENT
            # applier (applyDirectorOperations in utils/videoDirector.ts) to
            # store a live reference instead of a frozen path snapshot.
            filled_media = dict(entry)
            if form_ref:
                filled_media["form_ref"] = form_ref
            filled_ops.append({"op": "upsert_media", "media": filled_media})
            source = f" (from form field {form_ref['field']!r})" if form_ref else ""
            summary.append(f'{"Add" if is_new else "Update"} {role} media: {path}{source}')
            changes.append({
                "op": "upsert_media", "summary": summary[-1],
                "before": before_media, "after": filled_media,
            })

        elif op == "remove_media":
            media_id = raw_op.get("id")
            if not media_id or media_id not in media:
                errors.append(f"{context}: unknown media id {media_id!r}")
                continue
            removed_media = media.pop(media_id)
            filled_ops.append({"op": "remove_media", "id": media_id})
            summary.append(f"Remove media {media_id}")
            changes.append({
                "op": "remove_media", "summary": summary[-1],
                "before": removed_media, "after": None,
            })

        elif op == "upsert_audio":
            if not _mode_caps(mode, capabilities).get("audio"):
                errors.append(f"{context}: this preset's {mode!r} mode does not support audio tracks")
                continue
            audio_patch = raw_op.get("audio")
            if not isinstance(audio_patch, dict):
                errors.append(f"{context}: 'audio' must be an object")
                continue
            audio_path = audio_patch.get("path")
            if not audio_path:
                errors.append(f"{context}: 'path' is required")
                continue

            audio_role = audio_patch.get("role", "mux")
            if audio_role not in _AUDIO_ROLES:
                errors.append(f"{context}: role must be one of {list(_AUDIO_ROLES)}, got {audio_role!r}")
                continue

            start = audio_patch.get("start", 0.0)
            if not isinstance(start, (int, float)) or start < 0:
                errors.append(f"{context}: 'start' must be >= 0 seconds, got {start!r}")
                continue
            trim_start = audio_patch.get("trim_start", 0.0)
            if not isinstance(trim_start, (int, float)) or trim_start < 0:
                errors.append(f"{context}: 'trim_start' must be >= 0 seconds, got {trim_start!r}")
                continue
            # A track the model didn't measure runs the length of the composition
            # -- the document has no way to read the file's real duration, and a
            # missing length is a hard rejection downstream.
            length = audio_patch.get("length", total_duration())
            if not isinstance(length, (int, float)) or length <= 0:
                errors.append(f"{context}: 'length' must be a positive number of seconds, got {length!r}")
                continue

            requested_id = audio_patch.get("id")
            is_new = requested_id not in audio
            audio_id = requested_id if requested_id else _new_audio_id()
            before_audio = dict(audio[audio_id]) if not is_new else None
            entry = {
                "id": audio_id, "role": audio_role, "start": start,
                "trim_start": trim_start, "length": length, "path": audio_path,
            }
            audio[audio_id] = entry
            filled_ops.append({"op": "upsert_audio", "audio": dict(entry)})
            summary.append(f'{"Add" if is_new else "Update"} {audio_role} audio: {audio_path}')
            changes.append({
                "op": "upsert_audio", "summary": summary[-1],
                "before": before_audio, "after": dict(entry),
            })

        elif op == "remove_audio":
            audio_id = raw_op.get("id")
            if not audio_id or audio_id not in audio:
                errors.append(f"{context}: unknown audio id {audio_id!r}")
                continue
            removed_audio = audio.pop(audio_id)
            filled_ops.append({"op": "remove_audio", "id": audio_id})
            summary.append(f"Remove audio {audio_id}")
            changes.append({
                "op": "remove_audio", "summary": summary[-1],
                "before": removed_audio, "after": None,
            })

        elif op == "set_continuation":
            if style != "chain":
                errors.append(f"{context}: 'set_continuation' is only valid in chain style")
                continue
            if _continuation_disabled(mode, style, capabilities):
                errors.append(
                    f"{context}: 'set_continuation' is not available -- shots do not continue under "
                    "references, every shot is generated as an independent cut"
                )
                continue
            patch = raw_op.get("continuation")
            if not isinstance(patch, dict):
                errors.append(f"{context}: 'continuation' must be an object")
                continue
            before_continuation = dict(continuation) if isinstance(continuation, dict) else None
            declared = _mode_caps(mode, capabilities).get("continuation") or {}
            current_continuation = dict(continuation or {
                "overlap_frames": declared.get("overlap_frames", 0),
                "stitch": bool(declared.get("stitch", True)),
            })
            max_overlap = _mode_caps(mode, capabilities).get("max_overlap_frames")
            if "overlap_frames" in patch:
                overlap = patch["overlap_frames"]
                if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0:
                    errors.append(f"{context}: 'overlap_frames' must be a non-negative integer, got {overlap!r}")
                    continue
                if max_overlap is not None and overlap > max_overlap:
                    errors.append(
                        f"{context}: 'overlap_frames' {overlap} exceeds this mode's max_overlap_frames {max_overlap}"
                    )
                    continue
                current_continuation["overlap_frames"] = overlap
            if "stitch" in patch:
                if not isinstance(patch["stitch"], bool):
                    errors.append(f"{context}: 'stitch' must be a boolean, got {patch['stitch']!r}")
                    continue
                current_continuation["stitch"] = patch["stitch"]
            continuation = current_continuation
            filled_ops.append({"op": "set_continuation", "continuation": dict(continuation)})
            summary.append(
                f"Set shot joining: overlap {continuation['overlap_frames']} frames, "
                f"stitch {'on' if continuation['stitch'] else 'off'}"
            )
            changes.append({
                "op": "set_continuation", "summary": summary[-1],
                "before": before_continuation, "after": dict(continuation),
            })

        else:
            errors.append(f"{context}: unknown op {op!r}")

    mode_caps = modes_caps.get(mode) or {}
    media_list = list(media.values())
    if mode == "t2v" and media_list:
        errors.append("t2v mode does not accept media")
    elif mode == "i2v":
        if len(media_list) != 1 or media_list[0]["role"] != "first":
            errors.append("i2v mode requires exactly one media entry with role 'first'")
    elif mode == "flf":
        roles = sorted(m["role"] for m in media_list)
        if len(media_list) != 2 or roles != ["first", "last"]:
            errors.append("flf mode requires exactly one 'first' and one 'last' media entry")
    elif mode == "director" and style == "chain":
        errors.extend(_validate_chain_end_state(segments, segment_order, media_list, settings, mode_caps, total_duration()))
    elif mode == "director" and style == "timeline":
        max_keyframes = mode_caps.get("max_keyframes")
        keyframe_count = sum(1 for m in media_list if m["role"] == "keyframe")
        if max_keyframes is not None and keyframe_count > max_keyframes:
            errors.append(f"at most {max_keyframes} keyframes are allowed, got {keyframe_count}")
        duration = settings.get("duration")
        for entry in media_list:
            if entry["role"] != "keyframe":
                continue
            at = entry.get("at")
            if not isinstance(at, (int, float)) or at < 0 or (isinstance(duration, (int, float)) and at > duration):
                errors.append(f"keyframe {entry['id']}: 'at' must be within [0, settings.duration], got {at!r}")

    if audio and not mode_caps.get("audio"):
        errors.append(f"this preset's {mode!r} mode does not support audio tracks")

    if style != "chain":
        duration = settings.get("duration")
        max_duration = mode_caps.get("max_duration", limits.get("max_duration"))
        if max_duration is not None and isinstance(duration, (int, float)) and duration > max_duration:
            errors.append(f"settings.duration {duration} exceeds the allowed maximum {max_duration}")

    if errors:
        raise _OperationError("; ".join(errors))

    return filled_ops, summary, changes


def _validate_chain_end_state(
    segments: Dict[str, Dict[str, Any]],
    segment_order: List[str],
    media_list: List[Dict[str, Any]],
    settings: Dict[str, Any],
    mode_caps: Dict[str, Any],
    chain_duration: Optional[float],
) -> List[str]:
    """The chain-style half of `normalize.py`'s rules, checked here so the model
    gets them back as tool errors instead of a rejected generation."""
    errors: List[str] = []

    max_segments = mode_caps.get("max_segments", _DEFAULT_MAX_SEGMENTS)
    if max_segments is not None and len(segment_order) > max_segments:
        errors.append(f"chain style allows at most {max_segments} shots, got {len(segment_order)}")
    if not segment_order:
        errors.append("a chain needs at least one shot")

    frames_cap = mode_caps.get("max_frames_per_segment", _CHAIN_MAX_FRAMES_DEFAULT)
    fps = settings.get("fps")
    for seg_id in segment_order:
        segment = segments[seg_id]
        frames = segment.get("frames")
        if not isinstance(frames, int) or isinstance(frames, bool) or not (1 <= frames <= frames_cap):
            seconds = f" ({frames_cap / fps:.2f}s at {fps} fps)" if isinstance(fps, (int, float)) and fps > 0 else ""
            errors.append(
                f"segment {seg_id!r}: needs a length -- frames must be an int between 1 and "
                f"{frames_cap}{seconds}, got {frames!r}"
            )
        steps = segment.get("steps")
        if steps is not None and (not isinstance(steps, int) or not (_CHAIN_STEPS_RANGE[0] <= steps <= _CHAIN_STEPS_RANGE[1])):
            errors.append(f"segment {seg_id!r}: steps must be between {_CHAIN_STEPS_RANGE[0]} and {_CHAIN_STEPS_RANGE[1]}, got {steps!r}")
        cfg = segment.get("cfg")
        if cfg is not None and (not isinstance(cfg, (int, float)) or not (_CHAIN_CFG_RANGE[0] <= cfg <= _CHAIN_CFG_RANGE[1])):
            errors.append(f"segment {seg_id!r}: cfg must be between {_CHAIN_CFG_RANGE[0]} and {_CHAIN_CFG_RANGE[1]}, got {cfg!r}")

    keyframes = [m for m in media_list if m["role"] == "keyframe"]
    max_keyframes = mode_caps.get("max_keyframes", _DEFAULT_MAX_KEYFRAMES)
    if max_keyframes is not None and len(keyframes) > max_keyframes:
        errors.append(f"at most {max_keyframes} keyframes are allowed, got {len(keyframes)}")
    for entry in keyframes:
        at = entry.get("at")
        if not isinstance(at, (int, float)) or at < 0 or (isinstance(chain_duration, (int, float)) and at > chain_duration):
            window = f"{chain_duration:.2f}" if isinstance(chain_duration, (int, float)) else "the chain's total duration"
            errors.append(
                f"keyframe {entry['id']}: 'at' is seconds from the start of the chain and must be within "
                f"[0, {window}], got {at!r}"
            )

    return errors


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
            "or Video Director settings -- call this first."
            "{{#if update_video_director}} Use it before proposing changes with "
            "update_video_director.{{/if}}"
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
            "capability summary naming exactly which operations, roles and limits apply right "
            "now."
            "{{#if update_video_director}} Use update_video_director to propose "
            "changes.{{/if}}"
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
                "how_to_edit": _how_to_edit(flat["mode"], flat["style"], capabilities),
            }
            return ToolResult(success=True, data=json.dumps(result))
        except Exception as e:
            logger.error(f"Error reading video director from session metadata: {e}")
            return ToolResult(success=False, data="", error=str(e))


class UpdateVideoDirectorTool(BaseTool):
    """Proposes edits to the user's Video Director document."""

    modes = ["generation"]
    icon = "clapperboard"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return video_director_active(form_state)

    @property
    def name(self) -> str:
        return "update_video_director"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Changes the Video Director document on your generation form for you."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "When the user asks to change their video shots, segments, timeline, keyframes, "
            "audio tracks, how shots join (continue vs cut), mode, or settings on an active "
            "Video Director -- propose changes with this tool."
            "{{#if get_video_director}} Always call get_video_director first to see the "
            "current document, style, and which fields are valid before proposing "
            "operations.{{/if}} When the user instead asks for prompt IDEAS, VERSIONS, or "
            "ALTERNATIVES for one or more shots to pick from -- not a document change -- do "
            "NOT call this tool; emit one tag per version in your reply text instead: "
            '<tool_action type="update_director_segment" segment_index="N" segment_id="ID">'
            "proposed prompt text</tool_action>, using the index and id from "
            "get_video_director's segments. To attach existing form media to a shot: "
            f"{_MEDIA_OPERATION_EXAMPLE}."
        )

    @property
    def description(self) -> str:
        return (
            "Edit the user's Video Director document. `operations` is a JSON ARRAY of objects, "
            "each with an \"op\" key -- one `upsert_segment` PER SHOT, carrying that shot's own "
            "`prompt` and `duration` (seconds). Never combine shots in one prompt or write "
            "`[Shot 1]`/`[Scene 2]` markers in a prompt (a segment IS one shot; rejected). "
            "Example: "
            f"{_CALL_EXAMPLE}. The user approves before anything is applied.\n\n"
            "{{#if get_video_director}}get_video_director's how_to_edit/capabilities explain "
            "this preset's exact rules (chain shot length and join derivation, keyframe "
            "placement, audio, references) and the ops valid now.{{/if}} An invalid operation "
            "rejects the call (no approval offered) -- fix and retry.\n\n"
            "Modes: 't2v' (no media), 'i2v' (one 'first' media), 'flf' ('first'+'last' media), "
            "'director' ('chain': ordered separately-generated shots, when the preset declares "
            "segment_routing; else 'timeline': one generation, segments placed by start/end "
            "time).\n\n"
            "Operations (\"op\" key):\n"
            "- set_mode {mode}\n"
            "- set_settings {settings: {fps?, duration?, resolution?, seed?}}: partial merge; "
            "'duration' rejected in chain style (it's the sum of the shots there).\n"
            "- set_prompt {prompt} / set_negative_prompt {negative_prompt}: the global prompts.\n"
            "- upsert_segment {segment: {id?, prompt?, negative_prompt?, start?, end?, duration?, "
            "frames?, sub_type_override?, seed?, steps?, cfg?, references?}}: "
            "upserts by id ('id' updates that shot, absent adds a new one). "
            "duration/frames/sub_type_override/steps/cfg are chain-only; start/end (start<end) "
            "are timeline-only. 'references' -- {form_media: {field, label?, path?}} or {path} "
            "entries -- picks a subset of the reference pool for this shot, where "
            "capabilities.references is 'per_shot'.\n"
            "- remove_segment {id} / reorder_segments {ids} (a permutation of current ids).\n"
            "- upsert_media {media: {id?, role, segment_id?, at?, strength?, path?, "
            "form_media?}}: role 'first'/'last'/'keyframe' (chain style: 'last' pairs with "
            "'first' on the SAME shot), strength clamps to [0,1]. Address with `path` or "
            "`form_media: {field, label?, path?}` (field name from get_form_state).\n"
            "- remove_media {id}.\n"
            "- upsert_audio {audio: {id?, role?, path, start?, trim_start?, length?}}: only "
            "where the mode declares 'audio'; length defaults to the composition's total "
            "length.\n"
            "- remove_audio {id}.\n"
            "- set_continuation {continuation: {overlap_frames?, stitch?}}: chain style only.\n\n"
            "Validation runs against the END result of every operation together, not each in "
            "isolation -- e.g. switching to 'flf' and adding its media in the same call is valid "
            "even though neither op alone satisfies it."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    # This one stays the bare array: it documents the VALUE of a
                    # single argument, and a whole <tool_call> block here invites
                    # the model to nest the call inside its own operations list.
                    "description": (
                        "Ordered ARRAY of operation objects applied to the Video Director "
                        "document, one per change -- one upsert_segment per shot. This "
                        "argument's value looks like: "
                        f"{_OPERATIONS_EXAMPLE}"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": list(_ALL_OPS)},
                        },
                        "required": ["op"],
                    },
                },
                "reason": {
                    "type": "string",
                    "description": "Optional explanation for why these changes are proposed.",
                },
            },
            "required": ["operations"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        raw_operations = kwargs.get("operations")
        if not raw_operations:
            return ToolResult(
                success=False,
                data="",
                error=f"No operations provided. Specify at least one: {_CALL_EXAMPLE}",
            )
        operations, problem = coerce_operations(raw_operations)
        if problem:
            return ToolResult(success=False, data="", error=problem)

        form_state = context.session_metadata.get("form_state") or {}
        vd = form_state.get("video_director")
        if not vd or not vd.get("active"):
            return ToolResult(
                success=False,
                data="",
                error="The current preset/mode has no Video Director document active.",
            )

        doc = vd.get("doc") or {}
        capabilities = apply_preset_mode_overlay(vd.get("capabilities") or {}, form_state.get("mode"))
        form_data = form_state.get("form_data") or {}
        flat = _flatten(doc, capabilities, form_data)

        try:
            filled_ops, summary, changes = _apply_operations(operations, flat, capabilities, form_data)
        except _OperationError as e:
            return ToolResult(success=False, data="", error=str(e))

        result: Dict[str, Any] = {
            "status": "pending_approval",
            "operations": filled_ops,
            "summary": summary,
            "operation_count": len(filled_ops),
        }
        if kwargs.get("reason"):
            result["reason"] = kwargs["reason"]

        preview = ToolApprovalPreview(action="Update Video Director", items=list(summary), changes=changes)
        return ToolResult(success=True, data=json.dumps(result), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        operations, problem = coerce_operations(kwargs.get("operations") or [])
        if problem:
            return ToolResult(success=False, data="", error=problem)

        form_state = context.session_metadata.get("form_state") or {}
        vd = form_state.get("video_director") or {}
        doc = vd.get("doc") or {}
        capabilities = apply_preset_mode_overlay(vd.get("capabilities") or {}, form_state.get("mode"))
        form_data = form_state.get("form_data") or {}
        flat = _flatten(doc, capabilities, form_data)

        try:
            filled_ops, summary, _changes = _apply_operations(operations, flat, capabilities, form_data)
        except _OperationError as e:
            return ToolResult(success=False, data="", error=str(e))

        return ToolResult(
            success=True,
            data=json.dumps({
                "action": "apply_video_director_ops",
                "operations": filled_ops,
                "summary": summary,
            }),
        )
