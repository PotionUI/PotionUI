"""
Validation + canonicalization for the "Music Director" wire document.

The frontend submits `form_data.music_director` as a free-form JSON document
describing one of several composition modes (t2m/song/style/extend/repaint/
director). This module is the single gate between that document and the pipes
that consume it: it never talks to models or the filesystem beyond resolving
media paths, and it never knows about specific model families -- what's
allowed in a given mode comes entirely from the `capabilities` dict derived
from the preset's `vars.music_director`.

Mirrors `src/features/video_director/normalize.py` in shape and idiom (see
`docs/music-director.md` for the full contract this module implements), but
is deliberately its own module rather than a shared import: `music_director`
has no chain/timeline duality, no segment routing, and no whole-film
reference-field pool addressed from outside the document (a Music Director
document carries its own `references` pool inline) -- reusing video's
richer, video-shaped machinery would mean threading unused parameters
through every call. `apply_preset_mode_overlay` and `resolve_media_ref` ARE
generically reusable (capability-shape-agnostic, document-shape-agnostic
respectively), so those two live in `src.platform.util.path_resolution` and
are imported from there rather than duplicated -- a features package may
depend on `platform`, just not on a sibling features package.

Like `src/features/prompt/expander.py`, this is intentionally a bag of plain
functions rather than a class: there is no state to carry between calls, and
a validator gains nothing from being an object.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.platform.util.latents import generate_seed
from src.platform.util.path_resolution import apply_preset_mode_overlay, resolve_media_ref as _resolve_media_ref

logger = logging.getLogger(__name__)

_MODES = ("t2m", "song", "style", "extend", "repaint", "director")
_SECTION_MODES = frozenset({"song", "director"})
_SECTION_KINDS = frozenset({
    "intro", "verse", "pre_chorus", "chorus", "post_chorus", "bridge",
    "instrumental", "solo", "outro",
})
_SETTINGS_CAPABILITY_KEYS = ("bpm", "key", "time_signature")
_REFERENCES_MODE_VALUES = frozenset({"whole", "per_section"})


class MusicDirectorValidationError(ValueError):
    """Raised with every problem found in a document, not just the first."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))

    def __str__(self) -> str:
        return "; ".join(self.errors)


# The tag spellings MiniMax-Music3's model card documents as its executable
# structural directives; YuE accepts the same family. A preset's pipeline may
# reshape these on its own side, but this is the canonical form.
_SECTION_TAGS = {
    "intro": "[Intro]",
    "verse": "[Verse]",
    "pre_chorus": "[Pre-Chorus]",
    "chorus": "[Chorus]",
    "post_chorus": "[Post-Chorus]",
    "bridge": "[Bridge]",
    "instrumental": "[Instrumental]",
    "solo": "[Solo]",
    "outro": "[Outro]",
}


# A lyric line that IS a section tag: the whole line is one short bracket
# token ("[Verse]", "[pre-chorus 2]"). Lyrics that merely CONTAIN brackets
# ("[x2]" mid-line, "...[fades]...") don't match -- only a tag standing on
# its own line marks author-supplied structure.
_LEADING_TAG_RE = re.compile(r"^\s*\[[^\[\]\n]{1,32}\]\s*$")


def compile_sections_to_lyrics(sections: List[Dict[str, Any]]) -> str:
    """Serialize a `director`-mode section timeline into the tagged-lyrics
    document format a `compile: "single_shot"` mode (see
    `docs/music-director.md`) submits as ONE generation: the section's
    canonical bracket tag (`[Pre-Chorus]`) followed by its lyrics, sections
    separated by a single blank line.

    Author-supplied tags win: when a section's lyrics already open with a
    bracket tag on its own line (a user or LLM pasted whole tagged lyrics,
    the model's native format), the section is passed through verbatim and
    no tag is prepended from its kind -- prepending would double it.

    Pure function: no validation, no capability awareness, no I/O -- callers
    pass already-normalized sections (`normalize_music_director`'s output).
    """
    blocks = []
    for section in sections:
        lyrics = (section.get("lyrics") or "").strip()
        first_line = lyrics.split("\n", 1)[0] if lyrics else ""
        if _LEADING_TAG_RE.match(first_line):
            blocks.append(lyrics)
            continue
        kind = (section.get("kind") or "verse").strip().lower()
        tag = _SECTION_TAGS.get(kind, _SECTION_TAGS["verse"])
        blocks.append(f"{tag}\n{lyrics}")
    return "\n\n".join(blocks)


def normalize_music_director(
    document: Dict[str, Any],
    capabilities: Dict[str, Any],
    storage_dir: str,
    form_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Validate `document` against `capabilities` and return a NEW canonical dict.

    `capabilities` is expected to already be the EFFECTIVE, post-overlay set
    (see `apply_preset_mode_overlay`) -- this function does not apply an
    overlay itself, it only reads the merged result. `form_data` is accepted
    for parity with `normalize_video_director`'s signature but is currently
    unused: a Music Director document's `references` pool lives inline on the
    document itself (unlike Video Director's whole-film pool, which is
    addressed off separate form fields).

    Raises `MusicDirectorValidationError` carrying every error found. Never
    mutates `document`; unknown top-level keys are preserved verbatim, unknown
    keys inside known structures are dropped.
    """
    errors: List[str] = []
    capabilities = capabilities or {}
    modes = capabilities.get("modes") or {}
    limits = capabilities.get("limits") or {}
    settings_capability = capabilities.get("settings") or {}
    storage_path = Path(storage_dir).resolve()

    known_keys = {"schema_version", "mode", "description", "sections", "segments", "references", "extend_source", "repaint", "settings"}
    out: Dict[str, Any] = {key: value for key, value in document.items() if key not in known_keys}

    schema_version = document.get("schema_version")
    if schema_version is None:
        errors.append("missing schema_version")
    elif schema_version != 1:
        errors.append(
            f"unsupported schema_version {schema_version!r}: this server only understands schema_version 1 "
            "(the document was produced by a newer client than this server supports)"
        )

    mode = document.get("mode")
    if mode not in modes:
        allowed = sorted(modes.keys())
        errors.append(f"unsupported mode {mode!r}: allowed modes are {allowed}")
        # Nothing else can be meaningfully validated without a known mode.
        raise MusicDirectorValidationError(errors)

    mode_caps = modes.get(mode) or {}
    out["schema_version"] = 1
    out["mode"] = mode

    out["description"] = _normalize_description(document.get("description"), mode_caps, errors)

    references = _normalize_references(
        document.get("references"), mode, mode_caps, modes, storage_path, errors,
    )
    out["references"] = references

    out["sections"] = _normalize_sections(document.get("sections"), mode, mode_caps, references, errors)

    # Opaque round-trip carrier for the frontend's Song structure segment
    # editor (frontend/src/lib/utils/musicDirector.ts's `MusicDirectorValue.
    # segments`) -- the compiler and every other backend reader only ever
    # look at `sections[].lyrics`/`.kind` (derived from these segments by
    # `buildMusicDirectorSubmission` before the doc ever reaches here).
    # Preserved as-is (list) or dropped to None; never otherwise validated,
    # same discipline the per-section `lyrics_segments` carrier used before
    # it moved to this doc-level field.
    raw_segments = document.get("segments")
    out["segments"] = raw_segments if isinstance(raw_segments, list) else None

    out["extend_source"] = _normalize_extend_source(document.get("extend_source"), mode, storage_path, errors)
    out["repaint"] = _normalize_repaint(document.get("repaint"), mode, storage_path, errors)

    out["settings"] = _normalize_settings(document.get("settings") or {}, limits, settings_capability, errors)

    if errors:
        raise MusicDirectorValidationError(errors)

    return out


def _normalize_description(description: Any, mode_caps: Dict[str, Any], errors: List[str]) -> str:
    if description is None:
        value = ""
    elif not isinstance(description, str):
        errors.append(f"description must be a string, got {description!r}")
        return ""
    else:
        value = description

    # Empty is valid by default (a `t2m` document with no description is a
    # useless-but-valid request, the same way an empty `t2v` prompt is valid
    # in Video Director) -- UNLESS the mode's capability block opts out via
    # `description_required: true`. A family whose generator has no other
    # conditioning signal to fall back on (MiniMax-Music3's caption is read
    # by every declared mode, lyrics included -- see modes/song/pipeline.yml)
    # sets this so the gap surfaces here, before generation, instead of as
    # the pipe's own `validate_config` ValueError.
    if mode_caps.get("description_required") and not value.strip():
        errors.append("description: this mode requires a non-empty description -- describe the music before generating")

    return value


def _normalize_settings(
    settings: Dict[str, Any],
    limits: Dict[str, Any],
    settings_capability: Dict[str, Any],
    errors: List[str],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    duration = settings.get("duration")
    if duration is None:
        duration = limits.get("default_duration", 120)
    max_duration = limits.get("max_duration")
    duration_valid = isinstance(duration, (int, float)) and duration > 0
    if not duration_valid:
        errors.append(f"settings.duration must be > 0, got {duration!r}")
    elif max_duration is not None and duration > max_duration:
        errors.append(f"settings.duration {duration} exceeds the allowed maximum {max_duration}")
    out["duration"] = duration

    seed = settings.get("seed")
    if seed is None or seed == -1:
        seed = generate_seed(-1)
    out["seed"] = seed

    # bpm/key/time_signature are real, structured fields only when the
    # preset's `settings` capability block says so for that key -- otherwise
    # the caller is told to fold it into the free-form `description` instead
    # (the model reads it out of the prompt like everything else it doesn't
    # expose as a knob).
    for key in _SETTINGS_CAPABILITY_KEYS:
        out[key] = None
        value = settings.get(key)
        if value is None:
            continue
        if not settings_capability.get(key):
            errors.append(
                f"settings.{key} is not supported by this preset -- describe it in the description text instead"
            )
            continue
        if key == "bpm":
            if not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"settings.bpm must be a positive number, got {value!r}")
                continue
        elif not isinstance(value, str) or not value.strip():
            errors.append(f"settings.{key} must be a non-empty string, got {value!r}")
            continue
        out[key] = value

    return out


def _references_gate(mode: str, mode_caps: Dict[str, Any], modes: Dict[str, Any]) -> tuple:
    """`(allowed, required)` for the top-level `references` pool, given the
    submitting `mode`. `style` always requires at least one reference;
    `song` may carry references only when the preset ALSO declares a `style`
    mode (the two combine on families that condition a full song on a
    reference track); `director` follows its own `references` capability
    (`"whole"`/`"per_section"`, gating selection shape further down in
    `_normalize_sections`, not presence here); every other mode accepts none."""
    if mode == "style":
        return True, True
    if mode == "song":
        return "style" in modes, False
    if mode == "director":
        return mode_caps.get("references") in _REFERENCES_MODE_VALUES, False
    return False, False


def _normalize_references(
    raw_references: Any,
    mode: str,
    mode_caps: Dict[str, Any],
    modes: Dict[str, Any],
    storage_path: Path,
    errors: List[str],
) -> List[Dict[str, Any]]:
    allowed, required = _references_gate(mode, mode_caps, modes)

    if raw_references is None:
        if required:
            errors.append(f"references: mode {mode!r} requires at least one reference")
        return []

    if not allowed:
        errors.append(f"references: mode {mode!r} does not accept references")
        return []

    if not isinstance(raw_references, list):
        errors.append("references must be a list")
        return []

    if not raw_references and required:
        errors.append(f"references: mode {mode!r} requires at least one reference")
        return []

    # `max_reference_seconds` (style mode only) is metadata, not an enforced
    # bound: real audio duration isn't derivable here without decoding the
    # file, so this only checks a client-declared `duration_seconds` hint
    # when one is present -- mirrors video_director's `audio` role, whose
    # length is likewise taken on the caller's word, not probed.
    max_reference_seconds = mode_caps.get("max_reference_seconds") if mode == "style" else None

    out: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw_references):
        context = f"references[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{context}: must be an object")
            continue

        media_ref = _resolve_media_ref(entry.get("media"), storage_path, context, errors)
        if media_ref is None:
            continue

        if max_reference_seconds is not None:
            declared = media_ref.get("duration_seconds")
            if isinstance(declared, (int, float)) and declared > max_reference_seconds:
                errors.append(
                    f"{context}: reference is {declared}s, exceeding this mode's max_reference_seconds "
                    f"{max_reference_seconds} (trim it before uploading -- actual duration enforcement "
                    "beyond a client-declared hint is the generator's business)"
                )

        out.append({"id": entry.get("id"), "media": media_ref})

    return out


def _normalize_sections(
    raw_sections: Any,
    mode: str,
    mode_caps: Dict[str, Any],
    references: List[Dict[str, Any]],
    errors: List[str],
) -> List[Dict[str, Any]]:
    if mode not in _SECTION_MODES:
        if raw_sections is not None:
            errors.append(f"sections: mode {mode!r} does not accept sections")
        return []

    # `song` accepts a bare single section object as shorthand for "one
    # section, the whole song" -- the common case of plain lyrics with no
    # structure -- wrapped here into the one-item list every other mode
    # already works with.
    if mode == "song" and isinstance(raw_sections, dict):
        raw_sections = [raw_sections]

    if raw_sections is None:
        raw_sections = []

    if not isinstance(raw_sections, list):
        errors.append("sections must be a list (or, in song mode, a single section object)")
        return []

    if mode == "director":
        max_sections = mode_caps.get("max_sections", 12)
        if not raw_sections:
            errors.append("sections: director mode requires at least one section")
        elif len(raw_sections) > max_sections:
            errors.append(f"sections: director mode allows at most {max_sections} sections, got {len(raw_sections)}")
    elif mode == "song" and not raw_sections:
        errors.append("sections: song mode requires lyrics via at least one section")

    per_section_prompts = bool(mode_caps.get("per_section_prompts"))
    section_duration_hints = bool(mode_caps.get("section_duration_hints"))
    per_section_references = mode == "director" and mode_caps.get("references") == "per_section"
    reference_ids = {ref["id"] for ref in references if ref.get("id") is not None}

    out: List[Dict[str, Any]] = []
    for i, section in enumerate(raw_sections):
        context = f"sections[{i}]"
        if not isinstance(section, dict):
            errors.append(f"{context}: must be an object")
            continue

        kind = section.get("kind")
        if kind is None:
            kind = "verse"
        elif kind not in _SECTION_KINDS:
            errors.append(f"{context}.kind must be one of {sorted(_SECTION_KINDS)}, got {kind!r}")
            kind = "verse"

        lyrics = section.get("lyrics")
        if lyrics is None:
            lyrics = ""
        elif not isinstance(lyrics, str):
            errors.append(f"{context}.lyrics must be a string, got {lyrics!r}")
            lyrics = ""

        style_hint = section.get("style_hint")
        if style_hint is not None:
            if not per_section_prompts:
                errors.append(f"{context}.style_hint: per-section style hints are not supported by this preset")
                style_hint = None
            elif not isinstance(style_hint, str):
                errors.append(f"{context}.style_hint must be a string, got {style_hint!r}")
                style_hint = None

        duration_hint = section.get("duration_hint")
        if duration_hint is not None:
            if not section_duration_hints:
                errors.append(f"{context}.duration_hint: per-section duration hints are not supported by this preset")
                duration_hint = None
            elif not isinstance(duration_hint, (int, float)) or duration_hint <= 0:
                errors.append(f"{context}.duration_hint must be > 0, got {duration_hint!r}")
                duration_hint = None

        section_references = section.get("references")
        if section_references is not None:
            if not per_section_references:
                errors.append(
                    f"{context}.references: this preset's references capability does not allow "
                    "per-section selection"
                )
                section_references = None
            elif not isinstance(section_references, list) or not section_references:
                errors.append(f"{context}.references: must be a non-empty list of reference ids")
                section_references = None
            else:
                resolved_refs = []
                for ref_id in section_references:
                    if ref_id not in reference_ids:
                        errors.append(f"{context}.references: {ref_id!r} is not part of this document's references pool")
                        continue
                    resolved_refs.append(ref_id)
                section_references = resolved_refs

        out.append({
            "id": section.get("id") or f"section-{i}",
            "kind": kind,
            "lyrics": lyrics,
            "style_hint": style_hint,
            "duration_hint": duration_hint,
            "references": section_references,
        })

    return out


def _normalize_extend_source(
    raw: Any, mode: str, storage_path: Path, errors: List[str],
) -> Optional[Dict[str, Any]]:
    if mode != "extend":
        if raw is not None:
            errors.append(f"extend_source: mode {mode!r} does not accept extend_source")
        return None

    if not isinstance(raw, dict):
        errors.append("extend_source: extend mode requires an extend_source object")
        return None

    media_ref = _resolve_media_ref(raw.get("media"), storage_path, "extend_source", errors)
    if media_ref is None:
        return None
    return {"media": media_ref}


def _normalize_repaint(
    raw: Any, mode: str, storage_path: Path, errors: List[str],
) -> Optional[Dict[str, Any]]:
    if mode != "repaint":
        if raw is not None:
            errors.append(f"repaint: mode {mode!r} does not accept a repaint block")
        return None

    if not isinstance(raw, dict):
        errors.append("repaint: repaint mode requires a repaint object")
        return None

    source = raw.get("source")
    media_ref = None
    if not isinstance(source, dict):
        errors.append("repaint.source: repaint mode requires a source media object")
    else:
        media_ref = _resolve_media_ref(source.get("media"), storage_path, "repaint.source", errors)
        if media_ref is None:
            errors.append("repaint.source: media is required")

    # Range shape only -- 0 <= start < end. Checking `end` against the
    # source's REAL duration would need decoding the file, which is the
    # generator's business, not this normalizer's (mirrors
    # video_director's `audio[].length`, taken on the caller's word).
    start = raw.get("start")
    end = raw.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        errors.append("repaint: start/end are required numbers")
    elif not (0 <= start < end):
        errors.append(f"repaint: start must be >= 0 and < end, got start={start} end={end}")

    if media_ref is None:
        return None
    return {"source": {"media": media_ref}, "start": start, "end": end}
