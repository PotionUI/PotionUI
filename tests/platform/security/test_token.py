"""Tests for the TokenCodec class."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from jose import jwt

from src.platform.security.token import TokenCodec
from src.platform.security.config import AuthConfig
from src.platform.security.token import TokenData


class TestTokenCodec:
    """Tests for TokenCodec."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock AuthConfig."""
        config = Mock(spec=AuthConfig)
        config.secret_key = "test-secret-key-for-testing"
        config.algorithm = "HS256"
        config.access_token_expire_minutes = 60
        return config

    @pytest.fixture
    def token_codec(self, mock_config):
        """Create a TokenCodec with mock config."""
        return TokenCodec(mock_config)

    def test_create_access_token_default_expiry(self, token_codec, mock_config):
        """Test token creation with default expiry."""
        data = {"sub": "testuser", "user_id": "test-123"}

        token = token_codec.create_access_token(data)

        # Decode and verify
        payload = jwt.decode(
            token,
            mock_config.secret_key,
            algorithms=[mock_config.algorithm]
        )

        assert payload["sub"] == "testuser"
        assert payload["user_id"] == "test-123"
        assert "exp" in payload

        # Check expiry is roughly correct
        expected_exp = datetime.utcnow() + timedelta(minutes=60)
        actual_exp = datetime.utcfromtimestamp(payload["exp"])
        assert abs((actual_exp - expected_exp).total_seconds()) < 60

    def test_create_access_token_custom_expiry(self, token_codec, mock_config):
        """Test token creation with custom expiry."""
        data = {"sub": "testuser", "user_id": "test-123"}
        custom_delta = timedelta(hours=2)

        token = token_codec.create_access_token(data, custom_delta)

        # Decode and verify
        payload = jwt.decode(
            token,
            mock_config.secret_key,
            algorithms=[mock_config.algorithm]
        )

        # Check custom expiry
        expected_exp = datetime.utcnow() + custom_delta
        actual_exp = datetime.utcfromtimestamp(payload["exp"])
        assert abs((actual_exp - expected_exp).total_seconds()) < 60

    def test_decode_token_valid(self, token_codec):
        """Test decoding a valid token."""
        data = {"sub": "testuser", "user_id": "test-123"}
        token = token_codec.create_access_token(data)

        result = token_codec.decode_token(token)

        assert result is not None
        assert isinstance(result, TokenData)
        assert result.username == "testuser"
        assert result.user_id == "test-123"

    def test_decode_token_invalid(self, token_codec):
        """Test decoding an invalid token."""
        result = token_codec.decode_token("invalid.token.here")

        assert result is None

    def test_decode_token_expired(self, token_codec, mock_config):
        """Test decoding an expired token."""
        data = {"sub": "testuser", "user_id": "test-123"}
        expired_delta = timedelta(seconds=-1)  # Already expired

        token = token_codec.create_access_token(data, expired_delta)
        result = token_codec.decode_token(token)

        assert result is None

    def test_decode_token_missing_username(self, token_codec, mock_config):
        """Test decoding token with missing username claim."""
        # Create token without 'sub' claim
        to_encode = {"user_id": "test-123", "exp": datetime.utcnow() + timedelta(hours=1)}
        token = jwt.encode(to_encode, mock_config.secret_key, algorithm=mock_config.algorithm)

        result = token_codec.decode_token(token)

        assert result is None

    def test_decode_token_missing_user_id(self, token_codec, mock_config):
        """Test decoding token with missing user_id claim."""
        # Create token without 'user_id' claim
        to_encode = {"sub": "testuser", "exp": datetime.utcnow() + timedelta(hours=1)}
        token = jwt.encode(to_encode, mock_config.secret_key, algorithm=mock_config.algorithm)

        result = token_codec.decode_token(token)

        assert result is None

    def test_token_preserves_additional_claims(self, token_codec, mock_config):
        """Test that additional claims are preserved in the token."""
        data = {
            "sub": "testuser",
            "user_id": "test-123",
            "custom_claim": "custom_value",
            "role": "admin"
        }
        token = token_codec.create_access_token(data)

        payload = jwt.decode(
            token,
            mock_config.secret_key,
            algorithms=[mock_config.algorithm]
        )

        assert payload["custom_claim"] == "custom_value"
        assert payload["role"] == "admin"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
