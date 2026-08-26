"""Preset install/uninstall operations.

Module-level functions, `PresetCollaborators` as their leading arg - no class
holds them together (see `src.features.presets.collaborators`'s docstring).
"""
import logging
from typing import Any, Dict

from src.platform.plugins.hooks import execute_hook
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.hooks import PRESET_HOOKS
from src.features.presets.exceptions import (
    PermissionDeniedException,
    PresetAlreadyInstalledException,
    PresetNotFoundException,
    PresetNotInstalledException,
)
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


def install_preset(collaborators: PresetCollaborators, preset_id: str, user: User) -> Dict[str, Any]:
    """Install a preset.

    Executes hooks:
    - preset.before_install: Can modify/validate data or block
    - preset.after_install: Notification of successful installation

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to install
        user: The admin user performing the installation

    Returns:
        Installed preset data

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotFoundException: If the preset is not found
        PresetAlreadyInstalledException: If already installed
    """
    # Only admins can install presets
    if user.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("install_preset")

    # Check if preset exists in files
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not found_preset:
        raise PresetNotFoundException(preset_id)

    # Check if already installed
    if collaborators.db_repo.is_preset_installed(preset_id):
        raise PresetAlreadyInstalledException(preset_id)

    # Execute before_install hook
    hook_data, blocked = execute_hook(collaborators.plugins,
        PRESET_HOOKS.before_install,
        {
            "preset_id": preset_id,
            "user_id": user.id,
            "preset_name": found_preset.name
        }
    )

    if blocked:
        reason = hook_data.get("block_reason", "Installation blocked")
        logger.warning(f"Preset installation blocked by plugin: {reason}")
        raise PermissionDeniedException(f"install_preset: {reason}")

    # Install the preset
    installed_preset = collaborators.db_repo.install_preset(preset_id)

    # Execute after_install hook
    execute_hook(collaborators.plugins,
        PRESET_HOOKS.after_install,
        {
            "preset_id": preset_id,
            "user_id": user.id,
            "preset_name": found_preset.name,
            "installed_preset_id": installed_preset.id
        }
    )

    logger.info(f"Preset '{preset_id}' installed by user {user.id}")
    return installed_preset.to_dict()


def uninstall_preset(collaborators: PresetCollaborators, preset_id: str, user: User) -> str:
    """Uninstall a preset and remove all user assignments.

    Executes hooks:
    - preset.before_uninstall: Can block
    - preset.after_uninstall: Notification

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to uninstall
        user: The admin user performing the uninstallation

    Returns:
        Success message with count of removed assignments

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotInstalledException: If preset is not installed
    """
    # Only admins can uninstall presets
    if user.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("uninstall_preset")

    # Check if preset is installed
    if not collaborators.db_repo.is_preset_installed(preset_id):
        raise PresetNotInstalledException(preset_id)

    # Get assignment summary before uninstalling
    summary = collaborators.db_repo.get_preset_assignment_summary(preset_id)

    # Execute before_uninstall hook
    hook_data, blocked = execute_hook(collaborators.plugins,
        PRESET_HOOKS.before_uninstall,
        {
            "preset_id": preset_id,
            "user_id": user.id,
            "assignment_count": summary['total_assignments']
        }
    )

    if blocked:
        reason = hook_data.get("block_reason", "Uninstallation blocked")
        logger.warning(f"Preset uninstallation blocked by plugin: {reason}")
        raise PermissionDeniedException(f"uninstall_preset: {reason}")

    # Uninstall the preset
    success = collaborators.db_repo.uninstall_preset(preset_id)

    if not success:
        raise PresetNotInstalledException(preset_id)

    # Execute after_uninstall hook
    execute_hook(collaborators.plugins,
        PRESET_HOOKS.after_uninstall,
        {
            "preset_id": preset_id,
            "user_id": user.id,
            "removed_assignments": summary['total_assignments']
        }
    )

    logger.info(f"Preset '{preset_id}' uninstalled by user {user.id}")
    return f"Preset '{preset_id}' uninstalled successfully. Removed {summary['total_assignments']} user assignments."
