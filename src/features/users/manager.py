"""
User Manager - Business logic for user operations.
"""
import logging
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any

from src.platform.security import PasswordHasher
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.settings.settings import SettingsManager
from src.features.users.hooks import USER_HOOKS
from src.features.users.repository import UserRepository
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)

AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


class UserManager:
    """
    Manages user operations with plugin hook support.

    This manager handles all user CRUD operations, delegating to
    the repository for data access and executing plugin hooks
    at key points in the lifecycle.
    """

    def __init__(
        self,
        user_repository: UserRepository,
        password_hasher: PasswordHasher,
        plugin_registry: PluginRegistry,
        settings_manager: SettingsManager
    ):
        self.repo = user_repository
        self.passwords = password_hasher
        self.plugins = plugin_registry
        self.settings = settings_manager

    def get_all(self) -> List[User]:
        """Get all users."""
        return self.repo.get_all()

    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.repo.get_by_id(user_id)

    def create(
        self,
        username: str,
        email: str,
        password: str,
        account_type: str = "USER"
    ) -> User:
        """
        Create a new user.

        Args:
            username: Unique username
            email: Unique email address
            password: Plain text password (will be hashed)
            account_type: USER or ADMIN

        Returns:
            Created User object

        Raises:
            ValueError: If validation fails or operation is blocked
        """
        # Validate username uniqueness
        if self.repo.exists_by_username(username):
            raise ValueError("Username already exists")

        # Validate email uniqueness
        if self.repo.exists_by_email(email):
            raise ValueError("Email already exists")

        # Validate account type
        try:
            account_type_enum = AccountType(account_type)
        except ValueError:
            raise ValueError("Invalid account type. Must be USER or ADMIN")

        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_HOOKS.before_create,
            {
                "username": username,
                "email": email,
                "account_type": account_type
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "User creation blocked by plugin")
            raise ValueError(reason)

        # Allow hooks to modify data
        username = hook_data.get("username", username)
        email = hook_data.get("email", email)

        # Hash password and create user
        hashed_password = self.passwords.hash(password)
        user = self.repo.create(
            username=username,
            email=email,
            password_hash=hashed_password,
            account_type=account_type_enum
        )

        # Execute after_create hook
        execute_hook(self.plugins,
            USER_HOOKS.after_create,
            {
                "user_id": user.id,
                "username": username,
                "email": email,
                "account_type": account_type_enum.value,
            }
        )

        logger.info(f"Created user: {username}")
        return user

    def update(
        self,
        user_id: str,
        username: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        account_type: Optional[str] = None,
        requesting_user: Optional[User] = None
    ) -> User:
        """
        Update user fields.

        Args:
            user_id: ID of user to update
            username: New username (optional)
            email: New email (optional)
            password: New password (optional)
            account_type: New account type (optional, admin only)
            requesting_user: User making the request (for permission checks)

        Returns:
            Updated User object

        Raises:
            ValueError: If validation fails or user not found
        """
        user = self.repo.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        update_data = {}

        # Validate and prepare username update
        if username is not None:
            if self.repo.exists_by_username(username) and user.username != username:
                raise ValueError("Username already exists")
            update_data["username"] = username

        # Validate and prepare email update
        if email is not None:
            if self.repo.exists_by_email(email) and user.email != email:
                raise ValueError("Email already exists")
            update_data["email"] = email

        # Prepare password update
        if password is not None:
            update_data["password_hash"] = self.passwords.hash(password)

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
        hook_data, blocked = execute_hook(self.plugins,
            USER_HOOKS.before_update,
            {"user_id": user_id, "updates": update_data}
        )

        if blocked:
            reason = hook_data.get("block_reason", "User update blocked by plugin")
            raise ValueError(reason)

        # Perform update
        updated_user = self.repo.update(user_id, **update_data)
        if not updated_user:
            raise ValueError("Failed to update user")

        # Execute after_update hook
        execute_hook(self.plugins,
            USER_HOOKS.after_update,
            {"user_id": user_id, "updates": update_data}
        )

        logger.info(f"Updated user: {user_id}")
        return updated_user

    def delete(self, user_id: str, requesting_user_id: str) -> bool:
        """
        Delete a user.

        Args:
            user_id: ID of user to delete
            requesting_user_id: ID of user making the request

        Returns:
            True if deleted successfully

        Raises:
            ValueError: If user not found, trying to delete self, or deletion fails
        """
        if user_id == requesting_user_id:
            raise ValueError("Cannot delete your own account")

        user = self.repo.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_HOOKS.before_delete,
            {"user_id": user_id, "username": user.username}
        )

        if blocked:
            reason = hook_data.get("block_reason", "User deletion blocked by plugin")
            raise ValueError(reason)

        # Perform deletion
        success = self.repo.delete(user_id)
        if not success:
            raise ValueError("Failed to delete user")

        if user.avatar_filename:
            self._delete_avatar_file(user.avatar_filename)

        # Execute after_delete hook
        execute_hook(self.plugins,
            USER_HOOKS.after_delete,
            {"user_id": user_id, "username": user.username}
        )

        logger.info(f"Deleted user: {user.username}")
        return True

    # ========== Avatar ==========

    def _avatars_directory(self) -> Path:
        """`storage/avatars/`, created lazily. Global, not per-user: filenames
        are opaque uuid4s, so there is no need for per-user isolation here."""
        storage_dir = self.settings.get_file_storage_directory()
        avatars_dir = Path(storage_dir) / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)
        return avatars_dir

    def _validate_avatar_path(self, path: Path, base: Path) -> bool:
        try:
            return path.resolve().is_relative_to(base.resolve())
        except Exception:
            return False

    def _delete_avatar_file(self, filename: str) -> None:
        avatars_dir = self._avatars_directory()
        path = avatars_dir / filename
        if not self._validate_avatar_path(path, avatars_dir):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(f"Failed to delete avatar file: {filename}")

    def resolve_avatar_path(self, filename: str) -> Path:
        """Resolve `filename` to a real file under the avatars directory.

        Raises ValueError uniformly for both traversal attempts and missing
        files, so a caller serving this over `<img src>` cannot distinguish
        the two from the response.
        """
        avatars_dir = self._avatars_directory()
        candidate = avatars_dir / filename

        if not self._validate_avatar_path(candidate, avatars_dir):
            raise ValueError("Avatar not found")

        resolved = candidate.resolve()
        if not resolved.is_file():
            raise ValueError("Avatar not found")

        return resolved

    def upload_avatar(
        self,
        user_id: str,
        file_data: bytes,
        filename: Optional[str],
        content_type: Optional[str]
    ) -> User:
        """
        Replace a user's avatar.

        Args:
            user_id: ID of the user whose avatar is being set
            file_data: Raw image bytes
            filename: Original filename (used only for its extension)
            content_type: MIME type reported by the upload

        Returns:
            Updated User

        Raises:
            ValueError: If the user is missing, the upload fails validation,
                or the write fails
        """
        user = self.repo.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        if not content_type or not content_type.startswith("image/"):
            raise ValueError("Only image files are allowed")

        ext = Path(filename).suffix.lower() if filename else ""
        if ext not in AVATAR_ALLOWED_EXTENSIONS:
            raise ValueError(
                "Unsupported image extension. Allowed: "
                + ", ".join(sorted(AVATAR_ALLOWED_EXTENSIONS))
            )

        if len(file_data) > AVATAR_MAX_BYTES:
            raise ValueError("Avatar exceeds the 5MB size limit")

        avatars_dir = self._avatars_directory()
        new_filename = f"{uuid.uuid4()}{ext}"
        (avatars_dir / new_filename).write_bytes(file_data)

        updated_user = self.repo.update(user_id, avatar_filename=new_filename)
        if not updated_user:
            self._delete_avatar_file(new_filename)
            raise ValueError("Failed to update avatar")

        if user.avatar_filename:
            self._delete_avatar_file(user.avatar_filename)

        logger.info(f"Updated avatar for user: {user_id}")
        return updated_user

    def delete_avatar(self, user_id: str) -> User:
        """
        Clear a user's avatar and remove its file.

        Args:
            user_id: ID of the user whose avatar is being cleared

        Returns:
            Updated User

        Raises:
            ValueError: If the user is missing or the update fails
        """
        user = self.repo.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        if user.avatar_filename:
            self._delete_avatar_file(user.avatar_filename)

        updated_user = self.repo.update(user_id, avatar_filename=None)
        if not updated_user:
            raise ValueError("Failed to update avatar")

        logger.info(f"Cleared avatar for user: {user_id}")
        return updated_user
