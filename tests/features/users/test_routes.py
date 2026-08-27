"""
Tests for UserController.

The controller calls `src.features.users.operations` functions directly
(module-level, no injected manager) for mutations (create/update/delete,
upload/delete avatar) and for `resolve_avatar_path`; pure reads
(`get_all`, `get_by_id`) go straight to `UserRepository`. `mock_operations`
patches the `operations` module as imported into `routes.py`, so tests assert
against it exactly like the previous manager mock, without the controller
holding a stateful collaborator it doesn't need.
"""
import pytest
import sys
import os
from unittest.mock import Mock, patch
from datetime import datetime
from fastapi import HTTPException, status

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.users import routes as routes_module
from src.features.users.routes import UserController
from src.platform.http.base_controller import APIResponse
from src.features.users.dto import UserCreate, UserUpdate, UserResponse
from src.platform.security.user import User, AccountType


class FakeUploadFile:
    """Minimal stand-in for `fastapi.UploadFile` (avatar controller only reads
    `.filename`, `.content_type`, and awaits `.read()`)."""

    def __init__(self, content: bytes, filename: str, content_type: str):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class TestUserController:
    """Comprehensive tests for UserController"""

    @pytest.fixture
    def mock_operations(self, monkeypatch):
        """Patch the `operations` module as seen by routes.py."""
        mock = Mock()
        monkeypatch.setattr(routes_module, "operations", mock)
        return mock

    @pytest.fixture
    def mock_user_repository(self):
        """Mock UserRepository"""
        return Mock()

    @pytest.fixture
    def sample_user(self):
        """Sample user data"""
        return User(
            id="test-user-123",
            username="testuser",
            email="test@example.com",
            password_hash="$2b$12$test.hash",
            account_type=AccountType.USER,
            created_at=datetime.utcnow(),
            last_login=None
        )

    @pytest.fixture
    def admin_user(self):
        """Sample admin user data"""
        return User(
            id="admin-user-123",
            username="admin",
            email="admin@example.com",
            password_hash="$2b$12$admin.hash",
            account_type=AccountType.ADMIN,
            created_at=datetime.utcnow(),
            last_login=None
        )

    @pytest.fixture
    def user_controller(self, mock_operations, mock_user_repository):
        """UserController instance with mocked collaborators"""
        return UserController(
            user_repository=mock_user_repository,
            password_hasher=Mock(),
            plugin_registry=Mock(),
            settings=Mock(),
        )

    # Test GET all users
    @pytest.mark.asyncio
    async def test_get_all_users_admin_success(self, user_controller, mock_user_repository, admin_user, sample_user):
        """Test successful retrieval of all users by admin"""
        mock_users = [sample_user, admin_user]
        mock_user_repository.get_all.return_value = mock_users

        response = await user_controller.get_all_users(current_user=admin_user)

        assert response.success is True
        assert len(response.data) == 2
        assert "Retrieved 2 users" in response.message
        mock_user_repository.get_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_users_non_admin_forbidden(self, user_controller, sample_user):
        """Test non-admin user cannot get all users"""
        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_all_users(current_user=sample_user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "insufficient_permissions" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_all_users_exception(self, user_controller, mock_user_repository, admin_user):
        """Test exception handling in get_all_users"""
        mock_user_repository.get_all.side_effect = Exception("Database error")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_all_users(current_user=admin_user)
        assert exc_info.value.status_code == 500

    # Test GET user by ID
    @pytest.mark.asyncio
    async def test_get_user_admin_success(self, user_controller, mock_user_repository, admin_user, sample_user):
        """Test admin can get any user"""
        mock_user_repository.get_by_id.return_value = sample_user

        response = await user_controller.get_user("test-user-123", current_user=admin_user)

        assert response.success is True
        assert response.data['id'] == "test-user-123"
        mock_user_repository.get_by_id.assert_called_once_with("test-user-123")

    @pytest.mark.asyncio
    async def test_get_user_own_profile_success(self, user_controller, mock_user_repository, sample_user):
        """Test user can get their own profile"""
        mock_user_repository.get_by_id.return_value = sample_user

        response = await user_controller.get_user("test-user-123", current_user=sample_user)

        assert response.success is True
        assert response.data['username'] == "testuser"

    @pytest.mark.asyncio
    async def test_get_user_non_admin_other_forbidden(self, user_controller, sample_user):
        """Test non-admin cannot get other user's profile"""
        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_user("other-user-456", current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, user_controller, mock_user_repository, admin_user):
        """Test user not found"""
        mock_user_repository.get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_user("nonexistent", current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    # Test POST create user
    @pytest.mark.asyncio
    async def test_create_user_admin_success(self, user_controller, mock_operations, admin_user, sample_user):
        """Test admin can create user"""
        mock_operations.create.return_value = sample_user

        user_data = UserCreate(
            username="newuser",
            email="new@example.com",
            password="password123",
            account_type="USER"
        )

        response = await user_controller.create_user(user_data, current_user=admin_user)

        assert response.success is True
        assert response.data['username'] == "testuser"
        mock_operations.create.assert_called_once_with(
            user_controller.repo, user_controller.passwords, user_controller.plugins,
            username="newuser",
            email="new@example.com",
            password="password123",
            account_type="USER"
        )

    @pytest.mark.asyncio
    async def test_create_user_non_admin_forbidden(self, user_controller, sample_user):
        """Test non-admin cannot create user"""
        user_data = UserCreate(
            username="newuser",
            email="new@example.com",
            password="password123"
        )

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.create_user(user_data, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_create_user_username_exists(self, user_controller, mock_operations, admin_user):
        """Test create user with existing username"""
        mock_operations.create.side_effect = ValueError("Username already exists")

        user_data = UserCreate(
            username="existinguser",
            email="new@example.com",
            password="password123"
        )

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.create_user(user_data, current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "username_exists" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_create_user_email_exists(self, user_controller, mock_operations, admin_user):
        """Test create user with existing email"""
        mock_operations.create.side_effect = ValueError("Email already exists")

        user_data = UserCreate(
            username="newuser",
            email="existing@example.com",
            password="password123"
        )

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.create_user(user_data, current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "email_exists" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_create_user_invalid_account_type(self, user_controller, mock_operations, admin_user):
        """Test create user with invalid account type"""
        mock_operations.create.side_effect = ValueError("Invalid account type. Must be USER or ADMIN")

        user_data = UserCreate(
            username="newuser",
            email="new@example.com",
            password="password123",
            account_type="INVALID"
        )

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.create_user(user_data, current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid_account_type" in str(exc_info.value.detail)

    # Test PUT update user
    @pytest.mark.asyncio
    async def test_update_user_admin_success(self, user_controller, mock_operations, admin_user, sample_user):
        """Test admin can update any user"""
        updated_user = User(
            id="test-user-123",
            username="updateduser",
            email="updated@example.com",
            password_hash="new_hash",
            account_type=AccountType.USER
        )
        mock_operations.update.return_value = updated_user

        user_data = UserUpdate(
            username="updateduser",
            email="updated@example.com",
            password="newpassword"
        )

        response = await user_controller.update_user("test-user-123", user_data, current_user=admin_user)

        assert response.success is True
        mock_operations.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_user_own_profile_success(self, user_controller, mock_operations, sample_user):
        """Test user can update own profile"""
        mock_operations.update.return_value = sample_user

        user_data = UserUpdate(username="newusername")

        response = await user_controller.update_user("test-user-123", user_data, current_user=sample_user)

        assert response.success is True

    @pytest.mark.asyncio
    async def test_update_user_non_admin_password_forbidden(self, user_controller, mock_operations, sample_user):
        """Test a non-admin cannot set their own password via PUT /users/{id}"""
        user_data = UserUpdate(password="newpassword123")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.update_user("test-user-123", user_data, current_user=sample_user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        mock_operations.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_user_admin_password_still_allowed(self, user_controller, mock_operations, admin_user, sample_user):
        """Test an admin can still set someone else's password via PUT /users/{id}"""
        mock_operations.update.return_value = sample_user

        user_data = UserUpdate(password="newpassword123")

        response = await user_controller.update_user("test-user-123", user_data, current_user=admin_user)

        assert response.success is True
        mock_operations.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_user_non_admin_other_forbidden(self, user_controller, sample_user):
        """Test non-admin cannot update other user"""
        user_data = UserUpdate(username="newname")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.update_user("other-user-456", user_data, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, user_controller, mock_operations, admin_user):
        """Test update non-existent user"""
        mock_operations.update.side_effect = ValueError("User not found")

        user_data = UserUpdate(username="newname")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.update_user("nonexistent", user_data, current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_user_account_type_non_admin_forbidden(self, user_controller, mock_operations, sample_user):
        """Test non-admin cannot change account type"""
        mock_operations.update.side_effect = ValueError("Only admins can change account type")

        user_data = UserUpdate(account_type="ADMIN")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.update_user("test-user-123", user_data, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_update_user_no_updates(self, user_controller, mock_operations, admin_user):
        """Test update with no valid fields"""
        mock_operations.update.side_effect = ValueError("No valid fields to update")

        user_data = UserUpdate()

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.update_user("test-user-123", user_data, current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "no_updates" in str(exc_info.value.detail)

    # Test DELETE user
    @pytest.mark.asyncio
    async def test_delete_user_admin_success(self, user_controller, mock_operations, mock_user_repository, admin_user, sample_user):
        """Test admin can delete user"""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_operations.delete.return_value = True

        response = await user_controller.delete_user("test-user-123", current_user=admin_user)

        assert response.success is True
        assert "deleted successfully" in response.message
        mock_operations.delete.assert_called_once_with(
            user_controller.repo, user_controller.plugins, user_controller.settings,
            user_id="test-user-123",
            requesting_user_id="admin-user-123"
        )

    @pytest.mark.asyncio
    async def test_delete_user_non_admin_forbidden(self, user_controller, sample_user):
        """Test non-admin cannot delete user"""
        with pytest.raises(HTTPException) as exc_info:
            await user_controller.delete_user("test-user-123", current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_delete_user_cannot_delete_self(self, user_controller, mock_operations, mock_user_repository, admin_user):
        """Test admin cannot delete themselves"""
        mock_user_repository.get_by_id.return_value = admin_user
        mock_operations.delete.side_effect = ValueError("Cannot delete your own account")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.delete_user("admin-user-123", current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot_delete_self" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_delete_user_not_found(self, user_controller, mock_operations, mock_user_repository, admin_user):
        """Test delete non-existent user"""
        mock_user_repository.get_by_id.return_value = None
        mock_operations.delete.side_effect = ValueError("User not found")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.delete_user("nonexistent", current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_delete_user_delete_failed(self, user_controller, mock_operations, mock_user_repository, admin_user, sample_user):
        """Test delete user database failure"""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_operations.delete.side_effect = ValueError("Failed to delete user")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.delete_user("test-user-123", current_user=admin_user)
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    # Test avatar upload/delete/serve
    @pytest.mark.asyncio
    async def test_upload_avatar_success(self, user_controller, mock_operations, sample_user):
        """Test uploading an avatar for own account succeeds"""
        updated_user = User(
            id="test-user-123", username="testuser", email="test@example.com",
            password_hash="$2b$12$test.hash", account_type=AccountType.USER,
            avatar_filename="new-avatar.png"
        )
        mock_operations.upload_avatar.return_value = updated_user
        fake_file = FakeUploadFile(b"image-bytes", filename="photo.png", content_type="image/png")

        response = await user_controller.upload_avatar("test-user-123", fake_file, current_user=sample_user)

        assert response.success is True
        assert response.data["avatar_url"] == "/api/users/avatars/new-avatar.png"
        mock_operations.upload_avatar.assert_called_once_with(
            user_controller.repo, user_controller.settings,
            user_id="test-user-123",
            file_data=b"image-bytes",
            filename="photo.png",
            content_type="image/png",
        )

    @pytest.mark.asyncio
    async def test_upload_avatar_non_admin_other_forbidden(self, user_controller, sample_user):
        """Test a non-admin cannot upload another user's avatar"""
        fake_file = FakeUploadFile(b"image-bytes", filename="photo.png", content_type="image/png")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.upload_avatar("other-user-456", fake_file, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_upload_avatar_bad_content_type(self, user_controller, mock_operations, sample_user):
        """Test uploading a non-image file is rejected with 400"""
        mock_operations.upload_avatar.side_effect = ValueError("Only image files are allowed")
        fake_file = FakeUploadFile(b"not-an-image", filename="evil.exe", content_type="application/octet-stream")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.upload_avatar("test-user-123", fake_file, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_upload_avatar_oversize(self, user_controller, mock_operations, sample_user):
        """Test uploading an oversized file is rejected with 413"""
        mock_operations.upload_avatar.side_effect = ValueError("Avatar exceeds the 5MB size limit")
        fake_file = FakeUploadFile(b"x" * 10, filename="big.png", content_type="image/png")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.upload_avatar("test-user-123", fake_file, current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    @pytest.mark.asyncio
    async def test_delete_avatar_clears_column(self, user_controller, mock_operations, sample_user):
        """Test deleting own avatar clears avatar_url"""
        cleared_user = User(
            id="test-user-123", username="testuser", email="test@example.com",
            password_hash="$2b$12$test.hash", account_type=AccountType.USER,
            avatar_filename=None
        )
        mock_operations.delete_avatar.return_value = cleared_user

        response = await user_controller.delete_avatar("test-user-123", current_user=sample_user)

        assert response.success is True
        assert response.data["avatar_url"] is None
        mock_operations.delete_avatar.assert_called_once_with(user_controller.repo, user_controller.settings, "test-user-123")

    @pytest.mark.asyncio
    async def test_delete_avatar_non_admin_other_forbidden(self, user_controller, sample_user):
        """Test a non-admin cannot delete another user's avatar"""
        with pytest.raises(HTTPException) as exc_info:
            await user_controller.delete_avatar("other-user-456", current_user=sample_user)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_get_avatar_unknown_filename_404(self, user_controller, mock_operations):
        """Test serving an unknown avatar filename returns a real 404"""
        mock_operations.resolve_avatar_path.side_effect = ValueError("Avatar not found")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_avatar("does-not-exist.png")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_avatar_traversal_attempt_404(self, user_controller, mock_operations):
        """Test a path-traversal filename is rejected as a uniform 404"""
        mock_operations.resolve_avatar_path.side_effect = ValueError("Avatar not found")

        with pytest.raises(HTTPException) as exc_info:
            await user_controller.get_avatar("../../etc/passwd")
        assert exc_info.value.status_code == 404

    # Test Pydantic models
    def test_user_create_model(self):
        """Test UserCreate model validation"""
        user_data = UserCreate(
            username="testuser",
            email="test@example.com",
            password="password123",
            account_type="USER"
        )
        assert user_data.username == "testuser"
        assert user_data.email == "test@example.com"
        assert user_data.account_type == "USER"

    def test_user_update_model(self):
        """Test UserUpdate model validation"""
        user_data = UserUpdate(
            username="updateduser",
            email="updated@example.com"
        )
        assert user_data.username == "updateduser"
        assert user_data.email == "updated@example.com"
        assert user_data.password is None

    def test_user_response_model(self):
        """Test UserResponse model"""
        response_data = UserResponse(
            id="test-123",
            username="testuser",
            email="test@example.com",
            account_type="USER"
        )
        assert response_data.id == "test-123"
        assert response_data.username == "testuser"
