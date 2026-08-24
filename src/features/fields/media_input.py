"""Shared `input()` value-shape dispatch for the image/video/audio/media
field types (`Image`, `Video`, `Audio`, `Media` in this package). All four
accept the same value shapes and differ only in their own legacy-base64
`_validate_*`/`_process_*` pair, supplied by the caller, and in which of the
constraint keys below they document on their own `configuration()`.

This is the LIVE validator for these field types: `src/features/forms/binding.py`
registers each type in `_INPUT_VALIDATORS`, so `process_media_input` runs on
every real generation submission, ahead of `_check_media_containment`'s path
containment check - containment (a security boundary) and shape/constraint
validation are deliberately two different concerns, not layered into one
function.
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_MEDIA_LABEL_LENGTH = 64

# `configuration:` keys `_check_media_constraints` reads, shared verbatim
# across Image/Video/Audio/Media so a preset author writes the same key
# regardless of which of the four field types it lives on - only which keys
# a given field type *documents* (its own `configuration()`/FieldConfigSpec
# list, checked by the preset linter) differs.
CONFIG_ACCEPTED_TYPES = "accepted_types"
CONFIG_MAX_RESOLUTION = "max_resolution"
CONFIG_MAX_VIDEO_DURATION = "max_video_duration_seconds"
CONFIG_MAX_TOTAL_VIDEO_DURATION = "max_total_video_duration_seconds"
CONFIG_MAX_AUDIO_DURATION = "max_audio_duration_seconds"
CONFIG_MAX_TOTAL_AUDIO_DURATION = "max_total_audio_duration_seconds"

_CONSTRAINT_SCHEMA_KEYS = (
    CONFIG_ACCEPTED_TYPES,
    CONFIG_MAX_RESOLUTION,
    CONFIG_MAX_VIDEO_DURATION,
    CONFIG_MAX_TOTAL_VIDEO_DURATION,
    CONFIG_MAX_AUDIO_DURATION,
    CONFIG_MAX_TOTAL_AUDIO_DURATION,
)


def echo_configured_constraints(schema: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Echo whichever of the shared constraint keys (`CONFIG_*` above) a
    field's `configuration:` declares into its rendered top-level schema,
    unmodified - shared by Image/Video/Audio/Media's `output()` so the
    frontend can enforce the same `accepted_types`/`max_resolution`/duration
    limits client-side before upload, not just server-side in
    `_check_media_constraints`. Only present when actually configured; no
    defaults are invented here (mirrors how `max_items` is already only
    emitted when set - see each field's `output()`)."""
    for key in _CONSTRAINT_SCHEMA_KEYS:
        if key in config:
            schema[key] = config[key]


def clean_media_label(label: Any) -> Optional[str]:
    """Strip/cap a multi-item entry's optional `label` - a HANDLE other
    systems reference, not free-form prose. Non-string/empty-after-strip
    labels are dropped rather than coerced."""
    if not isinstance(label, str):
        return None
    cleaned = label.strip()[:MAX_MEDIA_LABEL_LENGTH]
    return cleaned or None


def process_media_input(
    field_name: str,
    value: Any,
    validation_rules: Optional[Dict[str, Any]],
    *,
    validate_legacy: Callable[[Dict[str, Any], Dict[str, Any]], List[str]],
    process_legacy: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Any:
    """Shared body for Image/Video/Audio/Media.input().

    Value shapes accepted, in priority order:
    - falsy -> `None` (a falsy LIST stays `[]` when `multi` is set, so an
      emptied-out multi-item field doesn't collapse to `None`);
    - a string -> passthrough unchanged. Containment/resolution against the
      user's storage root is `bind_form`'s job (`_check_media_containment`),
      and EXISTENCE is the media pipes' job at execution time - a bind-time
      existence check would reject legitimate render-without-files callers
      (the golden harness's `/GOLDEN/media/...`, setup's `/SETUP-CHECK/...`
      placeholders) and race with deletion anyway;
    - a dict carrying `path`, `relative_path`, or `url` -> passthrough media
      reference (the shape MediaLoaderField itself sends); `name`/`type`/
      `metadata` ride along untouched, `label` is kept only when `multi` is
      set (stripped/capped - see `clean_media_label`). Containment
      (`_check_media_containment`, this validator's downstream neighbour)
      traversal-checks whichever of `path`/`relative_path` are present; a
      url-only reference has no local path to check and no pipe will load
      it, so it fails at the media pipe, not here - lenient boundary;
    - a dict with none of those but `data` -> treated as a legacy base64
      upload (`{data, name, type, size}`), validated via `validate_legacy`/
      decoded via `process_legacy`; a dict with no recognized key at all
      falls into the same legacy validation and gets its "missing data"
      error;
    - a list -> when `configuration.multi` is true, each entry goes through
      the rules above plus `max_items`; when `multi` is NOT set, a bare
      list is still accepted for backward compatibility with older base64
      multi-upload callers (each entry the same way, no label/max_items).

    Once every item has a recognized shape, `_check_media_constraints` walks
    the RAW items (before base64 decoding, so a legacy item's own top-level
    width/height/duration keys and a passthrough item's `metadata` sub-dict
    are both still readable) against any of the `accepted_types`/
    `max_resolution`/`max_*_duration_seconds` keys the field's
    `configuration:` declares - see that function's docstring for the
    fail-open policy on missing metadata.
    """
    config = validation_rules or {}
    multi = bool(config.get("multi"))

    if multi:
        if not value:
            return [] if isinstance(value, list) else None
        if not isinstance(value, list):
            raise ValueError(f"'{field_name}' must be a list of items because multiple items are enabled")
        max_items = config.get("max_items")
        if max_items is not None and len(value) > max_items:
            raise ValueError(f"Too many items for '{field_name}': maximum is {max_items}")
        results = [
            _process_item(field_name, idx, item, config, validate_legacy, process_legacy, allow_label=True)
            for idx, item in enumerate(value)
        ]
        _check_media_constraints(field_name, value, config)
        return results

    if not value:
        return None

    if isinstance(value, list):
        results = [
            _process_item(field_name, idx, item, config, validate_legacy, process_legacy, allow_label=False)
            for idx, item in enumerate(value)
        ]
        _check_media_constraints(field_name, value, config)
        return results

    result = _process_item(field_name, 0, value, config, validate_legacy, process_legacy, allow_label=False)
    _check_media_constraints(field_name, [value], config)
    return result


def _process_item(
    field_name: str,
    idx: int,
    item: Any,
    config: Dict[str, Any],
    validate_legacy: Callable[[Dict[str, Any], Dict[str, Any]], List[str]],
    process_legacy: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    allow_label: bool,
) -> Any:
    if isinstance(item, str):
        return item

    if not isinstance(item, dict):
        raise ValueError(f"Invalid entry for '{field_name}': item {idx + 1} must be an object or path string")

    if item.get("path") or item.get("relative_path") or item.get("url"):
        result = dict(item)
        label = clean_media_label(item.get("label")) if allow_label else None
        if label:
            result["label"] = label
        else:
            result.pop("label", None)
        return result

    errors = validate_legacy(item, config)
    if errors:
        raise ValueError(f"Item {idx + 1} for '{field_name}' failed validation: {'; '.join(errors)}")
    processed = process_legacy(item)
    if allow_label:
        label = clean_media_label(item.get("label"))
        if label:
            processed["label"] = label
    return processed


# ---------------------------------------------------------------------------
# Constraint validation (accepted types / resolution / duration limits)
# ---------------------------------------------------------------------------


def _numeric(value: Any) -> Optional[float]:
    """`value` as a plain number, or `None` if it isn't one - `bool` is
    excluded even though it's an `int` subclass (a stray `True`/`False`
    metadata value is never a real width/height/duration)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _media_category(type_value: Any) -> Optional[str]:
    """Normalize an item's `type` to the bare category
    (`image`/`video`/`audio`) the constraint keys below are written in terms
    of. The modern passthrough shape carries a bare category already
    (`"image"`); the legacy base64 shape carries a MIME type
    (`"image/png"`) - both take the substring before the first `/`, which is
    a no-op for the former."""
    if not isinstance(type_value, str) or not type_value:
        return None
    category = type_value.split("/", 1)[0].strip().lower()
    return category or None


def _media_item_metrics(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[float], Optional[float], Optional[float]]:
    """Best-effort `(category, width, height, duration_seconds)` for one
    media item.

    Reads the modern passthrough shape's `metadata` sub-dict when present
    (`{width, height, duration_seconds, fps, size}` - see
    `mediaLoaderUpload.ts`'s `UploadedMediaItem`), else falls back to the
    item's own top-level keys (the legacy base64 shape, which never nests
    metadata and spells duration `duration` - see `Video._validate_video`/
    `Audio._validate_audio`). A missing or non-numeric field reads as
    `None`, never `0`: `media_probe` is explicitly best-effort, and coercing
    "unknown" to `0` would make every downstream `max_*` check pass on the
    way down and fail as soon as anything real is known, which is backwards
    from the fail-open policy `_check_media_constraints` documents.
    """
    category = _media_category(item.get("type"))
    metadata = item.get("metadata")
    source: Dict[str, Any] = metadata if isinstance(metadata, dict) else item
    width = _numeric(source.get("width"))
    height = _numeric(source.get("height"))
    duration = _numeric(source.get("duration_seconds"))
    if duration is None and source is item:
        duration = _numeric(item.get("duration"))
    return category, width, height, duration


def _check_media_constraints(field_name: str, items: List[Any], config: Dict[str, Any]) -> None:
    """Enforce `accepted_types`/`max_resolution`/`max_video_duration_seconds`/
    `max_total_video_duration_seconds`/`max_audio_duration_seconds`/
    `max_total_audio_duration_seconds` against `items` - the RAW values
    handed to `process_media_input`, one dict (or bare path string) per
    media item regardless of whether the field is `multi`.

    Every check is opt-in (skipped entirely when its config key is absent)
    and FAILS OPEN when the specific value it needs is unknown: a bare path
    string carries no metadata at all, and even a passthrough dict's
    `metadata` sub-dict can have any of its fields as `None` (best-effort
    probing - see `MediaManager._probe_upload_metadata`). Rejecting a
    submission the platform simply couldn't measure would be a false
    positive, not a safety check; an unmeasurable value is treated the same
    as an unconfigured limit; either way `total_*` duration is only checked
    when EVERY item of that category reported a duration - one unknown
    poisons the sum, since a partial sum could pass a total that, filled in,
    would not.

    Raises one `ValueError` joining every violation found (mirroring
    `validate_legacy`'s multi-message-per-item join), not just the first -
    a submission with several problems should surface all of them at once.
    """
    accepted_types = config.get(CONFIG_ACCEPTED_TYPES)
    max_resolution = _numeric(config.get(CONFIG_MAX_RESOLUTION))
    max_video_duration = _numeric(config.get(CONFIG_MAX_VIDEO_DURATION))
    max_total_video_duration = _numeric(config.get(CONFIG_MAX_TOTAL_VIDEO_DURATION))
    max_audio_duration = _numeric(config.get(CONFIG_MAX_AUDIO_DURATION))
    max_total_audio_duration = _numeric(config.get(CONFIG_MAX_TOTAL_AUDIO_DURATION))

    if not (
        accepted_types or max_resolution or max_video_duration or max_total_video_duration
        or max_audio_duration or max_total_audio_duration
    ):
        return  # no constraints declared on this field - skip the walk entirely

    if isinstance(accepted_types, (list, tuple, set)):
        accepted_types = {str(t).lower() for t in accepted_types}
    else:
        accepted_types = None

    violations: List[str] = []
    video_durations: List[float] = []
    video_duration_unknown = False
    audio_durations: List[float] = []
    audio_duration_unknown = False

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue  # a bare path string carries no type/metadata to check - fail open

        category, width, height, duration = _media_item_metrics(item)
        label = f"item {idx + 1}"

        if accepted_types is not None and category is not None and category not in accepted_types:
            violations.append(
                f"{label}: type '{category}' is not accepted for '{field_name}' "
                f"(accepted: {', '.join(sorted(accepted_types))})"
            )

        if max_resolution is not None and category in ("image", "video"):
            if width is not None and width > max_resolution:
                violations.append(f"{label}: width {width:g}px exceeds the maximum resolution of {max_resolution:g}px")
            if height is not None and height > max_resolution:
                violations.append(f"{label}: height {height:g}px exceeds the maximum resolution of {max_resolution:g}px")

        if category == "video":
            if duration is None:
                video_duration_unknown = True
            else:
                video_durations.append(duration)
                if max_video_duration is not None and duration > max_video_duration:
                    violations.append(
                        f"{label}: video duration {duration:g}s exceeds the per-video maximum of {max_video_duration:g}s"
                    )
        elif category == "audio":
            if duration is None:
                audio_duration_unknown = True
            else:
                audio_durations.append(duration)
                if max_audio_duration is not None and duration > max_audio_duration:
                    violations.append(
                        f"{label}: audio duration {duration:g}s exceeds the per-audio maximum of {max_audio_duration:g}s"
                    )

    if max_total_video_duration is not None and video_durations and not video_duration_unknown:
        total = sum(video_durations)
        if total > max_total_video_duration:
            violations.append(
                f"video items total {total:g}s of duration for '{field_name}', "
                f"exceeding the maximum of {max_total_video_duration:g}s"
            )

    if max_total_audio_duration is not None and audio_durations and not audio_duration_unknown:
        total = sum(audio_durations)
        if total > max_total_audio_duration:
            violations.append(
                f"audio items total {total:g}s of duration for '{field_name}', "
                f"exceeding the maximum of {max_total_audio_duration:g}s"
            )

    if violations:
        raise ValueError("; ".join(violations))
