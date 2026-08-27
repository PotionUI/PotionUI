"""Preset admin-set form-override operations.

Module-level functions, `PresetCollaborators` as their leading arg - no class
holds them together (see `src.features.presets.collaborators`'s docstring).
Not to be confused with `src.features.presets.form_overrides` (the
inventory/validation helpers this module calls).
"""
import logging
from typing import Any, Dict, Optional

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import (
    InvalidFormOverridesException,
    ModeNotFoundException,
    NoModesAvailableException,
    PermissionDeniedException,
    PresetNotFoundException,
    PresetNotInstalledException,
)
from src.features.presets.form_overrides import (
    build_inventory_entries,
    validate_form_overrides,
)
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


def _validate_mode_exists(found_preset, preset_id: str, mode: Optional[str]) -> str:
    """Resolve `mode` (defaulting to the preset's first mode) and confirm it exists.

    Raises NoModesAvailableException if the preset declares no modes at
    all, or ModeNotFoundException if `mode` doesn't match one.
    """
    mode_keys = list((found_preset.modes or {}).keys())
    if not mode_keys:
        raise NoModesAvailableException(preset_id)
    if mode is None:
        return mode_keys[0]
    if mode not in mode_keys:
        raise ModeNotFoundException(preset_id, mode, mode_keys)
    return mode


def get_form_overrides_inventory(
    collaborators: PresetCollaborators, preset_id: str, mode: Optional[str], admin: User,
) -> Dict[str, Any]:
    """The admin tab's unmerged view: every field in `mode`'s inventory
    (union across its form variants), each with its preset-declared
    default, its current override (`None` if unset), and the label of the
    tab it lives in (`None` if untabbed) - plus the mode's ordered tab list,
    for a tab-grouped admin UI.

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotFoundException: If the preset is not found
        NoModesAvailableException: If the preset has no modes
        ModeNotFoundException: If `mode` doesn't exist on the preset
    """
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("get_form_overrides_inventory")

    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not found_preset:
        raise PresetNotFoundException(preset_id)

    mode = _validate_mode_exists(found_preset, preset_id, mode)

    stored_overrides = collaborators.db_repo.get_preset_form_overrides(preset_id).get(mode, {})
    fields, tabs = build_inventory_entries(found_preset, mode, stored_overrides)

    return {
        "preset_id": preset_id,
        "mode": mode,
        "modes": list((found_preset.modes or {}).keys()),
        "fields": fields,
        "tabs": tabs,
    }


def set_form_overrides(
    collaborators: PresetCollaborators,
    preset_id: str,
    mode: Optional[str],
    overrides: Dict[str, Any],
    admin: User,
) -> Dict[str, Any]:
    """Set admin per-field form overrides for one mode of a preset.

    Applies to every form variant of `mode`. Sending an empty object
    (`{}`) or `null` for a field clears its override. Rejects unknown
    field names (not in the mode's expanded field inventory) and
    type-invalid `default` values, then persists and returns the same
    shape as `get_form_overrides_inventory`.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID
        mode: The mode name
        overrides: `{field_name: {default?, editable?, visible?} | {} | None}`
        admin: The admin user performing the update

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotFoundException: If the preset is not found
        NoModesAvailableException: If the preset has no modes
        ModeNotFoundException: If `mode` doesn't exist on the preset
        PresetNotInstalledException: If the preset is not installed
        InvalidFormOverridesException: If any field is unknown or any value is invalid
    """
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("set_form_overrides")

    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not found_preset:
        raise PresetNotFoundException(preset_id)

    mode = _validate_mode_exists(found_preset, preset_id, mode)

    if not collaborators.db_repo.is_preset_installed(preset_id):
        raise PresetNotInstalledException(preset_id)

    errors = validate_form_overrides(found_preset, mode, overrides)
    if errors:
        raise InvalidFormOverridesException(preset_id, mode, errors)

    # Merge into the existing stored values for this mode rather than
    # replacing wholesale, so a PUT for one field never clobbers another
    # field's previously-set override - same pattern as
    # set_preset_configuration. A `{}`/`None` value clears that field's
    # override instead of storing it.
    stored_all = collaborators.db_repo.get_preset_form_overrides(preset_id)
    stored_mode = dict(stored_all.get(mode, {}))
    for field_name, value in overrides.items():
        if not value:
            stored_mode.pop(field_name, None)
        else:
            stored_mode[field_name] = value

    if stored_mode:
        stored_all[mode] = stored_mode
    else:
        stored_all.pop(mode, None)

    collaborators.db_repo.set_preset_form_overrides(preset_id, stored_all)

    logger.info(
        f"Preset '{preset_id}' mode '{mode}' form overrides updated by admin {admin.id}: "
        f"{sorted(overrides.keys())}"
    )

    return get_form_overrides_inventory(collaborators, preset_id, mode, admin)
