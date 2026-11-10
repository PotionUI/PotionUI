from typing import Any, Dict, List

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import (
    InvalidModesError,
    MirrorIdUnavailableError,
    MirrorSourceInvalidError,
    MirrorSourceNotInstalledError,
    NotAMirrorError,
    PresetNotFoundException,
    PresetRowNotFoundError,
)
from src.features.presets.operations.display import build_admin_detail, clean_display, files_template
from src.features.presets.records import Preset, PresetTombstone
from src.platform.util.ids import generate_ulid

_ID_ATTEMPTS = 3


def clean_modes(source_modes: List[str], offered: Any, first: Any) -> Dict[str, Any]:
    errors: List[str] = []
    if offered is not None:
        if not isinstance(offered, list) or not all(isinstance(mode, str) for mode in offered):
            errors.append("offered modes must be a list of mode names or null")
            offered = None
        elif not offered:
            errors.append("offer at least one mode, or send null to offer every mode")
        else:
            unknown = [mode for mode in offered if mode not in source_modes]
            if unknown:
                errors.append(f"unknown mode(s): {', '.join(unknown)}")
            offered = list(dict.fromkeys(offered))
    if first is not None and not isinstance(first, str):
        errors.append("the first mode must be a mode name or null")
    elif first is not None and first not in (offered if offered else source_modes):
        errors.append(f"the first mode '{first}' is not offered")
    if errors:
        raise InvalidModesError(errors)
    return {"offered": offered, "first": first}


def _free_mirror_id(collaborators: PresetCollaborators) -> str:
    for _ in range(_ID_ATTEMPTS):
        candidate = generate_ulid()
        if collaborators.preset_loader.get(candidate) is None and not collaborators.db_repo.preset_id_taken(candidate):
            return candidate
    raise MirrorIdUnavailableError()


def create_mirror(collaborators: PresetCollaborators, source_id: str, payload: Any) -> Dict[str, Any]:
    template = collaborators.preset_loader.get(source_id)
    if template is None:
        raise PresetRowNotFoundError(source_id)
    if template.source_preset_id is not None:
        raise MirrorSourceInvalidError(source_id)
    source_row = collaborators.db_repo.get_installed_preset_by_preset_id(source_id)
    if source_row is None:
        raise MirrorSourceNotInstalledError(source_id)
    if source_row.is_mirror:
        raise MirrorSourceInvalidError(source_id)
    body = payload if isinstance(payload, dict) else {}
    display = clean_display(
        collaborators,
        {key: body.get(key) for key in ("name", "description", "cover")},
        None,
        template.path,
        True,
    )
    file_template = collaborators.preset_loader.file_template(source_id) or template
    modes = clean_modes(list(file_template.modes), body.get("offered_modes"), body.get("first_mode"))
    mirror = collaborators.db_repo.create_mirror(
        preset_id=_free_mirror_id(collaborators),
        source_preset_id=source_id,
        display_name=display["name"],
        display_description=display["description"],
        display_cover=display["cover"],
        offered_modes=modes["offered"],
        first_mode=modes["first"],
        configuration=dict(source_row.configuration or {}),
        form_overrides=dict(source_row.form_overrides or {}),
    )
    collaborators.preset_loader.refresh_overlays()
    return build_admin_detail(collaborators, mirror)


def _mirror_row(collaborators: PresetCollaborators, preset_id: str) -> Preset:
    row = collaborators.db_repo.get_installed_preset_by_preset_id(preset_id)
    if row is None:
        raise PresetRowNotFoundError(preset_id)
    if not row.is_mirror:
        raise NotAMirrorError(preset_id)
    return row


def set_mirror_modes(collaborators: PresetCollaborators, preset_id: str, payload: Any) -> Dict[str, Any]:
    row = _mirror_row(collaborators, preset_id)
    body = payload if isinstance(payload, dict) else {}
    source = files_template(collaborators, row)
    if source is None:
        raise InvalidModesError([f"the source preset '{row.source_preset_id}' is missing, so its modes are unknown"])
    modes = clean_modes(list(source.modes), body.get("offered"), body.get("first"))
    updated = collaborators.db_repo.set_mirror_modes(preset_id, modes["offered"], modes["first"])
    if updated is None:
        raise PresetRowNotFoundError(preset_id)
    collaborators.preset_loader.refresh_overlays()
    return build_admin_detail(collaborators, updated)


def delete_mirror(collaborators: PresetCollaborators, row: Preset) -> PresetTombstone:
    tombstone = collaborators.db_repo.delete_mirror(row.preset_id)
    if tombstone is None:
        raise PresetNotFoundException(row.preset_id)
    if collaborators.cover_store is not None:
        collaborators.cover_store.remove(row.id)
    return tombstone
