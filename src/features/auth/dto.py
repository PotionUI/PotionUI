"""
Authentication Data Transfer Objects (DTOs) for API requests and responses.
"""
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator

from src.features.auth.validators import validate_password_policy, validate_username_policy


class UserCreate(BaseModel):
    """Request model for user registration."""
    username: str
    email: EmailStr
    password: str
    # One-time setup token, only needed to claim an unclaimed instance from a
    # non-loopback origin (see SetupStatus.claim_requires_token).
    claim_token: Optional[str] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return validate_username_policy(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


class ChangePasswordRequest(BaseModel):
    """Request model for a self-service password change."""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_policy(value)


class UserLogin(BaseModel):
    """Request model for user login."""
    username: str
    password: str


class Token(BaseModel):
    """Response model for JWT token."""
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Response model for user data (public information)."""
    id: str
    username: str
    email: str
    account_type: str
    created_at: Optional[str] = None
    last_login: Optional[str] = None


class UserMeResponse(BaseModel):
    """Response model for current user information."""
    id: str
    username: str
    email: str
    account_type: str
    created_at: Optional[str] = None
    last_login: Optional[str] = None
    avatar_url: Optional[str] = None
