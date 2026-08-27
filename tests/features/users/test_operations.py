"""
Tests for src.features.users.operations (formerly UserManager).

`get_all`/`get_by_id` had no manager-level logic - they were pure
`UserRepository` passthroughs - so they have no operations-layer test here;
see tests/features/users/test_routes.py for controller-level coverage.
"""
import pytest
from unittest.mock import Mock, MagicMock

from src.features.users import operations
from src.platform.security import PasswordHasher
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings
from src.features.users.hooks import USER_HOOKS
from src.features.users.repository import UserRepository
from src.platform.security.user import User, AccountType
from src.platform.plugins.hooks import execute_hook


@pytest.fixture
def mock_user_repository():
    """Mock UserRepository for testing."""
    repo = Mock(spec=UserRepository)
    return repo


@pytest.fixture
def mock_password_hasher():
    """Mock PasswordHasher for testing."""
    hasher = Mock(spec=PasswordHasher)
    hasher.hash.return_value = "hashed_password"
    return hasher


@pytest.fixture
def mock_plugin_registry():
    """Mock PluginRegistry for testing."""
    registry = Mock(spec=PluginRegistry)
    # Default: no hooks block operations
    mock_context = MagicMock()
    mock_context.data = {}
    registry.execute_hook.return_value = (mock_context, [])
    return registry


@pytest.fixture
def mock_settings(tmp_path):
    """Mock Settings for testing, backed by a real scratch directory
    so avatar-related tests can write files."""
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(tmp_path)
    return settings


@pytest.fixture
def sample_user():
    """Create a sample user for testing."""
    return User(
        id="user-123",
        username="testuser",
        email="test@example.com",
        password_hash="hashed_password",
        account_type=AccountType.USER
    )


@pytest.fixture
def sample_admin():
    """Create a sample admin user for testing."""
    return User(
        id="admin-123",
        username="admin",
        email="admin@example.com",
        password_hash="hashed_password",
        account_type=AccountType.ADMIN
    )


class TestCreate:
    """Tests for operations.create."""

    def test_create_success(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should create user successfully with default USER account type."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_user_repository.create.return_value = sample_user

        result = operations.create(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            username="testuser",
            email="test@example.com",
            password="plain_password"
        )

        assert result == sample_user
        mock_password_hasher.hash.assert_called_once_with("plain_password")
        mock_user_repository.create.assert_called_once_with(
            username="testuser",
            email="test@example.com",
            password_hash="hashed_password",
            account_type=AccountType.USER
        )

    def test_create_with_admin_account_type(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_admin):
        """Should create admin user when account_type is ADMIN."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_user_repository.create.return_value = sample_admin

        result = operations.create(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            username="admin",
            email="admin@example.com",
            password="admin_password",
            account_type="ADMIN"
        )

        assert result == sample_admin
        mock_user_repository.create.assert_called_once()
        call_kwargs = mock_user_repository.create.call_args[1]
        assert call_kwargs["account_type"] == AccountType.ADMIN

    def test_create_fails_when_username_exists(self, mock_user_repository, mock_password_hasher, mock_plugin_registry):
        """Should raise ValueError when username already exists."""
        mock_user_repository.exists_by_username.return_value = True

        with pytest.raises(ValueError, match="Username already exists"):
            operations.create(
                mock_user_repository, mock_password_hasher, mock_plugin_registry,
                username="existing",
                email="test@example.com",
                password="password"
            )

    def test_create_fails_when_email_exists(self, mock_user_repository, mock_password_hasher, mock_plugin_registry):
        """Should raise ValueError when email already exists."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = True

        with pytest.raises(ValueError, match="Email already exists"):
            operations.create(
                mock_user_repository, mock_password_hasher, mock_plugin_registry,
                username="testuser",
                email="existing@example.com",
                password="password"
            )

    def test_create_fails_with_invalid_account_type(self, mock_user_repository, mock_password_hasher, mock_plugin_registry):
        """Should raise ValueError when account type is invalid."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False

        with pytest.raises(ValueError, match="Invalid account type"):
            operations.create(
                mock_user_repository, mock_password_hasher, mock_plugin_registry,
                username="testuser",
                email="test@example.com",
                password="password",
                account_type="INVALID"
            )

    def test_create_executes_before_create_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should execute before_create hook."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_user_repository.create.return_value = sample_user

        operations.create(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            username="testuser",
            email="test@example.com",
            password="password"
        )

        mock_plugin_registry.execute_hook.assert_any_call(
            USER_HOOKS.before_create,
            initial_data={
                "username": "testuser",
                "email": "test@example.com",
                "account_type": "USER"
            }
        )

    def test_create_executes_after_create_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should execute after_create hook."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_user_repository.create.return_value = sample_user

        operations.create(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            username="testuser",
            email="test@example.com",
            password="password"
        )

        mock_plugin_registry.execute_hook.assert_any_call(
            USER_HOOKS.after_create,
            initial_data={
                "user_id": "user-123",
                "username": "testuser",
                "email": "test@example.com",
                "account_type": "USER"
            }
        )

    def test_create_blocked_by_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry):
        """Should raise ValueError when before_create hook blocks operation."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False

        mock_context = MagicMock()
        mock_context.data = {"blocked": True, "block_reason": "Custom block reason"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ValueError, match="Custom block reason"):
            operations.create(
                mock_user_repository, mock_password_hasher, mock_plugin_registry,
                username="testuser",
                email="test@example.com",
                password="password"
            )

    def test_create_allows_hook_to_modify_data(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should allow hooks to modify username and email."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_user_repository.create.return_value = sample_user

        # Hook modifies data
        mock_context = MagicMock()
        mock_context.data = {
            "username": "modified_username",
            "email": "modified@example.com",
            "account_type": "USER"
        }
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        operations.create(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            username="original",
            email="original@example.com",
            password="password"
        )

        # Should use modified values
        mock_user_repository.create.assert_called_once()
        call_kwargs = mock_user_repository.create.call_args[1]
        assert call_kwargs["username"] == "modified_username"
        assert call_kwargs["email"] == "modified@example.com"


class TestUpdate:
    """Tests for operations.update."""

    def test_update_username_success(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should update username successfully."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = False
        updated_user = User(**{**sample_user.__dict__, "username": "newusername"})
        mock_user_repository.update.return_value = updated_user

        result = operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username="newusername")

        assert result.username == "newusername"
        mock_user_repository.update.assert_called_once()

    def test_update_email_success(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should update email successfully."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_email.return_value = False
        updated_user = User(**{**sample_user.__dict__, "email": "new@example.com"})
        mock_user_repository.update.return_value = updated_user

        result = operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", email="new@example.com")

        assert result.email == "new@example.com"
        mock_user_repository.update.assert_called_once()

    def test_update_password_success(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should update password successfully."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.update.return_value = sample_user

        operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", password="new_password")

        mock_password_hasher.hash.assert_called_once_with("new_password")
        mock_user_repository.update.assert_called_once()

    def test_update_account_type_as_admin(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user, sample_admin):
        """Should allow admin to update account type."""
        mock_user_repository.get_by_id.return_value = sample_user
        updated_user = User(**{**sample_user.__dict__, "account_type": AccountType.ADMIN})
        mock_user_repository.update.return_value = updated_user

        result = operations.update(
            mock_user_repository, mock_password_hasher, mock_plugin_registry,
            "user-123",
            account_type="ADMIN",
            requesting_user=sample_admin
        )

        assert result.account_type == AccountType.ADMIN

    def test_update_account_type_as_non_admin_fails(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should prevent non-admin from updating account type."""
        mock_user_repository.get_by_id.return_value = sample_user

        with pytest.raises(ValueError, match="Only admins can change account type"):
            operations.update(
                mock_user_repository, mock_password_hasher, mock_plugin_registry,
                "user-123",
                account_type="ADMIN",
                requesting_user=sample_user  # Non-admin user
            )

    def test_update_fails_when_user_not_found(self, mock_user_repository, mock_password_hasher, mock_plugin_registry):
        """Should raise ValueError when user not found."""
        mock_user_repository.get_by_id.return_value = None

        with pytest.raises(ValueError, match="User not found"):
            operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "nonexistent", username="newname")

    def test_update_fails_when_username_taken(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should raise ValueError when new username is already taken."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = True

        with pytest.raises(ValueError, match="Username already exists"):
            operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username="taken")

    def test_update_allows_keeping_same_username(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should allow updating with same username (no conflict)."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = True  # Exists but is the same user
        mock_user_repository.update.return_value = sample_user

        # Should not raise error since it's the same username
        operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username=sample_user.username)

        mock_user_repository.update.assert_called_once()

    def test_update_fails_when_no_fields_provided(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should raise ValueError when no valid fields to update."""
        mock_user_repository.get_by_id.return_value = sample_user

        with pytest.raises(ValueError, match="No valid fields to update"):
            operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123")

    def test_update_executes_before_update_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should execute before_update hook."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.update.return_value = sample_user

        operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username="newname")

        # Check that hook was called
        calls = [call for call in mock_plugin_registry.execute_hook.call_args_list
                 if call[0][0] == USER_HOOKS.before_update]
        assert len(calls) == 1

    def test_update_executes_after_update_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should execute after_update hook."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.update.return_value = sample_user

        operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username="newname")

        # Check that hook was called
        calls = [call for call in mock_plugin_registry.execute_hook.call_args_list
                 if call[0][0] == USER_HOOKS.after_update]
        assert len(calls) == 1

    def test_update_blocked_by_hook(self, mock_user_repository, mock_password_hasher, mock_plugin_registry, sample_user):
        """Should raise ValueError when before_update hook blocks operation."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.exists_by_username.return_value = False

        mock_context = MagicMock()
        mock_context.data = {"blocked": True, "block_reason": "Update not allowed"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ValueError, match="Update not allowed"):
            operations.update(mock_user_repository, mock_password_hasher, mock_plugin_registry, "user-123", username="newname")


class TestDelete:
    """Tests for operations.delete."""

    def test_delete_success(self, mock_user_repository, mock_plugin_registry, mock_settings, sample_user):
        """Should delete user successfully."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.delete.return_value = True

        result = operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="other-user")

        assert result is True
        mock_user_repository.delete.assert_called_once_with("user-123")

    def test_delete_fails_when_user_not_found(self, mock_user_repository, mock_plugin_registry, mock_settings):
        """Should raise ValueError when user not found."""
        mock_user_repository.get_by_id.return_value = None

        with pytest.raises(ValueError, match="User not found"):
            operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "nonexistent", requesting_user_id="other-user")

    def test_delete_fails_when_deleting_self(self, mock_user_repository, mock_plugin_registry, mock_settings):
        """Should raise ValueError when trying to delete own account."""
        with pytest.raises(ValueError, match="Cannot delete your own account"):
            operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="user-123")

    def test_delete_fails_when_repository_fails(self, mock_user_repository, mock_plugin_registry, mock_settings, sample_user):
        """Should raise ValueError when repository delete fails."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.delete.return_value = False

        with pytest.raises(ValueError, match="Failed to delete user"):
            operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="other-user")

    def test_delete_executes_before_delete_hook(self, mock_user_repository, mock_plugin_registry, mock_settings, sample_user):
        """Should execute before_delete hook."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.delete.return_value = True

        operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="other-user")

        calls = [call for call in mock_plugin_registry.execute_hook.call_args_list
                 if call[0][0] == USER_HOOKS.before_delete]
        assert len(calls) == 1

    def test_delete_executes_after_delete_hook(self, mock_user_repository, mock_plugin_registry, mock_settings, sample_user):
        """Should execute after_delete hook."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.delete.return_value = True

        operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="other-user")

        calls = [call for call in mock_plugin_registry.execute_hook.call_args_list
                 if call[0][0] == USER_HOOKS.after_delete]
        assert len(calls) == 1

    def test_delete_blocked_by_hook(self, mock_user_repository, mock_plugin_registry, mock_settings, sample_user):
        """Should raise ValueError when before_delete hook blocks operation."""
        mock_user_repository.get_by_id.return_value = sample_user

        mock_context = MagicMock()
        mock_context.data = {"blocked": True, "block_reason": "Cannot delete this user"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        with pytest.raises(ValueError, match="Cannot delete this user"):
            operations.delete(mock_user_repository, mock_plugin_registry, mock_settings, "user-123", requesting_user_id="other-user")


class TestExecuteHookHelper:
    """Tests for the shared execute_hook helper as used by operations.create/update/delete."""

    def test_execute_hook_returns_data_and_blocked_false(self, mock_plugin_registry):
        """Should return hook data and blocked=False when not blocked."""
        mock_context = MagicMock()
        mock_context.data = {"some": "data"}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(mock_plugin_registry, USER_HOOKS.before_create, {"test": "data"})

        assert data == {"some": "data"}
        assert blocked is False

    def test_execute_hook_returns_blocked_true(self, mock_plugin_registry):
        """Should return blocked=True when hook sets blocked flag."""
        mock_context = MagicMock()
        mock_context.data = {"blocked": True}
        mock_plugin_registry.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(mock_plugin_registry, USER_HOOKS.before_create, {"test": "data"})

        assert blocked is True


class TestAvatar:
    """Tests for operations.avatar: upload/delete/resolve."""

    def test_upload_avatar_success(self, mock_user_repository, mock_settings, sample_user, tmp_path):
        mock_user_repository.get_by_id.return_value = sample_user
        updated_user = User(
            id=sample_user.id, username=sample_user.username, email=sample_user.email,
            password_hash=sample_user.password_hash, account_type=sample_user.account_type,
            avatar_filename="placeholder.png"
        )
        mock_user_repository.update.return_value = updated_user

        result = operations.upload_avatar(
            mock_user_repository, mock_settings,
            user_id=sample_user.id,
            file_data=b"fake-image-bytes",
            filename="photo.PNG",
            content_type="image/png",
        )

        assert result == updated_user
        call = mock_user_repository.update.call_args
        assert call.args[0] == sample_user.id
        new_filename = call.kwargs["avatar_filename"]
        assert new_filename.endswith(".png")

        saved_path = tmp_path / "avatars" / new_filename
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"fake-image-bytes"

    def test_upload_avatar_rejects_non_image_content_type(self, mock_user_repository, mock_settings, sample_user):
        mock_user_repository.get_by_id.return_value = sample_user

        with pytest.raises(ValueError, match="image"):
            operations.upload_avatar(
                mock_user_repository, mock_settings,
                user_id=sample_user.id,
                file_data=b"not-an-image",
                filename="evil.exe",
                content_type="application/octet-stream",
            )

        mock_user_repository.update.assert_not_called()

    def test_upload_avatar_rejects_disallowed_extension(self, mock_user_repository, mock_settings, sample_user):
        mock_user_repository.get_by_id.return_value = sample_user

        with pytest.raises(ValueError, match="extension"):
            operations.upload_avatar(
                mock_user_repository, mock_settings,
                user_id=sample_user.id,
                file_data=b"fake-svg-bytes",
                filename="avatar.svg",
                content_type="image/svg+xml",
            )

        mock_user_repository.update.assert_not_called()

    def test_upload_avatar_rejects_oversize(self, mock_user_repository, mock_settings, sample_user):
        mock_user_repository.get_by_id.return_value = sample_user
        oversized = b"x" * (5 * 1024 * 1024 + 1)

        with pytest.raises(ValueError, match="5MB"):
            operations.upload_avatar(
                mock_user_repository, mock_settings,
                user_id=sample_user.id,
                file_data=oversized,
                filename="big.png",
                content_type="image/png",
            )

        mock_user_repository.update.assert_not_called()

    def test_upload_avatar_deletes_previous_file(self, mock_user_repository, mock_settings, sample_user, tmp_path):
        avatars_dir = tmp_path / "avatars"
        avatars_dir.mkdir()
        old_file = avatars_dir / "old-avatar.png"
        old_file.write_bytes(b"old")

        existing_user = User(
            id=sample_user.id, username=sample_user.username, email=sample_user.email,
            password_hash=sample_user.password_hash, account_type=sample_user.account_type,
            avatar_filename="old-avatar.png"
        )
        mock_user_repository.get_by_id.return_value = existing_user
        mock_user_repository.update.return_value = existing_user

        operations.upload_avatar(
            mock_user_repository, mock_settings,
            user_id=sample_user.id,
            file_data=b"new-bytes",
            filename="new.png",
            content_type="image/png",
        )

        assert not old_file.exists()

    def test_upload_avatar_user_not_found(self, mock_user_repository, mock_settings):
        mock_user_repository.get_by_id.return_value = None

        with pytest.raises(ValueError, match="User not found"):
            operations.upload_avatar(
                mock_user_repository, mock_settings,
                user_id="missing-user",
                file_data=b"bytes",
                filename="a.png",
                content_type="image/png",
            )

    def test_delete_avatar_clears_column_and_removes_file(self, mock_user_repository, mock_settings, sample_user, tmp_path):
        avatars_dir = tmp_path / "avatars"
        avatars_dir.mkdir()
        avatar_file = avatars_dir / "avatar123.png"
        avatar_file.write_bytes(b"data")

        existing_user = User(
            id=sample_user.id, username=sample_user.username, email=sample_user.email,
            password_hash=sample_user.password_hash, account_type=sample_user.account_type,
            avatar_filename="avatar123.png"
        )
        mock_user_repository.get_by_id.return_value = existing_user
        cleared_user = User(
            id=sample_user.id, username=sample_user.username, email=sample_user.email,
            password_hash=sample_user.password_hash, account_type=sample_user.account_type,
            avatar_filename=None
        )
        mock_user_repository.update.return_value = cleared_user

        result = operations.delete_avatar(mock_user_repository, mock_settings, sample_user.id)

        assert result.avatar_filename is None
        mock_user_repository.update.assert_called_once_with(sample_user.id, avatar_filename=None)
        assert not avatar_file.exists()

    def test_resolve_avatar_path_missing_raises(self, mock_settings):
        with pytest.raises(ValueError):
            operations.resolve_avatar_path(mock_settings, "does-not-exist.png")

    def test_resolve_avatar_path_traversal_raises(self, mock_settings, tmp_path):
        outside_file = tmp_path.parent / "secret.txt"
        outside_file.write_text("secret")

        with pytest.raises(ValueError):
            operations.resolve_avatar_path(mock_settings, "../secret.txt")

    def test_resolve_avatar_path_success(self, mock_settings, tmp_path):
        avatars_dir = tmp_path / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)
        avatar_file = avatars_dir / "avatar1.png"
        avatar_file.write_bytes(b"img")

        resolved = operations.resolve_avatar_path(mock_settings, "avatar1.png")

        assert resolved == avatar_file.resolve()
