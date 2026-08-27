"""
User Controller

Handles user CRUD operations for admin user management. Mutations and avatar
handling go through `src.features.users.operations` (formerly `UserManager`);
reads (`get_all`, `get_by_id`) go straight to `UserRepository`.
"""
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_user
from src.features.users.dto import UserCreate, UserUpdate, UserResponse
from src.features.users import operations
from src.features.users.repository import UserRepository
from src.platform.plugins import PluginRegistry
from src.platform.security import PasswordHasher
from src.platform.security.user import User, AccountType
from src.platform.settings.settings import Settings

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class UserController(BaseController):
    """
    Controller for user management operations.

    Handles user CRUD endpoints. Admin-only operations are enforced at the
    controller level.
    """

    def __init__(
        self,
        user_repository: UserRepository,
        password_hasher: PasswordHasher,
        plugin_registry: PluginRegistry,
        settings: Settings,
    ):
        super().__init__()
        self.repo = user_repository
        self.passwords = password_hasher
        self.plugins = plugin_registry
        self.settings = settings

    async def get_all_users(self, current_user: User) -> APIResponse:
        """Get all users (admin only)."""
        if current_user.account_type != AccountType.ADMIN:
            self.error_response(
                error="insufficient_permissions",
                message="Admin access required",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            users = self.repo.get_all()
            user_responses = [UserResponse(**user.to_dict()).model_dump() for user in users]

            return self.success_response(
                data=user_responses,
                message=f"Retrieved {len(users)} users"
            )
        except Exception as e:
            return self.handle_exception(e, "get_users_failed", "Failed to retrieve users")

    async def get_user(self, user_id: str, current_user: User) -> APIResponse:
        """Get user by ID."""
        if current_user.account_type != AccountType.ADMIN and current_user.id != user_id:
            self.error_response(
                error="insufficient_permissions",
                message="Can only access your own user data",
                status_code=status.HTTP_403_FORBIDDEN
            )

        user = self.repo.get_by_id(user_id)
        if not user:
            self.error_response(
                error="user_not_found",
                message="User not found",
                status_code=status.HTTP_404_NOT_FOUND
            )

        try:
            return self.success_response(
                data=UserResponse(**user.to_dict()).model_dump(),
                message="User retrieved successfully"
            )
        except Exception as e:
            return self.handle_exception(e, "get_user_failed", f"Failed to retrieve user {user_id}")

    async def create_user(self, user_data: UserCreate, current_user: User) -> APIResponse:
        """Create new user (admin only)."""
        if current_user.account_type != AccountType.ADMIN:
            self.error_response(
                error="insufficient_permissions",
                message="Admin access required",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            user = operations.create(
                self.repo, self.passwords, self.plugins,
                username=user_data.username,
                email=user_data.email,
                password=user_data.password,
                account_type=user_data.account_type
            )

            return self.success_response(
                data=UserResponse(**user.to_dict()).model_dump(),
                message="User created successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "Username already exists" in error_msg:
                self.error_response(
                    error="username_exists",
                    message="Username already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "Email already exists" in error_msg:
                self.error_response(
                    error="email_exists",
                    message="Email already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "Invalid account type" in error_msg:
                self.error_response(
                    error="invalid_account_type",
                    message="Invalid account type. Must be USER or ADMIN",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            else:
                self.error_response(
                    error="create_user_failed",
                    message=error_msg,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return self.handle_exception(e, "create_user_failed", "Failed to create user")

    async def update_user(
        self, user_id: str, user_data: UserUpdate, current_user: User
    ) -> APIResponse:
        """Update user."""
        if current_user.account_type != AccountType.ADMIN and current_user.id != user_id:
            self.error_response(
                error="insufficient_permissions",
                message="Can only update your own user data",
                status_code=status.HTTP_403_FORBIDDEN
            )

        if user_data.password is not None and current_user.account_type != AccountType.ADMIN:
            self.error_response(
                error="insufficient_permissions",
                message="Use POST /api/auth/change-password to change your own password",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            updated_user = operations.update(
                self.repo, self.passwords, self.plugins,
                user_id=user_id,
                username=user_data.username,
                email=user_data.email,
                password=user_data.password,
                account_type=user_data.account_type,
                requesting_user=current_user
            )

            return self.success_response(
                data=UserResponse(**updated_user.to_dict()).model_dump(),
                message="User updated successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "User not found" in error_msg:
                self.error_response(
                    error="user_not_found",
                    message="User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            elif "Username already exists" in error_msg:
                self.error_response(
                    error="username_exists",
                    message="Username already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "Email already exists" in error_msg:
                self.error_response(
                    error="email_exists",
                    message="Email already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "Only admins can change account type" in error_msg:
                self.error_response(
                    error="insufficient_permissions",
                    message="Only admins can change account type",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            elif "Invalid account type" in error_msg:
                self.error_response(
                    error="invalid_account_type",
                    message="Invalid account type. Must be USER or ADMIN",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "No valid fields to update" in error_msg:
                self.error_response(
                    error="no_updates",
                    message="No valid fields to update",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "Failed to update user" in error_msg:
                self.error_response(
                    error="update_failed",
                    message="Failed to update user",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            else:
                self.error_response(
                    error="update_user_failed",
                    message=error_msg,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return self.handle_exception(e, "update_user_failed", f"Failed to update user {user_id}")

    async def delete_user(self, user_id: str, current_user: User) -> APIResponse:
        """Delete user (admin only, cannot delete self)."""
        if current_user.account_type != AccountType.ADMIN:
            self.error_response(
                error="insufficient_permissions",
                message="Admin access required",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            # Get username before deletion for response message
            user = self.repo.get_by_id(user_id)
            username = user.username if user else "unknown"

            operations.delete(self.repo, self.plugins, self.settings, user_id=user_id, requesting_user_id=current_user.id)

            return self.success_response(
                message=f"User {username} deleted successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "Cannot delete your own account" in error_msg:
                self.error_response(
                    error="cannot_delete_self",
                    message="Cannot delete your own account",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            elif "User not found" in error_msg:
                self.error_response(
                    error="user_not_found",
                    message="User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            elif "Failed to delete user" in error_msg:
                self.error_response(
                    error="delete_failed",
                    message="Failed to delete user",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            else:
                self.error_response(
                    error="delete_user_failed",
                    message=error_msg,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return self.handle_exception(e, "delete_user_failed", f"Failed to delete user {user_id}")

    async def upload_avatar(self, user_id: str, file: UploadFile, current_user: User) -> APIResponse:
        """Upload/replace a user's avatar (self-or-admin)."""
        if current_user.account_type != AccountType.ADMIN and current_user.id != user_id:
            self.error_response(
                error="insufficient_permissions",
                message="Can only update your own avatar",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            content = await file.read()
            updated_user = operations.upload_avatar(
                self.repo, self.settings,
                user_id=user_id,
                file_data=content,
                filename=file.filename,
                content_type=file.content_type
            )

            return self.success_response(
                data=UserResponse(**updated_user.to_dict()).model_dump(),
                message="Avatar updated successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "User not found" in error_msg:
                self.error_response(
                    error="user_not_found",
                    message="User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            elif "5MB size limit" in error_msg:
                self.error_response(
                    error="avatar_too_large",
                    message=error_msg,
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )
            else:
                self.error_response(
                    error="invalid_avatar",
                    message=error_msg,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return self.handle_exception(e, "avatar_upload_failed", f"Failed to update avatar for user {user_id}")

    async def delete_avatar(self, user_id: str, current_user: User) -> APIResponse:
        """Clear a user's avatar (self-or-admin)."""
        if current_user.account_type != AccountType.ADMIN and current_user.id != user_id:
            self.error_response(
                error="insufficient_permissions",
                message="Can only update your own avatar",
                status_code=status.HTTP_403_FORBIDDEN
            )

        try:
            updated_user = operations.delete_avatar(self.repo, self.settings, user_id)

            return self.success_response(
                data=UserResponse(**updated_user.to_dict()).model_dump(),
                message="Avatar removed successfully"
            )
        except ValueError as e:
            error_msg = str(e)
            if "User not found" in error_msg:
                self.error_response(
                    error="user_not_found",
                    message="User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            else:
                self.error_response(
                    error="avatar_delete_failed",
                    message=error_msg,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return self.handle_exception(e, "avatar_delete_failed", f"Failed to delete avatar for user {user_id}")

    async def get_avatar(self, filename: str):
        """Serve an avatar file by name.

        Unauthenticated, like `serve_preset_file`: this is reached through
        plain `<img src>` tags, which never carry an Authorization header,
        and an avatar isn't sensitive data. Real HTTP status codes (not the
        APIResponse envelope) so the browser's `<img>` error handler fires on
        a missing/invalid file instead of trying to decode JSON as pixels.
        """
        try:
            avatar_path = operations.resolve_avatar_path(self.settings, filename)
            return FileResponse(
                path=str(avatar_path),
                headers={"Cache-Control": "public, max-age=31536000, immutable"}
            )
        except ValueError:
            raise HTTPException(status_code=404, detail="Avatar not found")
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error serving avatar: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to serve avatar")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.user_controller
    router = APIRouter(prefix="/api/users", tags=["users"])

    @router.get("/", response_model=APIResponse, summary="Get All Users")
    async def get_all_users(current_user: User = Depends(get_current_user)) -> APIResponse:
        """Get all users (admin only)."""
        return await controller.get_all_users(current_user)

    @router.get("/{user_id}", response_model=APIResponse, summary="Get User")
    async def get_user(user_id: str, current_user: User = Depends(get_current_user)) -> APIResponse:
        """Get user by ID."""
        return await controller.get_user(user_id, current_user)

    @router.post("/", response_model=APIResponse, summary="Create User")
    async def create_user(
        user_data: UserCreate, current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Create new user (admin only)."""
        return await controller.create_user(user_data, current_user)

    @router.put("/{user_id}", response_model=APIResponse, summary="Update User")
    async def update_user(
        user_id: str, user_data: UserUpdate, current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Update user."""
        return await controller.update_user(user_id, user_data, current_user)

    @router.delete("/{user_id}", response_model=APIResponse, summary="Delete User")
    async def delete_user(user_id: str, current_user: User = Depends(get_current_user)) -> APIResponse:
        """Delete user (admin only, cannot delete self)."""
        return await controller.delete_user(user_id, current_user)

    @router.post("/{user_id}/avatar", response_model=APIResponse, summary="Upload User Avatar")
    async def upload_avatar(
        user_id: str,
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Upload/replace a user's avatar (self-or-admin)."""
        return await controller.upload_avatar(user_id, file, current_user)

    @router.delete("/{user_id}/avatar", response_model=APIResponse, summary="Delete User Avatar")
    async def delete_avatar(
        user_id: str, current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Clear a user's avatar (self-or-admin)."""
        return await controller.delete_avatar(user_id, current_user)

    @router.get("/avatars/{filename}", summary="Serve User Avatar")
    async def get_avatar(filename: str):
        """Serve an avatar file. Unauthenticated - see `UserController.get_avatar`."""
        return await controller.get_avatar(filename)

    return router
