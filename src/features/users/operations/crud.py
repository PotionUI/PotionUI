"""
Create/update/delete a user, with plugin hooks.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"validation failed" (the controller converts that to an HTTP
response).
"""
import logging
from typing import Optional

from src.features.users.hooks import USER_HOOKS
from src.features.users.operations.avatar import delete_avatar_file
from src.features.users.repository import UserRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.security import PasswordHasher
from src.platform.security.user import AccountType, User
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


def create(
    repository: UserRepository,
    passwords: PasswordHasher,
    plugins: PluginRegistry,
    username: str,
    email: str,
    password: str,
    account_type: str = "USER",
) -> User:
    """
    Create a new user.

    Raises:
        ValueError: If validation fails or operation is blocked
    """
    # Validate username uniqueness
    if repository.exists_by_username(username):
        raise ValueError("Username already exists")

    # Validate email uniqueness
    if repository.exists_by_email(email):
        raise ValueError("Email already exists")

    # Validate account type
    try:
        account_type_enum = AccountType(account_type)
    except ValueError:
        raise ValueError("Invalid account type. Must be USER or ADMIN")

    # Execute before_create hook
    hook_data, blocked = execute_hook(
        plugins,
        USER_HOOKS.before_create,
        {"username": username, "email": email, "account_type": account_type},
    )

    if blocked:
        reason = hook_data.get("block_reason", "User creation blocked by plugin")
        raise ValueError(reason)

    # Allow hooks to modify data
    username = hook_data.get("username", username)
    email = hook_data.get("email", email)

    # Hash password and create user
    hashed_password = passwords.hash(password)
    user = repository.create(
        username=username,
        email=email,
        password_hash=hashed_password,
        account_type=account_type_enum,
    )

    # Execute after_create hook
    execute_hook(
        plugins,
        USER_HOOKS.after_create,
        {
            "user_id": user.id,
            "username": username,
            "email": email,
            "account_type": account_type_enum.value,
        },
    )

    logger.info(f"Created user: {username}")
    return user


def update(
    repository: UserRepository,
    passwords: PasswordHasher,
    plugins: PluginRegistry,
    user_id: str,
    username: Optional[str] = None,
    email: Optional[str] = None,
    password: Optional[str] = None,
    account_type: Optional[str] = None,
    requesting_user: Optional[User] = None,
) -> User:
    """
    Update user fields.

    Raises:
        ValueError: If validation fails or user not found
    """
    user = repository.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    update_data = {}

    # Validate and prepare username update
    if username is not None:
        if repository.exists_by_username(username) and user.username != username:
            raise ValueError("Username already exists")
        update_data["username"] = username

    # Validate and prepare email update
    if email is not None:
        if repository.exists_by_email(email) and user.email != email:
            raise ValueError("Email already exists")
        update_data["email"] = email

    # Prepare password update
    if password is not None:
        update_data["password_hash"] = passwords.hash(password)

    # Validate account type change (admin only)
    if account_type is not None:
        if requesting_user and requesting_user.account_type != AccountType.ADMIN:
            raise ValueError("Only admins can change account type")
        try:
            update_data["account_type"] = AccountType(account_type)
        except ValueError:
            raise ValueError("Invalid account type. Must be USER or ADMIN")

    if not update_data:
        raise ValueError("No valid fields to update")

    # Execute before_update hook
    hook_data, blocked = execute_hook(
        plugins, USER_HOOKS.before_update, {"user_id": user_id, "updates": update_data}
    )

    if blocked:
        reason = hook_data.get("block_reason", "User update blocked by plugin")
        raise ValueError(reason)

    # Perform update
    updated_user = repository.update(user_id, **update_data)
    if not updated_user:
        raise ValueError("Failed to update user")

    # Execute after_update hook
    execute_hook(plugins, USER_HOOKS.after_update, {"user_id": user_id, "updates": update_data})

    logger.info(f"Updated user: {user_id}")
    return updated_user


def delete(
    repository: UserRepository,
    plugins: PluginRegistry,
    settings: Settings,
    user_id: str,
    requesting_user_id: str,
) -> bool:
    """
    Delete a user.

    Raises:
        ValueError: If user not found, trying to delete self, or deletion fails
    """
    if user_id == requesting_user_id:
        raise ValueError("Cannot delete your own account")

    user = repository.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    # Execute before_delete hook
    hook_data, blocked = execute_hook(
        plugins, USER_HOOKS.before_delete, {"user_id": user_id, "username": user.username}
    )

    if blocked:
        reason = hook_data.get("block_reason", "User deletion blocked by plugin")
        raise ValueError(reason)

    # Perform deletion
    success = repository.delete(user_id)
    if not success:
        raise ValueError("Failed to delete user")

    if user.avatar_filename:
        delete_avatar_file(settings, user.avatar_filename)

    # Execute after_delete hook
    execute_hook(plugins, USER_HOOKS.after_delete, {"user_id": user_id, "username": user.username})

    logger.info(f"Deleted user: {user.username}")
    return True
