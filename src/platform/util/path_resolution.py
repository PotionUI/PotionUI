"""Resolving a caller-supplied path under a trusted root, rejecting escapes.

`resolve_within` is the primitive; `apply_preset_mode_overlay` and
`resolve_media_ref` are Director-document helpers (shared between
`video_director` and `music_director`) built on top of it.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional


def resolve_within(root: Path, raw: str, *, must_exist: bool = False) -> Optional[Path]:
    """Resolve `raw` under `root`, returning None if it escapes `root`.

    An absolute `raw` is resolved as-is (still checked for containment,
    it is not silently re-rooted); a relative `raw` is joined onto `root`
    first. When `must_exist` is set, a resolved-but-absent path also
    returns None.
    """
    candidate = Path(raw)
    attempt = candidate.resolve() if candidate.is_absolute() else (root / raw).resolve()
    try:
        attempt.relative_to(root)
    except ValueError:
        return None
    if must_exist and not attempt.exists():
        return None
    return attempt


def apply_preset_mode_overlay(capabilities: Dict[str, Any], preset_mode: Optional[str]) -> Dict[str, Any]:
    """Merge a preset's base Director capability block with the override
    declared for `preset_mode` under `capabilities.preset_mode_overrides`,
    if any -- the mechanism that lets one preset expose a different Director
    capability set per preset mode (e.g. MiniMax-H3's `video` vs `refs`).
    Generic machinery, agnostic to any one capability or Director document
    shape (shared between `video_director` and `music_director`).

    Every top-level key overlays shallowly (the override's value replaces the
    base's), except `modes`: each composition mode named in the override's
    `modes` dict is itself shallow-merged onto the base's entry for that
    composition mode -- so an override that only touches one nested key
    doesn't have to repeat the rest of that mode's entry, and a composition
    mode the override doesn't mention is untouched. A missing or unrecognized
    `preset_mode`, or a capabilities dict declaring no `preset_mode_overrides`,
    returns the base capabilities unchanged (a copy, with
    `preset_mode_overrides` itself stripped -- callers only ever want the
    EFFECTIVE set, never the raw override table).
    """
    capabilities = capabilities or {}
    overrides = capabilities.get("preset_mode_overrides")
    merged = {k: v for k, v in capabilities.items() if k != "preset_mode_overrides"}

    override = overrides.get(preset_mode) if isinstance(overrides, dict) and preset_mode is not None else None
    if not isinstance(override, dict):
        return merged

    base_modes = dict(capabilities.get("modes") or {})
    for key, value in override.items():
        if key != "modes":
            merged[key] = value

    override_modes = override.get("modes")
    if isinstance(override_modes, dict):
        merged_modes = dict(base_modes)
        for comp_mode, comp_override in override_modes.items():
            base_entry = base_modes.get(comp_mode)
            if isinstance(comp_override, dict) and isinstance(base_entry, dict):
                merged_modes[comp_mode] = {**base_entry, **comp_override}
            else:
                merged_modes[comp_mode] = comp_override
        merged["modes"] = merged_modes
    else:
        merged["modes"] = base_modes

    return merged


def resolve_media_ref(
    media_ref: Optional[Dict[str, Any]],
    storage_path: Path,
    context: str,
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Resolve a `{path, relative_path, ...}` reference to an absolute, existing
    path inside `storage_path`, rejecting traversal outside of it. Returns a
    NEW dict with `path` set to the resolved absolute path, or None on error
    (the error is appended to `errors`). Shared between `video_director` and
    `music_director`.
    """
    if not isinstance(media_ref, dict) or not (media_ref.get("path") or media_ref.get("relative_path")):
        errors.append(f"{context}: media requires a 'path' or 'relative_path'")
        return None

    # An upload's `path` can be rooted anywhere (it is the raw save location,
    # e.g. "storage/uploads/x.png" under a relative storage config), while
    # `relative_path` is storage-relative — so a single candidate joined onto
    # `storage_path` double-prefixes one shape or the other. Try each key,
    # first as an absolute path, then joined onto the storage root.
    raws = [r for r in (media_ref.get("path"), media_ref.get("relative_path")) if r]
    resolved = None
    escaped = False
    for raw in raws:
        attempt = resolve_within(storage_path, raw)
        if attempt is None:
            escaped = True
            continue
        if attempt.exists():
            resolved = attempt
            break

    if resolved is None:
        raw = raws[0]
        if escaped:
            errors.append(f"{context}: media path {raw!r} escapes the storage directory")
        else:
            errors.append(f"{context}: media path {raw!r} does not exist")
        return None

    result = dict(media_ref)
    result["path"] = str(resolved)
    return result
