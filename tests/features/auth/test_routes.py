"""Tests for the refactored AuthController."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from typing import Dict, Any

from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt

from src.features.auth.routes import AuthController
from src.platform.security.current_user import (
    get_current_user,
    get_current_active_user,
    authenticate_websocket_token,
    set_auth,
)
from src.platform.http.base_controller import APIResponse
from src.features.auth.dto import ChangePasswordRequest, UserCreate, Token, UserResponse
from src.platform.security.token import TokenData
from src.platform.security import Auth, AuthConfig, PasswordHasher, TokenCodec
from src.platform.security.user import User, AccountType


class TestAuthController:
    """Tests for the refactored AuthController."""

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock Auth."""
        manager = Mock(spec=Auth)
        manager.passwords = Mock(spec=PasswordHasher)
        manager.tokens = Mock(spec=TokenCodec)
        manager.config = Mock(spec=AuthConfig)

        # Configure default config values
        manager.config.secret_key = "test-secret-key"
        manager.config.algorithm = "HS256"
        manager.config.access_token_expire_minutes = 60

        return manager

    @pytest.fixture
    def auth_controller(self, mock_auth_manager):
        """Create an AuthController with mock manager."""
        controller = AuthController(mock_auth_manager)
        # Also set auth for dependencies
        set_auth(mock_auth_manager)
        return controller

    @pytest.fixture
    def sample_user(self):
        """Create a sample user."""
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
        """Create a sample admin user."""
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
    def sample_user_create(self):
        """Create sample user creation data."""
        return UserCreate(
            username="newuser",
            email="newuser@example.com",
            password="password123"
        )

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = Mock()
        request.client = Mock()
        request.client.host = "127.0.0.1"
        request.headers = {"user-agent": "Test Agent"}
        return request

    @pytest.fixture
    def sample_oauth_form(self):
        """Create sample OAuth form data."""
        form = Mock(spec=OAuth2PasswordRequestForm)
        form.username = "testuser"
        form.password = "password123"
        return form

    # Controller initialization tests

    def test_controller_initialization(self, auth_controller, mock_auth_manager):
        """Test controller initializes with Auth."""
        assert auth_controller.auth == mock_auth_manager
        assert hasattr(auth_controller, 'logger')

    # Registration tests

    @pytest.mark.asyncio
    async def test_register_success(self, auth_controller, mock_auth_manager, sample_user, mock_request, sample_user_create):
        """Test successful user registration."""
        mock_auth_manager.register.return_value = (sample_user, "test.jwt.token")

        result = await auth_controller.register(sample_user_create, mock_request)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert "access_token" in result.data
        assert result.data["token_type"] == "bearer"
        assert result.message == "User registered successfully"

        mock_auth_manager.register.assert_called_once_with(
            username=sample_user_create.username,
            email=sample_user_create.email,
            password=sample_user_create.password,
            ip_address="127.0.0.1",
            user_agent="Test Agent",
            origin_is_loopback=True,
            claim_token=None,
        )

    @pytest.mark.asyncio
    async def test_register_first_user_admin(self, auth_controller, mock_auth_manager, admin_user, mock_request, sample_user_create):
        """Test first registered user becomes admin."""
        mock_auth_manager.register.return_value = (admin_user, "test.jwt.token")

        result = await auth_controller.register(sample_user_create, mock_request)

        assert result.success is True
        assert result.data["user"]["account_type"] == "ADMIN"

    @pytest.mark.asyncio
    async def test_register_username_exists(self, auth_controller, mock_auth_manager, mock_request, sample_user_create):
        """Test registration fails when username exists."""
        mock_auth_manager.register.side_effect = ValueError("Username already exists")

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.register(sample_user_create, mock_request)

        assert exc_info.value.status_code == 400
        assert "Username already exists" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_register_email_exists(self, auth_controller, mock_auth_manager, mock_request, sample_user_create):
        """Test registration fails when email exists."""
        mock_auth_manager.register.side_effect = ValueError("Email already exists")

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.register(sample_user_create, mock_request)

        assert exc_info.value.status_code == 400
        assert "Email already exists" in str(exc_info.value.detail)

    # Login tests

    @pytest.mark.asyncio
    async def test_login_success(self, auth_controller, mock_auth_manager, sample_user, mock_request, sample_oauth_form):
        """Test successful login."""
        mock_auth_manager.authenticate.return_value = (sample_user, "test.jwt.token")

        result = await auth_controller.login(sample_oauth_form, mock_request)

        assert isinstance(result, Token)
        assert result.access_token == "test.jwt.token"
        assert result.token_type == "bearer"

        mock_auth_manager.authenticate.assert_called_once_with(
            username=sample_oauth_form.username,
            password=sample_oauth_form.password,
            ip_address="127.0.0.1",
            user_agent="Test Agent",
            remember_me=False
        )

    @pytest.mark.asyncio
    async def test_login_with_remember_me(self, auth_controller, mock_auth_manager, sample_user, mock_request, sample_oauth_form):
        """Test login passes remember_me=True through to authenticate."""
        mock_auth_manager.authenticate.return_value = (sample_user, "test.jwt.token")

        result = await auth_controller.login(sample_oauth_form, mock_request, remember_me=True)

        assert isinstance(result, Token)
        assert result.access_token == "test.jwt.token"

        mock_auth_manager.authenticate.assert_called_once_with(
            username=sample_oauth_form.username,
            password=sample_oauth_form.password,
            ip_address="127.0.0.1",
            user_agent="Test Agent",
            remember_me=True
        )

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, auth_controller, mock_auth_manager, mock_request, sample_oauth_form):
        """Test login fails with invalid credentials."""
        mock_auth_manager.authenticate.side_effect = ValueError("Incorrect username or password")

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.login(sample_oauth_form, mock_request)

        assert exc_info.value.status_code == 401
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Incorrect username or password" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_login_rate_limits_repeated_failures(self, auth_controller, mock_auth_manager, mock_request, sample_oauth_form):
        mock_auth_manager.authenticate.side_effect = ValueError("Incorrect username or password")

        for _ in range(auth_controller.login_limiter.max_attempts):
            with pytest.raises(HTTPException) as exc_info:
                await auth_controller.login(sample_oauth_form, mock_request)
            assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.login(sample_oauth_form, mock_request)

        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers

    # Change password tests

    @pytest.mark.asyncio
    async def test_change_password_success(self, auth_controller, mock_auth_manager, sample_user):
        """Test changing own password succeeds and resets the limiter."""
        mock_auth_manager.change_password.return_value = sample_user
        request = ChangePasswordRequest(current_password="oldpassword1", new_password="newpassword1")

        result = await auth_controller.change_password(request, sample_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        mock_auth_manager.change_password.assert_called_once_with(
            user_id=sample_user.id,
            current_password="oldpassword1",
            new_password="newpassword1",
        )

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, auth_controller, mock_auth_manager, sample_user):
        """Test changing password fails with 400 when current password is wrong."""
        mock_auth_manager.change_password.side_effect = ValueError("Current password is incorrect")
        request = ChangePasswordRequest(current_password="wrongpassword", new_password="newpassword1")

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.change_password(request, sample_user)

        assert exc_info.value.status_code == 400

    def test_change_password_rejects_weak_new_password(self):
        """Test the new_password field reuses the register password policy."""
        with pytest.raises(ValueError):
            ChangePasswordRequest(current_password="oldpassword1", new_password="short")

    @pytest.mark.asyncio
    async def test_change_password_rate_limits_repeated_failures(self, auth_controller, mock_auth_manager, sample_user):
        """Test repeated wrong-current-password attempts get rate limited."""
        mock_auth_manager.change_password.side_effect = ValueError("Current password is incorrect")
        request = ChangePasswordRequest(current_password="wrongpassword", new_password="newpassword1")

        for _ in range(auth_controller.change_password_limiter.max_attempts):
            with pytest.raises(HTTPException) as exc_info:
                await auth_controller.change_password(request, sample_user)
            assert exc_info.value.status_code == 400

        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.change_password(request, sample_user)

        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers

    # Get me tests

    @pytest.mark.asyncio
    async def test_get_me_success(self, auth_controller, sample_user):
        """Test getting current user info."""
        result = await auth_controller.get_me(sample_user)

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data["id"] == sample_user.id
        assert result.data["username"] == sample_user.username
        assert result.data["email"] == sample_user.email

    # get_current_user dependency tests

    @pytest.mark.asyncio
    async def test_get_current_user_valid_token(self, auth_controller, mock_auth_manager, sample_user):
        """Test get_current_user with valid token."""
        mock_auth_manager.get_user_from_token.return_value = sample_user

        result = await get_current_user("valid.jwt.token")

        assert result == sample_user
        mock_auth_manager.get_user_from_token.assert_called_once_with("valid.jwt.token")

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, auth_controller, mock_auth_manager):
        """Test get_current_user with invalid token."""
        mock_auth_manager.get_user_from_token.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user("invalid.token")

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Could not validate credentials" in exc_info.value.detail

    # get_current_active_user tests

    @pytest.mark.asyncio
    async def test_get_current_active_user(self, sample_user):
        """Test get_current_active_user passes through user."""
        result = await get_current_active_user(sample_user)
        assert result == sample_user

    # WebSocket authentication tests

    def test_authenticate_websocket_token_valid(self, auth_controller, mock_auth_manager, sample_user):
        """Test WebSocket auth with valid token."""
        mock_auth_manager.authenticate_websocket.return_value = (sample_user, None)

        user, error = authenticate_websocket_token("valid.token")

        assert user == sample_user
        assert error is None

    def test_authenticate_websocket_token_no_token(self, auth_controller, mock_auth_manager):
        """Test WebSocket auth with no token."""
        mock_auth_manager.authenticate_websocket.return_value = (None, "Authentication required")

        user, error = authenticate_websocket_token(None)

        assert user is None
        assert error == "Authentication required"

    def test_authenticate_websocket_token_invalid(self, auth_controller, mock_auth_manager):
        """Test WebSocket auth with invalid token."""
        mock_auth_manager.authenticate_websocket.return_value = (None, "Invalid token")

        user, error = authenticate_websocket_token("invalid.token")

        assert user is None
        assert error == "Invalid token"

    # DTO model tests

    def test_user_create_model(self):
        """Test UserCreate model validation."""
        user_create = UserCreate(
            username="testuser",
            email="test@example.com",
            password="password123"
        )

        assert user_create.username == "testuser"
        assert user_create.email == "test@example.com"
        assert user_create.password == "password123"

    @pytest.mark.parametrize("password", ["short", "x" * 73])
    def test_user_create_rejects_invalid_password_length(self, password):
        with pytest.raises(ValueError):
            UserCreate(username="testuser", email="test@example.com", password=password)

    @pytest.mark.parametrize("username", ["ab", "has spaces", "bad/name"])
    def test_user_create_rejects_invalid_username(self, username):
        with pytest.raises(ValueError):
            UserCreate(username=username, email="test@example.com", password="password123")

    def test_token_model(self):
        """Test Token model."""
        token = Token(access_token="test.token.here")

        assert token.access_token == "test.token.here"
        assert token.token_type == "bearer"

    def test_token_data_model(self):
        """Test TokenData model."""
        token_data = TokenData(username="testuser", user_id="test-123")

        assert token_data.username == "testuser"
        assert token_data.user_id == "test-123"

    def test_user_response_model(self, sample_user):
        """Test UserResponse model serialization."""
        user_response = UserResponse(**sample_user.to_dict())

        assert user_response.id == sample_user.id
        assert user_response.username == sample_user.username
        assert user_response.email == sample_user.email
        assert user_response.account_type == sample_user.account_type.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
