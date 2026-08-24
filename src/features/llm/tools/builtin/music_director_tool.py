"""Chat tools for reading and editing the generation form's Music Director document.

The frontend sends the live editor state as `form_state.music_director` on the
session metadata: `{"active": bool, "doc": <MusicDirectorValue|None>,
"capabilities": <raw preset var vars.music_director|None>}`. `doc` mirrors
`frontend/src/lib/types/musicDirector.ts` (MusicDirectorValue) -- unlike
Video Director there is no chain/timeline duality, so `_read_doc` is a
light defensive re-shape rather than a per-mode translation.

There is no `set_mode` operation and no `mode` field a caller may write:
Music Director has no mode switch (mirrors Video Director's own modeless
`director`/style precedent, taken one step further here -- see
`docs/music-director.md` "Composition modes"). `mode` is always DERIVED from
the document's shape (sections present, a bare reference pool, the
instrumental toggle, an extend/repaint source) by `_derive_mode`, mirroring
`deriveMusicDirectorMode` in `frontend/src/lib/utils/musicDirector.ts`
exactly. Both tools report the derived mode back; neither accepts it as input.

These tools never mutate `doc` themselves: `update_music_director`'s confirmed
result carries only the requested operations (server-assigned ids filled in),
and the frontend applies them to its own editor state.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import music_director_active
from src.features.llm.tools.tool_call_rescue import decode_payload, demangle_quote_tokens
from src.features.music_director import apply_preset_mode_overlay

logger = logging.getLogger(__name__)

# Mirrors MUSIC_MODE_ORDER in frontend/src/lib/types/musicDirector.ts exactly
# -- deriveMusicDirectorMode's precedence checks this order.
_MODE_ORDER = ("t2m", "song", "style", "director", "extend", "repaint")
_SECTION_MODES = frozenset({"song", "director"})
_SECTION_KINDS = (
    "intro", "verse", "pre_chorus", "chorus", "post_chorus", "bridge",
    "instrumental", "solo", "outro",
)
_SETTINGS_FIELDS = ("duration", "bpm", "key", "time_signature")
_ALL_OPS = (
    "set_description", "set_settings", "set_instrumental",
    "upsert_section", "remove_section", "reorder_sections",
    "upsert_reference", "remove_reference",
)

# Every field a number may arrive in, quoted or not -- same defense as
# video_director_tool's _NUMERIC_FIELDS.
_NUMERIC_FIELDS = frozenset({"duration", "bpm", "duration_hint"})

# The payload object each op carries, and the fields that belong in it -- a
# model that flattened those fields onto the operation itself is read as if
# it had nested them (see video_director_tool._normalize_op).
_OP_PAYLOADS = {
    "set_settings": ("settings", _SETTINGS_FIELDS),
    "upsert_section": ("section", ("id", "kind", "lyrics", "style_hint", "duration_hint", "references")),
    "upsert_reference": ("reference", ("id", "media")),
}

_OPERATIONS_EXAMPLE = (
    '[{"op": "set_description", "description": "warm 90s boom-bap, vinyl crackle and dusty drums, husky '
    'female vocal"}, {"op": "upsert_section", "section": {"kind": "verse", "lyrics": "Riding through the '
    'city lights, chasing echoes of the night..."}}, {"op": "upsert_section", "section": {"kind": "chorus", '
    '"lyrics": "We rise, we shine, we never fall behind..."}}]'
)
# Same rationale as video_director_tool._CALL_EXAMPLE: the whole call, never a
# bare `operations = [...]` assignment, so a local model that concatenates
# this into a <tool_action> tag still produces something rescuable.
_CALL_EXAMPLE = (
    '<tool_call>{"name": "update_music_director", "arguments": {"operations": '
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
    payload back under its own key. Mirrors video_director_tool._normalize_op."""
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
    Mirrors video_director_tool.coerce_operations exactly."""
    if isinstance(operations, dict):
        return [operations], None
    if isinstance(operations, list):
        return operations, None
    if isinstance(operations, str):
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


def _truncate(text: Any, limit: int = 60) -> str:
    text = text if isinstance(text, str) else ""
    return text[:limit] + ("..." if len(text) > limit else "")


def _new_section_id(existing_ids: List[str]) -> str:
    """Mirrors `mintSectionId` in frontend/src/lib/utils/musicDirector.ts --
    same `section-N` naming as normalize.py's own default section id."""
    used = set(existing_ids)
    n = len(existing_ids) + 1
    sid = f"section-{n}"
    while sid in used:
        n += 1
        sid = f"section-{n}"
    return sid


def _new_reference_id(existing_ids: List[str]) -> str:
    """Mirrors `mintReferenceId` in frontend/src/lib/utils/musicDirector.ts."""
    used = set(existing_ids)
    n = len(existing_ids) + 1
    rid = f"ref-{n}"
    while rid in used:
        n += 1
        rid = f"ref-{n}"
    return rid


_KIND_LABELS = {
    "intro": "Intro", "verse": "Verse", "pre_chorus": "Pre-Chorus", "chorus": "Chorus",
    "post_chorus": "Post-Chorus", "bridge": "Bridge", "instrumental": "Instrumental",
    "solo": "Solo", "outro": "Outro",
}


def _build_kind_aliases() -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for kind, label in _KIND_LABELS.items():
        aliases[kind] = kind
        aliases[kind.replace("_", "-")] = kind
        aliases[kind.replace("_", " ")] = kind
        aliases[label.lower()] = kind
    return aliases


_KIND_ALIASES = _build_kind_aliases()
_TRAILING_COUNTER_RE = re.compile(r"[\s#-]+\d+$")


def _canonicalize_kind(name: Any) -> str:
    """Matches a Song structure segment's `name` back to a `SectionKind` --
    mirrors `canonicalizeSectionKind`/`matchSectionKind` in
    utils/musicDirector.ts exactly, including the unconditional 'verse'
    fallback for an unrecognized name (this is a read-back for the LLM, not
    a validation gate -- the frontend's own `validateMusicDirector` is what
    rejects an unrecognized name before submission)."""
    trimmed = (name or "").strip()
    if not trimmed:
        return "verse"
    lower = trimmed.lower()
    without_counter = _TRAILING_COUNTER_RE.sub("", lower).strip()
    return _KIND_ALIASES.get(without_counter) or _KIND_ALIASES.get(lower) or "verse"


def _references_gate(mode: str, capabilities: Dict[str, Any]) -> Tuple[bool, bool]:
    """`(allowed, required)` for the document's `references` pool given `mode`
    -- mirrors normalize.py's `_references_gate` / utils/musicDirector.ts's
    `musicReferencesGate` exactly."""
    modes = capabilities.get("modes") or {}
    if mode == "style":
        return True, True
    if mode == "song":
        return "style" in modes, False
    if mode == "director":
        mode_caps = modes.get("director") or {}
        return mode_caps.get("references") in ("whole", "per_section"), False
    return False, False


def _derive_mode(read: Dict[str, Any], capabilities: Dict[str, Any]) -> str:
    """Mirrors `deriveMusicDirectorMode` in utils/musicDirector.ts exactly --
    the precedence order (extend > repaint > style > director > instrumental
    > song > t2m) matters and must not be reordered."""
    modes = capabilities.get("modes") or {}
    enabled = [m for m in _MODE_ORDER if m in modes]
    if not enabled:
        return "t2m"

    if "extend" in enabled and read.get("extend_source"):
        return "extend"
    repaint = read.get("repaint") or {}
    if "repaint" in enabled and isinstance(repaint, dict) and repaint.get("source"):
        return "repaint"

    sections = read.get("sections") or []
    references = read.get("references") or []
    if "style" in enabled and not sections and references:
        return "style"

    # More than one section is what distinguishes a `director` arrangement
    # from `song`'s single-lyrics shorthand -- per-section style hints/
    # duration hints/references no longer exist as an editable surface (the
    # Song structure segment editor has no UI for them), so section COUNT is
    # the only signal left. Mirrors deriveMusicDirectorMode in
    # utils/musicDirector.ts exactly.
    if "director" in enabled and len(sections) > 1:
        return "director"

    if read.get("instrumental"):
        if "t2m" in enabled:
            return "t2m"
        if "song" in enabled:
            return "song"

    if "song" in enabled and sections:
        return "song"
    if "t2m" in enabled:
        return "t2m"
    if "song" in enabled:
        return "song"
    return enabled[0]


def _normalize_segment_as_section_read(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    """Reads one Song structure segment (frontend Segment shape: id, content,
    type, enabled, name -- `MusicDirectorValue.segments`) as a wire-section-
    shaped dict (id, kind, lyrics) so the rest of this module's mode
    derivation/operation machinery, written against that shape, needs no
    change. Mirrors `wireSection`/`canonicalizeSectionKind` in
    utils/musicDirector.ts. Disabled or break-type segments are skipped
    (return None), matching the frontend's own filter before it ever builds
    `sections`. Per-section style_hint/duration_hint/references have no
    editable surface any more (Segment carries none of them) -- always None
    here, same as the frontend's own read-back would show."""
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("type") == "break" or raw.get("enabled") is False:
        return None
    return {
        "id": raw.get("id") or f"section-{index + 1}",
        "kind": _canonicalize_kind(raw.get("name")),
        "lyrics": raw.get("content") or "",
        "style_hint": None,
        "duration_hint": None,
        "references": None,
    }


def _normalize_reference_read(raw: Any, index: int) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    return {"id": raw.get("id") or f"ref-{index + 1}", "media": media}


def _read_doc(doc: Dict[str, Any], capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """A defensively re-shaped read model of `doc`, plus its derived mode --
    mirrors `normalizeMusicDirectorValue` + `deriveMusicDirectorMode` in
    utils/musicDirector.ts. Idempotent, no I/O."""
    limits = capabilities.get("limits") or {}
    settings_raw = doc.get("settings") or {}
    read: Dict[str, Any] = {
        "description": doc.get("description") or "",
        "instrumental": bool(doc.get("instrumental", False)),
        "sections": [
            section
            for section in (
                _normalize_segment_as_section_read(s, i) for i, s in enumerate(doc.get("segments") or [])
            )
            if section is not None
        ],
        "references": [_normalize_reference_read(r, i) for i, r in enumerate(doc.get("references") or [])],
        "extend_source": doc.get("extend_source"),
        "repaint": doc.get("repaint"),
        "settings": {
            "duration": settings_raw.get("duration", limits.get("default_duration", 120)),
            "seed": settings_raw.get("seed", -1),
            "bpm": settings_raw.get("bpm"),
            "key": settings_raw.get("key"),
            "time_signature": settings_raw.get("time_signature"),
        },
    }
    read["mode"] = _derive_mode(read, capabilities)
    return read


def _available_ops(capabilities: Dict[str, Any]) -> List[str]:
    modes = capabilities.get("modes") or {}
    ops = ["set_description", "set_settings", "set_instrumental"]
    if "song" in modes or "director" in modes:
        ops += ["upsert_section", "remove_section", "reorder_sections"]
    director_caps = modes.get("director") or {}
    references_possible = (
        "style" in modes
        or ("song" in modes and "style" in modes)
        or ("director" in modes and director_caps.get("references") in ("whole", "per_section"))
    )
    if references_possible:
        ops += ["upsert_reference", "remove_reference"]
    return ops


def _capability_summary(capabilities: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Everything the model may do to THIS document right now, derived from
    the preset's declared capabilities for the CURRENT (derived) mode --
    never hardcoded family prose."""
    modes = capabilities.get("modes") or {}
    limits = capabilities.get("limits") or {}
    settings_cap = capabilities.get("settings") or {}
    mode_caps = modes.get(mode) or {}
    refs_allowed, refs_required = _references_gate(mode, capabilities)

    sections_summary: Dict[str, Any]
    if mode in _SECTION_MODES:
        sections_summary = {
            "supported": True,
            "required": True,
            "max_sections": mode_caps.get("max_sections", 12) if mode == "director" else None,
            "style_hint_supported": bool(mode_caps.get("per_section_prompts")),
            "per_section_references": mode == "director" and mode_caps.get("references") == "per_section",
        }
    else:
        sections_summary = {"supported": False}

    return {
        "enabled_modes": [m for m in _MODE_ORDER if m in modes],
        "current_mode": mode,
        "default_duration": limits.get("default_duration"),
        "max_duration": limits.get("max_duration"),
        "settings_fields": [k for k in ("bpm", "key", "time_signature") if settings_cap.get(k)],
        "sections": sections_summary,
        "references": {
            "supported": refs_allowed,
            "required": refs_required,
            "max_reference_seconds": mode_caps.get("max_reference_seconds") if mode == "style" else None,
        },
        "available_operations": _available_ops(capabilities),
    }


def _how_to_edit(mode: str, capabilities: Dict[str, Any]) -> str:
    modes = capabilities.get("modes") or {}
    lines = [
        "Call update_music_director with one or more operations to change this document: "
        + ", ".join(_available_ops(capabilities))
        + ". There is no set_mode -- the mode is DERIVED from the document's shape (an extend/repaint "
        "source picked in the editor wins outright; otherwise a bare reference pool with no sections "
        "derives 'style'; more than one section derives 'director'; the Instrumental toggle derives "
        "'t2m'; one or more plain sections derives 'song') and is always reported back by "
        "get_music_director, never chosen directly.",
    ]
    if capabilities.get("form_owns_settings"):
        lines.append(
            "This preset's generation form owns 'description'/'settings.duration'/the instrumental "
            "toggle as plain form fields (see get_form_state) -- set_description/set_instrumental/"
            "set_settings still work here but only change this document's own (unused) copies; to "
            "actually change the caption, duration, or instrumental toggle, use update_form_settings "
            "on the form's 'description'/'duration'/'instrumental' fields instead."
        )
    else:
        lines.append(
            "Write 'description' as a compact style prompt covering three things in order: genre/era, "
            "instrumentation and production texture, then vocal character and mood -- e.g. 'warm 90s "
            "boom-bap, vinyl crackle and dusty drums, husky female vocal'. Keep it dense; it is the only "
            "place non-structural style lives."
        )
    if "song" in modes or "director" in modes:
        lines.append(
            "Sections carry the lyrics: upsert_section per section, using 'kind' from "
            + ", ".join(_SECTION_KINDS)
            + " (defaults to 'verse'). A single plain section is 'song'; splitting the lyrics into "
            "several sections (intro/verse/chorus/...) is how you build a 'director' arrangement, when "
            "this preset enables it."
        )
        director_caps = modes.get("director") or {}
        if director_caps.get("per_section_prompts"):
            lines.append("This preset supports a per-section 'style_hint' override on upsert_section.")
        if director_caps.get("references") == "per_section":
            lines.append(
                "This preset selects references per section: upsert_section's 'references' is a list of "
                "reference ids already in this document's pool (see get_music_director's 'references')."
            )
    if "style" in modes or ("song" in modes and "style" in modes) or (
        "director" in modes and (modes.get("director") or {}).get("references") in ("whole", "per_section")
    ):
        lines.append(
            "upsert_reference {reference: {id?, media: {path, type?}}} adds an audio reference to the "
            "document's pool (path is an already-known storage path); remove_reference {id} removes one. "
            "'style' mode requires at least one reference and has no sections."
        )
    return " ".join(lines)


def _apply_operations(
    operations: List[Dict[str, Any]], read: Dict[str, Any], capabilities: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply `operations` to a working copy derived from `read`, validating the
    END state (not each operation in isolation). Returns
    `(filled_operations, summary_lines)` or raises `_OperationError` carrying
    every problem found.
    """
    limits = capabilities.get("limits") or {}

    description = read["description"]
    instrumental = read["instrumental"]
    settings = dict(read["settings"])
    sections: Dict[str, Dict[str, Any]] = {s["id"]: dict(s) for s in read["sections"]}
    section_order: List[str] = [s["id"] for s in read["sections"]]
    references: Dict[str, Dict[str, Any]] = {r["id"]: dict(r) for r in read["references"]}
    reference_order: List[str] = [r["id"] for r in read["references"]]

    filled_ops: List[Dict[str, Any]] = []
    summary: List[str] = []
    errors: List[str] = []

    for i, raw_op in enumerate(operations):
        context = f"operations[{i}]"
        raw_op = _normalize_op(raw_op)
        if not isinstance(raw_op, dict) or not raw_op.get("op"):
            errors.append(
                f"{context}: every operation needs an 'op' naming what it does, one of "
                f"{list(_available_ops(capabilities))}"
            )
            continue
        op = raw_op["op"]

        if op == "set_description":
            value = raw_op.get("description")
            if not isinstance(value, str):
                errors.append(f"{context}: 'description' must be a string")
                continue
            description = value
            filled_ops.append({"op": "set_description", "description": description})
            summary.append(f'Set description: "{_truncate(description)}"')

        elif op == "set_instrumental":
            value = raw_op.get("instrumental")
            if not isinstance(value, bool):
                errors.append(f"{context}: 'instrumental' must be true or false")
                continue
            instrumental = value
            filled_ops.append({"op": "set_instrumental", "instrumental": value})
            summary.append(f"Set instrumental: {value}")

        elif op == "set_settings":
            patch = raw_op.get("settings")
            if not isinstance(patch, dict):
                errors.append(f"{context}: 'settings' must be an object")
                continue
            applied = {k: patch[k] for k in _SETTINGS_FIELDS if k in patch}
            if not applied:
                errors.append(f"{context}: 'settings' has no recognized fields ({', '.join(_SETTINGS_FIELDS)})")
                continue
            bad = False
            if "duration" in applied:
                duration = applied["duration"]
                max_duration = limits.get("max_duration")
                if not isinstance(duration, (int, float)) or duration <= 0:
                    errors.append(f"{context}: 'duration' must be a positive number of seconds, got {duration!r}")
                    bad = True
                elif max_duration is not None and duration > max_duration:
                    errors.append(f"{context}: 'duration' {duration} exceeds the allowed maximum {max_duration}")
                    bad = True
            if "bpm" in applied and applied["bpm"] is not None:
                bpm = applied["bpm"]
                if not isinstance(bpm, (int, float)) or bpm <= 0:
                    errors.append(f"{context}: 'bpm' must be a positive number, got {bpm!r}")
                    bad = True
            for key in ("key", "time_signature"):
                if key in applied and applied[key] is not None and not isinstance(applied[key], str):
                    errors.append(f"{context}: '{key}' must be a string, got {applied[key]!r}")
                    bad = True
            if bad:
                continue
            settings.update(applied)
            filled_ops.append({"op": "set_settings", "settings": applied})
            summary.append("Update settings: " + ", ".join(f"{k}={v}" for k, v in applied.items()))

        elif op == "upsert_section":
            patch = raw_op.get("section")
            if not isinstance(patch, dict):
                errors.append(f"{context}: 'section' must be an object")
                continue

            requested_id = patch.get("id")
            is_new = requested_id not in sections
            sid = requested_id if requested_id else _new_section_id(section_order)
            current = dict(sections[sid]) if not is_new else {
                "id": sid, "kind": "verse", "lyrics": "", "style_hint": None,
                "duration_hint": None, "references": None,
            }

            bad = False
            if "kind" in patch:
                kind = patch["kind"]
                if kind not in _SECTION_KINDS:
                    errors.append(f"{context}: 'kind' must be one of {list(_SECTION_KINDS)}, got {kind!r}")
                    bad = True
                else:
                    current["kind"] = kind
            if "lyrics" in patch:
                lyrics = patch["lyrics"]
                if not isinstance(lyrics, str):
                    errors.append(f"{context}: 'lyrics' must be a string")
                    bad = True
                else:
                    current["lyrics"] = lyrics
            if "style_hint" in patch:
                style_hint = patch["style_hint"]
                if style_hint is not None and not isinstance(style_hint, str):
                    errors.append(f"{context}: 'style_hint' must be a string or null")
                    bad = True
                else:
                    current["style_hint"] = style_hint
            if "duration_hint" in patch:
                duration_hint = patch["duration_hint"]
                if duration_hint is not None and (not isinstance(duration_hint, (int, float)) or duration_hint <= 0):
                    errors.append(f"{context}: 'duration_hint' must be a positive number of seconds or null, got {duration_hint!r}")
                    bad = True
                else:
                    current["duration_hint"] = duration_hint
            if "references" in patch:
                refs = patch["references"]
                if refs is not None and (not isinstance(refs, list) or not all(isinstance(r, str) for r in refs)):
                    errors.append(f"{context}: 'references' must be a list of reference ids, or null")
                    bad = True
                else:
                    current["references"] = refs
            if bad:
                continue

            current["id"] = sid
            sections[sid] = current
            if sid not in section_order:
                section_order.append(sid)

            filled_ops.append({"op": "upsert_section", "section": dict(current)})
            label = current.get("lyrics") or current.get("kind") or sid
            summary.append(f'{"Add" if is_new else "Update"} section "{_truncate(label)}" ({current["kind"]})')

        elif op == "remove_section":
            sid = raw_op.get("id")
            if not sid or sid not in sections:
                errors.append(f"{context}: unknown section id {sid!r}")
                continue
            del sections[sid]
            section_order = [s for s in section_order if s != sid]
            filled_ops.append({"op": "remove_section", "id": sid})
            summary.append(f"Remove section {sid}")

        elif op == "reorder_sections":
            ids = raw_op.get("ids")
            if not isinstance(ids, list) or sorted(map(str, ids)) != sorted(map(str, section_order)):
                errors.append(f"{context}: 'ids' must be a permutation of the current section ids {section_order}")
                continue
            section_order = list(ids)
            filled_ops.append({"op": "reorder_sections", "ids": section_order})
            summary.append("Reorder sections: " + ", ".join(section_order))

        elif op == "upsert_reference":
            patch = raw_op.get("reference")
            if not isinstance(patch, dict):
                errors.append(f"{context}: 'reference' must be an object")
                continue
            media = patch.get("media")
            if not isinstance(media, dict) or not (media.get("path") or media.get("relative_path")):
                errors.append(f"{context}: 'media' must be an object with a 'path' (or 'relative_path')")
                continue

            requested_id = patch.get("id")
            is_new = requested_id not in references
            rid = requested_id if requested_id else _new_reference_id(reference_order)
            entry = {"id": rid, "media": dict(media)}
            references[rid] = entry
            if rid not in reference_order:
                reference_order.append(rid)

            filled_ops.append({"op": "upsert_reference", "reference": dict(entry)})
            summary.append(f'{"Add" if is_new else "Update"} reference: {media.get("path") or media.get("relative_path")}')

        elif op == "remove_reference":
            rid = raw_op.get("id")
            if not rid or rid not in references:
                errors.append(f"{context}: unknown reference id {rid!r}")
                continue
            del references[rid]
            reference_order = [r for r in reference_order if r != rid]
            filled_ops.append({"op": "remove_reference", "id": rid})
            summary.append(f"Remove reference {rid}")

        else:
            errors.append(f"{context}: unknown op {op!r}")

    # Validate the END state -- e.g. adding a first section and switching a
    # bare t2m/style document into 'song'/'director' shape is only valid
    # together, never checked against each intermediate op.
    end_state = {
        "instrumental": instrumental,
        "sections": [sections[sid] for sid in section_order],
        "references": [references[rid] for rid in reference_order],
        "extend_source": read.get("extend_source"),
        "repaint": read.get("repaint"),
    }
    mode = _derive_mode(end_state, capabilities)
    mode_caps = (capabilities.get("modes") or {}).get(mode) or {}

    if mode in _SECTION_MODES:
        if not section_order:
            errors.append(f"resulting mode {mode!r} requires at least one section -- add one with upsert_section")
        elif mode == "director":
            max_sections = mode_caps.get("max_sections", 12)
            if len(section_order) > max_sections:
                errors.append(f"director mode allows at most {max_sections} sections, got {len(section_order)}")

        per_section_prompts = bool(mode_caps.get("per_section_prompts"))
        per_section_references = mode == "director" and mode_caps.get("references") == "per_section"
        reference_ids = set(reference_order)
        for sid in section_order:
            section = sections[sid]
            if section.get("style_hint") and not per_section_prompts:
                errors.append(f"section {sid!r}: style_hint is not supported by this preset's {mode!r} mode")
            section_refs = section.get("references")
            if section_refs:
                if not per_section_references:
                    errors.append(f"section {sid!r}: per-section references are not supported by this preset's {mode!r} mode")
                else:
                    bad_refs = [r for r in section_refs if r not in reference_ids]
                    if bad_refs:
                        errors.append(f"section {sid!r}: references {bad_refs} are not in this document's reference pool")
    elif section_order:
        errors.append(f"resulting mode {mode!r} does not accept sections")

    refs_allowed, refs_required = _references_gate(mode, capabilities)
    if not refs_allowed and reference_order:
        errors.append(f"resulting mode {mode!r} does not accept references")
    elif refs_required and not reference_order:
        errors.append(f"resulting mode {mode!r} requires at least one reference")

    duration = settings.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append("settings.duration must be a positive number of seconds")

    if errors:
        raise _OperationError("; ".join(errors))

    return filled_ops, summary


class GetMusicDirectorTool(BaseTool):
    """Reads the Music Director document currently loaded on the user's form."""

    modes = ["generation"]
    icon = "music"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return music_director_active(form_state)

    @property
    def name(self) -> str:
        return "get_music_director"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Reads the Music Director document on your generation form."

    @property
    def hint(self) -> str:
        return (
            "When the user asks about their song's lyrics, sections, references, or Music "
            "Director settings -- call this first."
            "{{#if update_music_director}} Use it before proposing changes with "
            "update_music_director.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Read the Music Director document currently loaded on the user's generation "
            "form, if the active preset/mode exposes one. Music Director composes a music "
            "generation; its mode is DERIVED from the document's shape, never chosen -- "
            "'t2m' (style description only, instrumental), 'song' (one or more plain lyric "
            "sections), 'style' (reference-audio-conditioned, no sections), 'director' (a "
            "structured section timeline with per-section style hints and/or references), "
            "'extend' or 'repaint' (continuing or in-place-regenerating an existing track). "
            "Returns the derived mode, the style description, the instrumental toggle, "
            "every section (kind, lyrics, and where supported style_hint/duration_hint/"
            "references), the reference-audio pool, settings (duration, seed, and bpm/key/"
            "time_signature where the preset exposes them as real fields), and a capability "
            "summary naming exactly which operations, section kinds and limits apply right "
            "now."
            "{{#if update_music_director}} Use update_music_director to propose "
            "changes.{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        form_state = context.session_metadata.get("form_state") or {}
        md = form_state.get("music_director")
        if not md or not md.get("active"):
            return ToolResult(
                success=False,
                data="",
                error=(
                    "The current preset/mode has no Music Director document active. "
                    "Use get_form_state and update_form_settings for regular form fields instead."
                ),
            )

        try:
            doc = md.get("doc") or {}
            capabilities = apply_preset_mode_overlay(md.get("capabilities") or {}, form_state.get("mode"))
            read = _read_doc(doc, capabilities)

            result = {
                "mode": read["mode"],
                "description": read["description"],
                "instrumental": read["instrumental"],
                "sections": read["sections"],
                "references": read["references"],
                "extend_source": read["extend_source"],
                "repaint": read["repaint"],
                "settings": read["settings"],
                "capabilities": _capability_summary(capabilities, read["mode"]),
                "how_to_edit": _how_to_edit(read["mode"], capabilities),
            }
            return ToolResult(success=True, data=json.dumps(result))
        except Exception as e:
            logger.error(f"Error reading music director from session metadata: {e}")
            return ToolResult(success=False, data="", error=str(e))


class UpdateMusicDirectorTool(BaseTool):
    """Proposes edits to the user's Music Director document."""

    modes = ["generation"]
    icon = "music"

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return music_director_active(form_state)

    @property
    def name(self) -> str:
        return "update_music_director"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Changes the Music Director document on your generation form for you."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "When the user asks to change their song's lyrics, sections, style description, "
            "references, instrumental toggle, or settings on an active Music Director -- "
            "propose changes with this tool."
            "{{#if get_music_director}} Always call get_music_director first to see the "
            "current document and which fields/kinds are valid before proposing "
            "operations.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Edit the user's Music Director document. `operations` is a JSON ARRAY of objects, "
            "each with an \"op\" key -- one `upsert_section` PER SECTION, with its own "
            "kind/lyrics. No `set_mode` -- mode is DERIVED from the result's shape, reported "
            "back, never accepted as input. Example: "
            f"{_CALL_EXAMPLE}. User approves before anything is applied.\n\n"
            "{{#if get_music_director}}get_music_director gives this preset's exact rules and "
            "valid ops.{{/if}} An invalid operation rejects the call (no approval) -- fix and "
            "retry.\n\n"
            "'description' is a three-part style prompt -- genre/era, instrumentation/texture, "
            "vocal character/mood. 'kind' on upsert_section is one of: " +
            ", ".join(_SECTION_KINDS) + " (default 'verse'); one plain section derives 'song', "
            "several (or one with style_hint/references) derives 'director'.\n\n"
            "Operations (\"op\" key; capabilities.available_operations lists what's valid "
            "now):\n"
            "- set_description {description}.\n"
            "- set_instrumental {instrumental}: true/false; without an instrumental mode, "
            "composes literal \"[instrumental]\" lyrics.\n"
            "- set_settings {settings: {duration?, bpm?, key?, time_signature?}}: partial "
            "merge; bpm/key/time_signature need capabilities.settings_fields.\n"
            "- upsert_section {section: {id?, kind?, lyrics?, style_hint?, duration_hint?, "
            "references?}}: upserts by id; style_hint needs per_section_prompts, references "
            "needs capabilities.references='per_section'.\n"
            "- remove_section {id} / reorder_sections {ids} (permutation of current ids).\n"
            "- upsert_reference {reference: {id?, media: {path, type?}}}: adds/updates an "
            "audio reference in the document's pool (path = known storage path).\n"
            "- remove_reference {id}.\n\n"
            "Validation runs against the END result of all operations together, not each in "
            "isolation."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": (
                        "Ordered ARRAY of operation objects applied to the Music Director "
                        "document, one per change -- one upsert_section per lyric section. "
                        f"This argument's value looks like: {_OPERATIONS_EXAMPLE}"
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
        md = form_state.get("music_director")
        if not md or not md.get("active"):
            return ToolResult(
                success=False,
                data="",
                error="The current preset/mode has no Music Director document active.",
            )

        doc = md.get("doc") or {}
        capabilities = apply_preset_mode_overlay(md.get("capabilities") or {}, form_state.get("mode"))
        read = _read_doc(doc, capabilities)

        try:
            filled_ops, summary = _apply_operations(operations, read, capabilities)
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

        preview = ToolApprovalPreview(action="Update Music Director", items=list(summary))
        return ToolResult(success=True, data=json.dumps(result), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        operations, problem = coerce_operations(kwargs.get("operations") or [])
        if problem:
            return ToolResult(success=False, data="", error=problem)

        form_state = context.session_metadata.get("form_state") or {}
        md = form_state.get("music_director") or {}
        doc = md.get("doc") or {}
        capabilities = apply_preset_mode_overlay(md.get("capabilities") or {}, form_state.get("mode"))
        read = _read_doc(doc, capabilities)

        try:
            filled_ops, summary = _apply_operations(operations, read, capabilities)
        except _OperationError as e:
            return ToolResult(success=False, data="", error=str(e))

        return ToolResult(
            success=True,
            data=json.dumps({
                "action": "apply_music_director_ops",
                "operations": filled_ops,
                "summary": summary,
            }),
        )
