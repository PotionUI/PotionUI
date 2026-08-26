"""
Group membership: list/add/remove members, list a user's groups.

Module-level functions, collaborators as explicit leading args - no class
holds them together.
"""
import logging
from typing import List

from src.features.user_groups.dto import UserGroupDTO, UserGroupMemberDTO
from src.features.user_groups.hooks import USER_GROUP_HOOKS
from src.features.user_groups.mappers import group_to_dto, member_to_dto
from src.features.user_groups.operations.guards import require_admin, require_group_exists
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.security.user import User

logger = logging.getLogger(__name__)


def get_group_members(repository: UserGroupRepository, group_id: str, user: User) -> List[UserGroupMemberDTO]:
    """
    Get all members of a group.

    Raises:
        ValueError: If user is not admin or group not found
    """
    require_admin(user)
    require_group_exists(repository, group_id)

    members = repository.get_group_members(group_id)
    return [member_to_dto(m) for m in members]


def add_members(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, user_ids: List[str], user: User
) -> List[UserGroupMemberDTO]:
    """
    Add users to a group.

    Executes hooks for each member:
    - user_group.before_add_member: Can block addition
    - user_group.after_add_member: Notification of successful addition

    Raises:
        ValueError: If user is not admin or group not found

    Returns:
        List of added members (duplicates are skipped)
    """
    require_admin(user)
    require_group_exists(repository, group_id)

    added = []
    for user_id in user_ids:
        # Execute before_add_member hook
        hook_data, blocked = execute_hook(
            plugins,
            USER_GROUP_HOOKS.before_add_member,
            {"group_id": group_id, "user_id": user_id, "admin_id": user.id},
        )

        if blocked:
            reason = hook_data.get("block_reason", "Member addition blocked")
            logger.warning(f"Member addition blocked by plugin: {reason}")
            continue

        member = repository.add_user_to_group(group_id, user_id)
        if member:
            added.append(member_to_dto(member))

            # Execute after_add_member hook
            execute_hook(
                plugins,
                USER_GROUP_HOOKS.after_add_member,
                {"group_id": group_id, "user_id": user_id, "member_id": member.id},
            )

    logger.info(f"Added {len(added)} members to group {group_id}")
    return added


def remove_member(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, user_id: str, user: User
) -> bool:
    """
    Remove a user from a group.

    Executes hooks:
    - user_group.before_remove_member: Can block removal
    - user_group.after_remove_member: Notification of successful removal

    Raises:
        ValueError: If user is not admin, group not found, or member not found
    """
    require_admin(user)
    require_group_exists(repository, group_id)

    # Execute before_remove_member hook
    hook_data, blocked = execute_hook(
        plugins,
        USER_GROUP_HOOKS.before_remove_member,
        {"group_id": group_id, "user_id": user_id, "admin_id": user.id},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Member removal blocked")
        logger.warning(f"Member removal blocked by plugin: {reason}")
        raise ValueError(reason)

    removed = repository.remove_user_from_group(group_id, user_id)
    if not removed:
        raise ValueError("User is not a member of this group")

    # Execute after_remove_member hook
    execute_hook(plugins, USER_GROUP_HOOKS.after_remove_member, {"group_id": group_id, "user_id": user_id})

    logger.info(f"Removed user {user_id} from group {group_id}")
    return True


def get_user_groups(repository: UserGroupRepository, user_id: str, user: User) -> List[UserGroupDTO]:
    """
    Get all groups a user belongs to.

    Raises:
        ValueError: If requesting user is not admin
    """
    require_admin(user)

    groups = repository.get_user_groups(user_id)
    return [group_to_dto(g) for g in groups]
