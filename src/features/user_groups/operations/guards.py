"""
Shared preconditions every user_groups operation opens with: the requesting
user must be an admin, and (for anything scoped to one group) the group must
exist. Module-level functions, no class holds them - every operation in this
package calls these two directly rather than through a shared base.
"""
from src.features.user_groups.dto import UserGroupDTO
from src.features.user_groups.mappers import group_to_dto
from src.features.user_groups.repository import UserGroupRepository
from src.platform.security.user import AccountType, User


def require_admin(user: User) -> None:
    """
    Verify the user has admin permissions.

    Raises:
        ValueError: If user is not an admin
    """
    if user.account_type != AccountType.ADMIN:
        raise ValueError("Admin access required")


def require_group_exists(repository: UserGroupRepository, group_id: str) -> UserGroupDTO:
    """
    Verify a group exists and return it.

    Raises:
        ValueError: If group does not exist
    """
    group = repository.get_group_by_id(group_id)
    if not group:
        raise ValueError("User group not found")
    return group_to_dto(group)
