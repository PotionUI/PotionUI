"""Validation for media values an LLM proposes for a form field.

`update_form_settings` and `run_generation` both let a model put an arbitrary
value on a known field name. For a media field that means the model can invent
a path - and it does: handed a `generation_id` and a thumbnail by
`search_gallery`, a model will construct `generations/<id>/0.png`, which is
wrong twice over (the date segment is missing, and videos have no index 0 -
the counter pre-increments, so the first video is `1.mp4`).

Nothing downstream catches it usefully: `bind_form` checks containment, not
existence, so an invented path sails through and the generation fails deep in
a pipe, long after the model could have corrected itself. So the check lives
at the tool boundary, and the rejection NAMES THE VALID FORM - in this
codebase error strings teach the model at least as much as the tool
description does.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from src.features.forms.binding import MEDIA_FIELD_TYPES
from src.platform.util.path_resolution import resolve_within

logger = logging.getLogger(__name__)

# Repeated verbatim in the tool descriptions and in every rejection, so the
# model sees the same shape whichever way it learns it.
MEDIA_VALUE_FORM = (
    "a storage-root-relative path exactly as returned in a search_gallery "
    "result's `path` (e.g. 'generations/2026-08-12/01ABC.../1.png'). Never "
    "construct one from a generation id: the date segment is not derivable "
    "and videos have no index 0"
)


def _walk_media_field_names(node: Any, result: Set[str], fallback_name: Optional[str] = None) -> None:
    if isinstance(node, dict):
        name = node.get("name") or fallback_name
        if isinstance(name, str) and node.get("type") in MEDIA_FIELD_TYPES:
            result.add(name)
        for child in node.get("children") or []:
            _walk_media_field_names(child, result)
    elif isinstance(node, list):
        for child in node:
            _walk_media_field_names(child, result)


def media_field_names(schema_properties: Optional[Dict[str, Any]]) -> Set[str]:
    """Names of the media-carrying fields in a serialized form schema.

    `get_form_schema`'s `form_schema.properties` is not a flat `{name: spec}`
    map - every preset lays fields out under a `tabs` root whose `children`
    nest `tab`/`section`/`row` wrappers arbitrarily deep before reaching an
    actual field (mirrors model_values.model_field_names). This walks that
    tree; a top-level entry's own dict key still stands in for a spec that
    carries no `name` of its own."""
    result: Set[str] = set()
    if isinstance(schema_properties, dict):
        for name, spec in schema_properties.items():
            _walk_media_field_names(spec, result, fallback_name=name)
    return result


def _raw_paths(value: Any) -> Optional[List[str]]:
    """The path strings inside one media value, or None if it isn't one."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        raws = [
            raw for raw in (value.get("path"), value.get("relative_path"))
            if isinstance(raw, str) and raw
        ]
        return raws if raws else None
    return None


def _resolve(raw: str, storage_root: Path) -> Optional[Path]:
    """First existing resolution of `raw`, over the two relative conventions.

    Mirrors `media_loader._resolve_media_path`: a value may be CWD-relative
    and already carry the storage prefix ('storage/uploads/...') or be
    storage-root-relative ('generations/...'). Take it as given first, then
    joined onto the root - never blindly re-root, which would double-prefix
    the first convention and still land inside the root.
    """
    candidate = Path(raw)
    as_given = candidate if candidate.is_absolute() else (Path.cwd() / candidate)
    if as_given.exists():
        return as_given.resolve()
    under_root = (storage_root / raw)
    if under_root.exists():
        return under_root.resolve()
    return None


def validate_media_value(
    field_name: str,
    value: Any,
    storage_dir: Optional[str],
) -> List[str]:
    """Errors for one LLM-proposed media field value; empty means acceptable.

    A `None`/empty value is a clear, not a path, and is always allowed.
    A `None` `storage_dir` means there is no user context to check against -
    skip rather than guess, exactly as `bind_form` does.
    """
    if value is None or value == "" or value == []:
        return []
    if storage_dir is None:
        logger.warning(
            "no storage_dir available to validate LLM-proposed media for '%s'", field_name
        )
        return []

    items: Sequence[Any] = value if isinstance(value, list) else [value]
    storage_root = Path(storage_dir).resolve()
    errors: List[str] = []

    for item in items:
        raws = _raw_paths(item)
        if raws is None:
            errors.append(
                f"'{field_name}' takes {MEDIA_VALUE_FORM}. "
                f"Got {type(item).__name__}, which is not a media value."
            )
            continue

        for raw in raws:
            resolved = _resolve(raw, storage_root)
            if resolved is None:
                errors.append(
                    f"'{field_name}': no file exists at {raw!r}. It must be "
                    f"{MEDIA_VALUE_FORM}."
                )
                continue
            if resolve_within(storage_root, str(resolved)) is None:
                errors.append(
                    f"'{field_name}': {raw!r} resolves outside your storage "
                    f"directory. It must be {MEDIA_VALUE_FORM}."
                )

    return errors


def validate_media_changes(
    proposed: Dict[str, Any],
    media_fields: Set[str],
    storage_dir: Optional[str],
) -> List[str]:
    """Errors across a `{field_name: value}` batch of proposed changes."""
    errors: List[str] = []
    for field_name, value in proposed.items():
        if field_name in media_fields:
            errors.extend(validate_media_value(field_name, value, storage_dir))
    return errors


def preset_form_media_errors(
    preset_manager: Any,
    storage_dir: Optional[str],
    preset_id: str,
    mode: str,
    proposed: Dict[str, Any],
) -> List[str]:
    """Errors for `proposed` field values against `preset_id`/`mode`'s media
    fields, loading the form schema itself. Shared by any tool that lets a
    model set form field values outside a live form_state (`run_generation`
    instead reads the schema already open on the session's current preset)."""
    if not proposed or preset_manager is None:
        return []
    try:
        schema_data = preset_manager.get_form_schema(preset_id, mode=mode)
        media_fields = media_field_names(schema_data.get("form_schema", {}).get("properties", {}))
    except Exception as e:
        logger.debug(f"Could not load form schema for media validation: {e}")
        return []
    return validate_media_changes(proposed, media_fields, storage_dir)
