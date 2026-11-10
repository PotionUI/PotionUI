from typing import Any, Dict, List, Optional

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.cover_store import DISPLAY_ROOT, public_cover_exists
from src.features.presets.exceptions import (
    InvalidCoverError,
    InvalidDisplayError,
    PresetRowNotFoundError,
    PresetRowNotInstalledError,
)
from src.features.presets.mappers import admin_detail, mirror_entry
from src.features.presets.records import Preset

NAME_MAX = 80
DESCRIPTION_MAX = 4000
DISPLAY_KEYS = ("name", "description", "cover")


def installed_row(collaborators: PresetCollaborators, preset_id: str) -> Preset:
    row = collaborators.db_repo.get_installed_preset_by_preset_id(preset_id)
    if row is not None:
        return row
    if collaborators.preset_loader.get(preset_id) is not None:
        raise PresetRowNotInstalledError(preset_id)
    raise PresetRowNotFoundError(preset_id)


def files_template(collaborators: PresetCollaborators, row: Preset):
    return collaborators.preset_loader.file_template(row.source_preset_id or row.preset_id)


def build_admin_detail(collaborators: PresetCollaborators, row: Preset) -> Dict[str, Any]:
    loader = collaborators.preset_loader
    counts = collaborators.db_repo.get_installed_preset_summaries().get(row.preset_id)
    source = loader.get(row.source_preset_id) if row.is_mirror else None
    mirrors = (
        []
        if row.is_mirror
        else [mirror_entry(m, loader.mirror_state(m.preset_id)) for m in collaborators.db_repo.mirrors_of(row.preset_id)]
    )
    return admin_detail(
        row,
        files_template(collaborators, row),
        source.name if source is not None else None,
        loader.mirror_state(row.preset_id),
        mirrors,
        counts,
    )


def get_admin_detail(collaborators: PresetCollaborators, preset_id: str) -> Dict[str, Any]:
    row = collaborators.db_repo.get_installed_preset_by_preset_id(preset_id)
    if row is None:
        raise PresetRowNotFoundError(preset_id)
    return build_admin_detail(collaborators, row)


def _text(value: Any, label: str, errors: List[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"{label} must be text or null")
        return None
    return value.strip() or None


def clean_display(
    collaborators: PresetCollaborators,
    payload: Any,
    preset_db_id: Optional[str],
    files_path: Optional[str],
    is_mirror: bool,
) -> Dict[str, Optional[str]]:
    if not isinstance(payload, dict):
        raise InvalidDisplayError(["the body must be an object with name, description and cover"])
    errors = [f"'{key}' is required (send null to clear it)" for key in DISPLAY_KEYS if key not in payload]
    name = _text(payload.get("name"), "name", errors)
    description = _text(payload.get("description"), "description", errors)
    cover = _text(payload.get("cover"), "cover", errors)
    if name is None and is_mirror:
        errors.append("a mirror needs a name")
    if name is not None and len(name) > NAME_MAX:
        errors.append(f"name may be at most {NAME_MAX} characters")
    if description is not None and len(description) > DESCRIPTION_MAX:
        errors.append(f"description may be at most {DESCRIPTION_MAX} characters")
    if cover is not None and not _cover_allowed(collaborators, cover, preset_db_id, files_path):
        errors.append(f"cover '{cover}' is not a public file of this preset or a cover uploaded for it")
    if errors:
        raise InvalidDisplayError(errors)
    return {"name": name, "description": description, "cover": cover}


def _cover_allowed(
    collaborators: PresetCollaborators, cover: str, preset_db_id: Optional[str], files_path: Optional[str]
) -> bool:
    if cover.startswith(f"{DISPLAY_ROOT}/"):
        store = collaborators.cover_store
        return store is not None and preset_db_id is not None and store.has(preset_db_id, cover)
    return public_cover_exists(files_path, cover)


def _store(collaborators: PresetCollaborators):
    if collaborators.cover_store is None:
        raise InvalidCoverError("Cover uploads are not available on this instance")
    return collaborators.cover_store


def _save_display(collaborators: PresetCollaborators, row: Preset, values: Dict[str, Optional[str]]) -> Dict[str, Any]:
    updated = collaborators.db_repo.set_display(row.preset_id, values["name"], values["description"], values["cover"])
    if updated is None:
        raise PresetRowNotFoundError(row.preset_id)
    if collaborators.cover_store is not None:
        collaborators.cover_store.prune(updated.id, updated.display_cover)
    collaborators.preset_loader.refresh_overlays()
    return build_admin_detail(collaborators, updated)


def set_display(collaborators: PresetCollaborators, preset_id: str, payload: Any) -> Dict[str, Any]:
    row = installed_row(collaborators, preset_id)
    template = files_template(collaborators, row)
    values = clean_display(collaborators, payload, row.id, template.path if template else None, row.is_mirror)
    return _save_display(collaborators, row, values)


def upload_display_cover(
    collaborators: PresetCollaborators, preset_id: str, filename: str, content: bytes
) -> Dict[str, Any]:
    row = installed_row(collaborators, preset_id)
    cover = _store(collaborators).save(row.id, filename, content)
    values = {"name": row.display_name, "description": row.display_description, "cover": cover}
    return _save_display(collaborators, row, values)
