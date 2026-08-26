"""Preset user-assignment operations.

Module-level functions, `PresetCollaborators` as their leading arg - no class
holds them together (see `src.features.presets.collaborators`'s docstring).
"""
import logging
from typing import Any, Dict, List

from src.platform.plugins.hooks import execute_hook
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.hooks import PRESET_HOOKS
from src.features.presets.exceptions import (
    InvalidUsersException,
    PermissionDeniedException,
    PresetNotAssignedException,
    PresetNotInstalledException,
    UserNotFoundException,
)
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


def assign_preset_to_users(
    collaborators: PresetCollaborators,
    preset_id: str,
    user_ids: List[str],
    admin: User
) -> Dict[str, Any]:
    """Assign a preset to multiple users.

    Executes hooks:
    - preset.before_assign: Can modify/validate data or block
    - preset.after_assign: Notification

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to assign
        user_ids: List of user IDs to assign to
        admin: The admin user performing the assignment

    Returns:
        Assignment result dictionary

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotInstalledException: If preset is not installed
        InvalidUsersException: If any users are invalid
    """
    # Only admins can assign presets
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("assign_preset_to_users")

    # Check if preset is installed
    if not collaborators.db_repo.is_preset_installed(preset_id):
        raise PresetNotInstalledException(preset_id)

    # Validate all user IDs exist
    invalid_users = []
    for user_id in user_ids:
        if not collaborators.user_repo.get_by_id(user_id):
            invalid_users.append(user_id)

    if invalid_users:
        raise InvalidUsersException(invalid_users)

    # Execute before_assign hook
    hook_data, blocked = execute_hook(collaborators.plugins,
        PRESET_HOOKS.before_assign,
        {
            "preset_id": preset_id,
            "user_ids": user_ids,
            "admin_id": admin.id
        }
    )

    if blocked:
        reason = hook_data.get("block_reason", "Assignment blocked")
        logger.warning(f"Preset assignment blocked by plugin: {reason}")
        raise PermissionDeniedException(f"assign_preset_to_users: {reason}")

    # Allow hooks to modify user list
    user_ids = hook_data.get("user_ids", user_ids)

    # Assign preset to users
    assignments = collaborators.db_repo.assign_preset_to_users(preset_id, user_ids)

    # Execute after_assign hook
    execute_hook(collaborators.plugins,
        PRESET_HOOKS.after_assign,
        {
            "preset_id": preset_id,
            "user_ids": user_ids,
            "admin_id": admin.id,
            "assigned_count": len(assignments)
        }
    )

    logger.info(f"Preset '{preset_id}' assigned to {len(assignments)} users by admin {admin.id}")
    return {
        'preset_id': preset_id,
        'assigned_count': len(assignments),
        'assignments': [assignment.to_dict() for assignment in assignments]
    }


def unassign_preset_from_user(
    collaborators: PresetCollaborators,
    preset_id: str,
    user_id: str,
    admin: User
) -> str:
    """Unassign a preset from a user.

    Executes hooks:
    - preset.before_unassign: Can block
    - preset.after_unassign: Notification

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to unassign
        user_id: The user ID to unassign from
        admin: The admin user performing the unassignment

    Returns:
        Success message

    Raises:
        PermissionDeniedException: If user is not an admin
        UserNotFoundException: If user is not found
        PresetNotAssignedException: If preset is not assigned to user
    """
    # Only admins can unassign presets
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("unassign_preset_from_user")

    # Check if user exists
    if not collaborators.user_repo.get_by_id(user_id):
        raise UserNotFoundException(user_id)

    # This endpoint manages the removable direct link only. Inherited group
    # access may remain after removal and must not make a missing direct
    # assignment look removable (or fire unassignment hooks for one).
    if not collaborators.db_repo.is_preset_directly_assigned_to_user(preset_id, user_id):
        raise PresetNotAssignedException(preset_id, user_id)

    # Execute before_unassign hook
    hook_data, blocked = execute_hook(collaborators.plugins,
        PRESET_HOOKS.before_unassign,
        {
            "preset_id": preset_id,
            "user_id": user_id,
            "admin_id": admin.id
        }
    )

    if blocked:
        reason = hook_data.get("block_reason", "Unassignment blocked")
        logger.warning(f"Preset unassignment blocked by plugin: {reason}")
        raise PermissionDeniedException(f"unassign_preset_from_user: {reason}")

    # Unassign the preset
    success = collaborators.db_repo.unassign_preset_from_user(preset_id, user_id)

    if not success:
        raise PresetNotAssignedException(preset_id, user_id)

    # Execute after_unassign hook
    execute_hook(collaborators.plugins,
        PRESET_HOOKS.after_unassign,
        {
            "preset_id": preset_id,
            "user_id": user_id,
            "admin_id": admin.id
        }
    )

    logger.info(f"Preset '{preset_id}' unassigned from user {user_id} by admin {admin.id}")
    return f"Preset '{preset_id}' unassigned from user '{user_id}'"


def get_preset_assignments(
    collaborators: PresetCollaborators,
    preset_id: str,
    admin: User
) -> Dict[str, Any]:
    """Get assignment summary for a preset.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID
        admin: The admin user requesting the summary

    Returns:
        Assignment summary dictionary

    Raises:
        PermissionDeniedException: If user is not an admin
        PresetNotInstalledException: If preset is not installed
    """
    # Only admins can view assignments
    if admin.account_type != AccountType.ADMIN:
        raise PermissionDeniedException("get_preset_assignments")

    summary = collaborators.db_repo.get_preset_assignment_summary(preset_id)

    if not summary['installed']:
        raise PresetNotInstalledException(preset_id)

    # Add user details to assignments
    for assignment in summary['assignments']:
        user = collaborators.user_repo.get_by_id(assignment['user_id'])
        if user:
            assignment['user'] = user.to_dict()

    return summary
