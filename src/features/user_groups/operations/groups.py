"""
User group CRUD: list, create, get, update, delete.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"validation failed" (the controller converts that to an HTTP
response).
"""
import logging
from typing import List

from src.features.user_groups.dto import GroupCreate, GroupUpdate, GroupWithCountsDTO, UserGroupDTO
from src.features.user_groups.hooks import USER_GROUP_HOOKS
from src.features.user_groups.mappers import group_to_dto, group_to_counts_dto
from src.features.user_groups.operations.guards import require_admin, require_group_exists
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.security.user import User

logger = logging.getLogger(__name__)


class SystemGroupProtectedError(ValueError):
    """Raised when deletion of a built-in (`is_system=True`) group is attempted.

    A `ValueError` subclass so anything only expecting the base type still
    behaves reasonably; the controller catches this specific type first to
    surface HTTP 409 with a plain message instead of the generic 400 the
    routes give other `ValueError`s (see `UserGroupController.delete_group`).
    """

    def __init__(self, group_name: str):
        self.group_name = group_name
        super().__init__(f"'{group_name}' is a built-in group and can't be deleted.")


def get_all_groups(repository: UserGroupRepository, user: User) -> List[GroupWithCountsDTO]:
    """
    Get all user groups with resource counts.

    Raises:
        ValueError: If user is not an admin
    """
    require_admin(user)

    groups = repository.get_all_groups()
    return [group_to_counts_dto(group, repository) for group in groups]


def create_group(
    repository: UserGroupRepository, plugins: PluginRegistry, request: GroupCreate, user: User
) -> UserGroupDTO:
    """
    Create a new user group.

    Executes hooks:
    - user_group.before_create: Can modify/validate data or block
    - user_group.after_create: Notification of successful creation

    Raises:
        ValueError: If user is not admin, name exists, or creation blocked
    """
    require_admin(user)

    # Check for duplicate name
    existing = repository.get_group_by_name(request.name)
    if existing:
        raise ValueError("A group with this name already exists")

    # Execute before_create hook
    hook_data, blocked = execute_hook(
        plugins,
        USER_GROUP_HOOKS.before_create,
        {"name": request.name, "description": request.description, "user_id": user.id},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Group creation blocked")
        logger.warning(f"Group creation blocked by plugin: {reason}")
        raise ValueError(reason)

    # Allow hooks to modify data
    name = hook_data.get("name", request.name)
    description = hook_data.get("description", request.description)

    # Create the group
    group = repository.create_group(name=name, description=description)

    if not group:
        raise ValueError("Failed to create group")

    # Execute after_create hook
    execute_hook(
        plugins,
        USER_GROUP_HOOKS.after_create,
        {"group_id": group.id, "name": group.name, "description": group.description},
    )

    logger.info(f"Group created: {group.name} (id: {group.id})")
    return group_to_dto(group)


def get_group(repository: UserGroupRepository, group_id: str, user: User) -> GroupWithCountsDTO:
    """
    Get a specific group with resource counts.

    Raises:
        ValueError: If user is not admin or group not found
    """
    require_admin(user)
    group = require_group_exists(repository, group_id)

    return group_to_counts_dto(group, repository)


def update_group(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, request: GroupUpdate, user: User
) -> UserGroupDTO:
    """
    Update a user group.

    Executes hooks:
    - user_group.before_update: Can modify/validate data or block
    - user_group.after_update: Notification of successful update

    Raises:
        ValueError: If user is not admin, group not found, name exists, or update blocked
    """
    require_admin(user)
    existing_group = require_group_exists(repository, group_id)

    # Check for duplicate name if name is being changed
    if request.name is not None:
        existing = repository.get_group_by_name(request.name)
        if existing and existing.id != group_id:
            raise ValueError("A group with this name already exists")

    # Execute before_update hook
    hook_data, blocked = execute_hook(
        plugins,
        USER_GROUP_HOOKS.before_update,
        {
            "group_id": group_id,
            "old_name": existing_group.name,
            "new_name": request.name,
            "old_description": existing_group.description,
            "new_description": request.description,
            "user_id": user.id,
        },
    )

    if blocked:
        reason = hook_data.get("block_reason", "Group update blocked")
        logger.warning(f"Group update blocked by plugin: {reason}")
        raise ValueError(reason)

    # Allow hooks to modify data
    name = hook_data.get("new_name", request.name)
    description = hook_data.get("new_description", request.description)

    # Update the group
    updated = repository.update_group(group_id, name=name, description=description)

    if not updated:
        raise ValueError("Failed to update group")

    # Execute after_update hook
    execute_hook(
        plugins,
        USER_GROUP_HOOKS.after_update,
        {"group_id": group_id, "name": updated.name, "description": updated.description},
    )

    logger.info(f"Group updated: {updated.name} (id: {group_id})")
    return group_to_dto(updated)


def delete_group(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, user: User
) -> str:
    """
    Delete a user group.

    Executes hooks:
    - user_group.before_delete: Can block deletion
    - user_group.after_delete: Notification of successful deletion

    Raises:
        ValueError: If user is not admin, group not found, or deletion blocked
        SystemGroupProtectedError: If the group is a built-in group (is_system)

    Returns:
        The name of the deleted group
    """
    require_admin(user)
    group = require_group_exists(repository, group_id)

    if group.is_system:
        raise SystemGroupProtectedError(group.name)

    # Execute before_delete hook
    hook_data, blocked = execute_hook(
        plugins, USER_GROUP_HOOKS.before_delete, {"group_id": group_id, "name": group.name, "user_id": user.id}
    )

    if blocked:
        reason = hook_data.get("block_reason", "Group deletion blocked")
        logger.warning(f"Group deletion blocked by plugin: {reason}")
        raise ValueError(reason)

    # Delete the group
    success = repository.delete_group(group_id)
    if not success:
        raise ValueError("Failed to delete group")

    # Execute after_delete hook
    execute_hook(plugins, USER_GROUP_HOOKS.after_delete, {"group_id": group_id, "name": group.name})

    logger.info(f"Group deleted: {group.name} (id: {group_id})")
    return group.name
