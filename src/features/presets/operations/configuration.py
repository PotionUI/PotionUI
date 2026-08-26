"""Preset admin-set configuration operations.

Module-level functions, `PresetCollaborators` as their leading arg - no class
holds them together (see `src.features.presets.collaborators`'s docstring).
Not to be confused with `src.features.presets.configuration` (the schema
merge/validate helpers this module calls).
"""
import logging
from typing import Any, Dict, List

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.configuration import merge_configuration_schema, validate_configuration_value
from src.features.presets.exceptions import (
    InvalidConfigurationException,
    PermissionDeniedException,
    PresetNotFoundException,
    PresetNotInstalledException,
)
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


def get_preset_configuration(collaborators: PresetCollaborators, preset_id: str) -> Dict[str, Any]:
    """Get a preset's declared configuration schema merged with its stored values.

    Returns `{"preset_id": ..., "entries": [{"key", "type", "label",
    "description", "value"}]}` - `entries` is empty if the preset declares no
    `configuration:` block at all (not an error).

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not found_preset:
        raise PresetNotFoundException(preset_id)

    stored_values = collaborators.db_repo.get_preset_configuration(preset_id)
    entries = merge_configuration_schema(found_preset.configuration, stored_values)

    return {"preset_id": preset_id, "entries": entries}


def set_preset_configuration(
    collaborators: PresetCollaborators,
    preset_id: str,
    values: Dict[str, Any],
    admin: User,
) -> Dict[str, Any]:
    """Set admin-set configuration values for a preset.

    Rejects unknown keys (not declared in preset.yml's `configuration:`) and
    type-invalid values (e.g. a `model_tags` entry naming a tag ID that doesn't
    exist), then persists and returns the same shape as `get_preset_configuration`.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID
        values: `{key: value}` - must be a subset of the preset's declared keys
        admin: The admin user performing the update

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotFoundException: If the preset is not found
        PresetNotInstalledException: If the preset is not installed
        InvalidConfigurationException: If any key is unknown or any value is invalid
    """
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("set_preset_configuration")

    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not found_preset:
        raise PresetNotFoundException(preset_id)

    if not collaborators.db_repo.is_preset_installed(preset_id):
        raise PresetNotInstalledException(preset_id)

    declared = found_preset.configuration or {}
    errors: List[str] = []

    unknown_keys = sorted(set(values.keys()) - set(declared.keys()))
    if unknown_keys:
        errors.append(f"unknown configuration key(s): {unknown_keys}")

    if not errors:
        from src.features.tags.repository import tag_repo

        for key, value in values.items():
            config_type = declared[key]["type"]
            error = validate_configuration_value(config_type, value, tag_repo)
            if error:
                errors.append(f"{key}: {error}")

    if errors:
        raise InvalidConfigurationException(preset_id, errors)

    # Merge into existing stored values rather than replacing wholesale, so a
    # PUT for one key never clobbers another key's previously-set value.
    current = collaborators.db_repo.get_preset_configuration(preset_id)
    current.update(values)
    collaborators.db_repo.set_preset_configuration(preset_id, current)

    logger.info(f"Preset '{preset_id}' configuration updated by admin {admin.id}: {sorted(values.keys())}")

    return get_preset_configuration(collaborators, preset_id)
