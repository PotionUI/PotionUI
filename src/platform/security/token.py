"""
JWT token management utilities.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel

from src.platform.security.config import AuthConfig


class TokenData(BaseModel):
    """The claims a validated access token resolves to."""
    username: Optional[str] = None
    user_id: Optional[str] = None


class TokenCodec:
    """
    Handles JWT token creation and validation.
    """

    def __init__(self, config: AuthConfig):
        self._config = config

    def create_access_token(
        self,
        data: dict,
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """
        Create a new JWT access token.

        Args:
            data: Dictionary of claims to include in the token
            expires_delta: Optional custom expiration time

        Returns:
            Encoded JWT token string
        """
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(
                minutes=self._config.access_token_expire_minutes
            )

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(
            to_encode,
            self._config.secret_key,
            algorithm=self._config.algorithm
        )
        return encoded_jwt

    def decode_token(self, token: str) -> Optional[TokenData]:
        """
        Decode and validate a JWT token.

        Args:
            token: JWT token string

        Returns:
            TokenData if valid, None if invalid
        """
        try:
            payload = jwt.decode(
                token,
                self._config.secret_key,
                algorithms=[self._config.algorithm]
            )
            username: str = payload.get("sub")
            user_id: str = payload.get("user_id")

            if username is None or user_id is None:
                logging.warning(f"Token missing required fields: sub={bool(username)}, user_id={bool(user_id)}")
                return None

            return TokenData(username=username, user_id=user_id)
        except JWTError as e:
            logging.warning(f"JWT decode failed: {e}")
            return None
