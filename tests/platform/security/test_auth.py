"""Tests for the Auth class."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from src.platform.security.auth import Auth
from src.platform.security.config import AuthConfig
from src.platform.security.password import PasswordHasher
from src.platform.security.token import TokenCodec
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext, HookResult
from src.platform.security.user import User, AccountType
from src.features.users.repository import UserRepository
from src.platform.security.token import TokenData


class TestAuth:
    """Tests for Auth."""

    @pytest.fixture
    def mock_user_repository(self):
        """Create a mock UserRepository."""
        return Mock(spec=UserRepository)

    @pytest.fixture
    def mock_password_hasher(self):
        """Create a mock PasswordHasher."""
        hasher = Mock(spec=PasswordHasher)
        hasher.hash.return_value = "$2b$12$hashed_password"
        hasher.verify.return_value = True
        return hasher

    @pytest.fixture
    def mock_token_manager(self):
        """Create a mock TokenCodec."""
        manager = Mock(spec=TokenCodec)
        manager.create_access_token.return_value = "test.jwt.token"
        manager.decode_token.return_value = TokenData(username="testuser", user_id="test-123")
        return manager

    @pytest.fixture
    def mock_auth_config(self):
        """Create a mock AuthConfig."""
        config = Mock(spec=AuthConfig)
        config.secret_key = "test-secret"
        config.algorithm = "HS256"
        config.access_token_expire_minutes = 60
        return config

    @pytest.fixture
    def mock_plugin_registry(self):
        """Create a mock PluginRegistry."""
        registry = Mock(spec=PluginRegistry)
        # Default: no hooks block anything
        context = HookContext(hook_name="test", plugin_id="test", data={})
        registry.execute_hook.return_value = (context, [])
        return registry

    @pytest.fixture
    def mock_instance_claim(self):
        """Create a mock InstanceClaimStore. Default: instance is unclaimed."""
        from src.platform.security.claim_store import InstanceClaimStore
        claim = Mock(spec=InstanceClaimStore)
        claim.is_claimed.return_value = False
        claim.owner_user_id.return_value = None
        return claim

    @pytest.fixture
    def mock_claim_tokens(self):
        """Create a mock ClaimTokenStore. Default: no valid token."""
        from src.platform.security.claim_token import ClaimTokenStore
        tokens = Mock(spec=ClaimTokenStore)
        tokens.exists.return_value = False
        tokens.verify.return_value = False
        return tokens

    @pytest.fixture
    def mock_settings(self):
        """Settings manager whose registration_policy is closed by default."""
        settings = Mock()
        settings.get_setting.return_value = "closed"
        return settings

    @pytest.fixture
    def auth(
        self,
        mock_user_repository,
        mock_password_hasher,
        mock_token_manager,
        mock_auth_config,
        mock_plugin_registry,
        mock_instance_claim,
        mock_claim_tokens,
        mock_settings,
    ):
        """Create an Auth with mocks."""
        return Auth(
            user_repository=mock_user_repository,
            password_hasher=mock_password_hasher,
            token_codec=mock_token_manager,
            auth_config=mock_auth_config,
            plugin_registry=mock_plugin_registry,
            instance_claim=mock_instance_claim,
            claim_tokens=mock_claim_tokens,
            settings=mock_settings,
        )

    @pytest.fixture
    def sample_user(self):
        """Create a sample user."""
        return User(
            id="test-user-123",
            username="testuser",
            email="test@example.com",
            password_hash="$2b$12$hashed_password",
            account_type=AccountType.USER,
            created_at=datetime.utcnow(),
            last_login=None
        )

    # Registration tests

    def test_register_success_first_user_admin(self, auth, mock_user_repository, mock_instance_claim, mock_claim_tokens):
        """The first registration claims the instance and becomes admin."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = False

        admin_user = User(
            id="admin-123",
            username="newuser",
            email="newuser@example.com",
            password_hash="$2b$12$hashed",
            account_type=AccountType.ADMIN,
            created_at=datetime.utcnow()
        )
        mock_user_repository.create_claiming_instance.return_value = (admin_user, True)

        user, token = auth.register(
            username="newuser",
            email="newuser@example.com",
            password="password123"
        )

        assert user.account_type == AccountType.ADMIN
        mock_user_repository.create_claiming_instance.assert_called_once()
        # Winning the claim retires the one-time setup token.
        mock_claim_tokens.clear.assert_called_once()

    def test_register_success_subsequent_user(self, auth, mock_user_repository, mock_instance_claim, mock_settings, mock_claim_tokens):
        """A registration on an open, already-claimed instance becomes a regular user."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = True
        mock_settings.get_setting.return_value = "open"

        new_user = User(
            id="new-123",
            username="newuser",
            email="newuser@example.com",
            password_hash="$2b$12$hashed",
            account_type=AccountType.USER,
            created_at=datetime.utcnow()
        )
        mock_user_repository.create_claiming_instance.return_value = (new_user, False)

        user, token = auth.register(
            username="newuser",
            email="newuser@example.com",
            password="password123"
        )

        assert user.account_type == AccountType.USER
        mock_user_repository.create_claiming_instance.assert_called_once()
        # A non-owner registration must not touch the setup token.
        mock_claim_tokens.clear.assert_not_called()

    def test_register_closed_after_claim(self, auth, mock_user_repository, mock_instance_claim, mock_settings):
        """Once claimed, a closed registration_policy rejects new accounts."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = True
        mock_settings.get_setting.return_value = "closed"

        with pytest.raises(ValueError) as exc_info:
            auth.register("newuser", "new@example.com", "password123")

        assert "already has an owner" in str(exc_info.value)
        mock_user_repository.create_claiming_instance.assert_not_called()

    def test_register_reopened_after_claim(self, auth, mock_user_repository, mock_instance_claim, mock_settings):
        """An admin reopening (policy='open') lets new accounts register again."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = True
        mock_settings.get_setting.return_value = "open"

        new_user = User(
            id="new-123", username="newuser", email="newuser@example.com",
            password_hash="$2b$12$hashed", account_type=AccountType.USER,
            created_at=datetime.utcnow(),
        )
        mock_user_repository.create_claiming_instance.return_value = (new_user, False)

        user, _ = auth.register("newuser", "newuser@example.com", "password123")
        assert user.account_type == AccountType.USER

    def test_register_remote_unclaimed_requires_token(self, auth, mock_user_repository, mock_instance_claim, mock_claim_tokens):
        """Claiming an unclaimed instance from a non-loopback origin needs a valid token."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = False
        mock_claim_tokens.verify.return_value = False

        with pytest.raises(ValueError) as exc_info:
            auth.register(
                "newuser", "new@example.com", "password123",
                origin_is_loopback=False, claim_token="wrong",
            )

        assert "setup token" in str(exc_info.value)
        mock_user_repository.create_claiming_instance.assert_not_called()
        mock_claim_tokens.verify.assert_called_once_with("wrong")

    def test_register_remote_unclaimed_with_valid_token(self, auth, mock_user_repository, mock_instance_claim, mock_claim_tokens):
        """A valid setup token lets a remote origin create the owner account."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = False
        mock_claim_tokens.verify.return_value = True

        admin_user = User(
            id="admin-123", username="newuser", email="newuser@example.com",
            password_hash="$2b$12$hashed", account_type=AccountType.ADMIN,
            created_at=datetime.utcnow(),
        )
        mock_user_repository.create_claiming_instance.return_value = (admin_user, True)

        user, _ = auth.register(
            "newuser", "newuser@example.com", "password123",
            origin_is_loopback=False, claim_token="right",
        )
        assert user.account_type == AccountType.ADMIN

    def test_register_loopback_unclaimed_needs_no_token(self, auth, mock_user_repository, mock_instance_claim, mock_claim_tokens):
        """A loopback owner-claim is trusted and never checks a token."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = False

        admin_user = User(
            id="admin-123", username="newuser", email="newuser@example.com",
            password_hash="$2b$12$hashed", account_type=AccountType.ADMIN,
            created_at=datetime.utcnow(),
        )
        mock_user_repository.create_claiming_instance.return_value = (admin_user, True)

        user, _ = auth.register(
            "newuser", "newuser@example.com", "password123",
            origin_is_loopback=True,
        )
        assert user.account_type == AccountType.ADMIN
        mock_claim_tokens.verify.assert_not_called()

    def test_register_username_exists(self, auth, mock_user_repository):
        """Test registration fails when username exists."""
        mock_user_repository.exists_by_username.return_value = True

        with pytest.raises(ValueError) as exc_info:
            auth.register("existing", "new@example.com", "password")

        assert "Username already exists" in str(exc_info.value)

    def test_register_email_exists(self, auth, mock_user_repository):
        """Test registration fails when email exists."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = True

        with pytest.raises(ValueError) as exc_info:
            auth.register("newuser", "existing@example.com", "password")

        assert "Email already exists" in str(exc_info.value)

    def test_register_executes_hooks(self, auth, mock_plugin_registry, mock_user_repository, mock_instance_claim):
        """Test that registration executes before and after hooks."""
        mock_user_repository.exists_by_username.return_value = False
        mock_user_repository.exists_by_email.return_value = False
        mock_instance_claim.is_claimed.return_value = False

        new_user = User(
            id="new-123",
            username="newuser",
            email="newuser@example.com",
            password_hash="$2b$12$hashed",
            account_type=AccountType.ADMIN,
            created_at=datetime.utcnow()
        )
        mock_user_repository.create_claiming_instance.return_value = (new_user, True)

        auth.register("newuser", "newuser@example.com", "password")

        # Check that hooks were executed
        assert mock_plugin_registry.execute_hook.call_count == 2
        calls = [call[0][0] for call in mock_plugin_registry.execute_hook.call_args_list]
        assert "auth.before_register" in calls
        assert "auth.after_register" in calls

    def test_register_blocked_by_hook(self, auth, mock_plugin_registry):
        """Test that registration can be blocked by a hook."""
        blocked_context = HookContext(
            hook_name="auth.before_register",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Registration disabled"}
        )
        mock_plugin_registry.execute_hook.return_value = (blocked_context, [])

        with pytest.raises(ValueError) as exc_info:
            auth.register("newuser", "new@example.com", "password")

        assert "Registration disabled" in str(exc_info.value)

    # Authentication tests

    def test_authenticate_success(self, auth, mock_user_repository, mock_token_manager, sample_user):
        """Test successful authentication."""
        mock_user_repository.get_by_username.return_value = sample_user
        mock_user_repository.update_last_login.return_value = sample_user

        user, token = auth.authenticate("testuser", "password")

        assert user == sample_user
        assert token == "test.jwt.token"
        mock_user_repository.update_last_login.assert_called_once_with(sample_user.id)
        mock_token_manager.create_access_token.assert_called_once_with(
            data={"sub": sample_user.username, "user_id": sample_user.id},
            expires_delta=None
        )

    def test_authenticate_with_remember_me(self, auth, mock_user_repository, mock_token_manager, mock_auth_config, sample_user):
        """Test authenticate with remember_me=True passes expires_delta to create_access_token."""
        mock_user_repository.get_by_username.return_value = sample_user
        mock_user_repository.update_last_login.return_value = sample_user
        mock_auth_config.remember_me_token_expire_days = 30

        user, token = auth.authenticate("testuser", "password", remember_me=True)

        assert user == sample_user
        assert token == "test.jwt.token"
        mock_token_manager.create_access_token.assert_called_once_with(
            data={"sub": sample_user.username, "user_id": sample_user.id},
            expires_delta=timedelta(days=30)
        )

    def test_authenticate_without_remember_me_uses_default_expiry(self, auth, mock_user_repository, mock_token_manager, sample_user):
        """Test authenticate without remember_me passes expires_delta=None."""
        mock_user_repository.get_by_username.return_value = sample_user
        mock_user_repository.update_last_login.return_value = sample_user

        user, token = auth.authenticate("testuser", "password", remember_me=False)

        assert user == sample_user
        mock_token_manager.create_access_token.assert_called_once_with(
            data={"sub": sample_user.username, "user_id": sample_user.id},
            expires_delta=None
        )

    def test_authenticate_user_not_found(self, auth, mock_user_repository):
        """Test authentication fails when user not found."""
        mock_user_repository.get_by_username.return_value = None

        with pytest.raises(ValueError) as exc_info:
            auth.authenticate("nonexistent", "password")

        assert "Incorrect username or password" in str(exc_info.value)

    def test_authenticate_wrong_password(self, auth, mock_user_repository, mock_password_hasher, sample_user):
        """Test authentication fails with wrong password."""
        mock_user_repository.get_by_username.return_value = sample_user
        mock_password_hasher.verify.return_value = False

        with pytest.raises(ValueError) as exc_info:
            auth.authenticate("testuser", "wrongpassword")

        assert "Incorrect username or password" in str(exc_info.value)

    def test_authenticate_executes_hooks(self, auth, mock_plugin_registry, mock_user_repository, sample_user):
        """Test that authentication executes before and after hooks."""
        mock_user_repository.get_by_username.return_value = sample_user
        mock_user_repository.update_last_login.return_value = sample_user

        auth.authenticate("testuser", "password")

        # Check that hooks were executed
        assert mock_plugin_registry.execute_hook.call_count == 2
        calls = [call[0][0] for call in mock_plugin_registry.execute_hook.call_args_list]
        assert "auth.before_login" in calls
        assert "auth.after_login" in calls

    # Change password tests

    def test_change_password_success(self, auth, mock_user_repository, mock_password_hasher, sample_user):
        """Test changing a password verifies the current one and persists the new hash."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_user_repository.update_password.return_value = sample_user

        result = auth.change_password(
            user_id=sample_user.id,
            current_password="oldpassword",
            new_password="newpassword123",
        )

        assert result == sample_user
        mock_password_hasher.verify.assert_called_once_with("oldpassword", sample_user.password_hash)
        mock_password_hasher.hash.assert_called_once_with("newpassword123")
        mock_user_repository.update_password.assert_called_once_with(
            sample_user.id, "$2b$12$hashed_password"
        )

    def test_change_password_wrong_current_password(self, auth, mock_user_repository, mock_password_hasher, sample_user):
        """Test changing a password fails when the current password is wrong."""
        mock_user_repository.get_by_id.return_value = sample_user
        mock_password_hasher.verify.return_value = False

        with pytest.raises(ValueError) as exc_info:
            auth.change_password(
                user_id=sample_user.id,
                current_password="wrongpassword",
                new_password="newpassword123",
            )

        assert "incorrect" in str(exc_info.value).lower()
        mock_user_repository.update_password.assert_not_called()

    def test_change_password_user_not_found(self, auth, mock_user_repository):
        """Test changing a password fails when the user no longer exists."""
        mock_user_repository.get_by_id.return_value = None

        with pytest.raises(ValueError) as exc_info:
            auth.change_password(
                user_id="missing-user",
                current_password="oldpassword",
                new_password="newpassword123",
            )

        assert "User not found" in str(exc_info.value)

    def test_authenticate_blocked_by_hook(self, auth, mock_plugin_registry):
        """Test that authentication can be blocked by a hook."""
        blocked_context = HookContext(
            hook_name="auth.before_login",
            plugin_id="test",
            data={"blocked": True, "block_reason": "Too many attempts"}
        )
        mock_plugin_registry.execute_hook.return_value = (blocked_context, [])

        with pytest.raises(ValueError) as exc_info:
            auth.authenticate("testuser", "password")

        assert "Too many attempts" in str(exc_info.value)

    # Token validation tests

    def test_get_user_from_token_valid(self, auth, mock_user_repository, sample_user):
        """Test getting user from valid token."""
        mock_user_repository.get_by_id.return_value = sample_user

        result = auth.get_user_from_token("valid.jwt.token")

        assert result == sample_user

    def test_get_user_from_token_invalid(self, auth, mock_token_manager):
        """Test getting user from invalid token."""
        mock_token_manager.decode_token.return_value = None

        result = auth.get_user_from_token("invalid.token")

        assert result is None

    def test_get_user_from_token_user_not_found(self, auth, mock_user_repository):
        """Test getting user from token when user doesn't exist."""
        mock_user_repository.get_by_id.return_value = None

        result = auth.get_user_from_token("valid.token")

        assert result is None

    # WebSocket authentication tests

    def test_authenticate_websocket_no_token(self, auth):
        """Test WebSocket auth with no token."""
        user, error = auth.authenticate_websocket(None)

        assert user is None
        assert error == "Authentication required"

    def test_authenticate_websocket_invalid_token(self, auth, mock_token_manager):
        """Test WebSocket auth with invalid token."""
        mock_token_manager.decode_token.return_value = None

        user, error = auth.authenticate_websocket("invalid.token")

        assert user is None
        assert error == "Invalid token"

    def test_authenticate_websocket_user_not_found(self, auth, mock_user_repository):
        """Test WebSocket auth when user not found."""
        mock_user_repository.get_by_id.return_value = None

        user, error = auth.authenticate_websocket("valid.token")

        assert user is None
        assert error == "User not found"

    def test_authenticate_websocket_success(self, auth, mock_user_repository, sample_user):
        """Test successful WebSocket authentication."""
        mock_user_repository.get_by_id.return_value = sample_user

        user, error = auth.authenticate_websocket("valid.token")

        assert user == sample_user
        assert error is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
