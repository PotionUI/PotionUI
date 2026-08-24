"""
Authentication module for PotionUI.

This module provides authentication functionality including:
- The caller identity (`User`, `AccountType`) that authentication resolves to
- Password hashing (bcrypt)
- JWT token management
- Authentication configuration
- Auth operations coordination
"""
from src.platform.security.config import AuthConfig
from src.platform.security.claim_store import InstanceClaimStore
from src.platform.security.claim_token import ClaimTokenManager
from src.platform.security.password import PasswordHasher
from src.platform.security.token import TokenData, TokenManager
from src.platform.security.user import AccountType, User
from src.platform.security.manager import AuthManager

__all__ = [
    "AccountType",
    "AuthConfig",
    "AuthManager",
    "ClaimTokenManager",
    "InstanceClaimStore",
    "PasswordHasher",
    "TokenData",
    "TokenManager",
    "User",
]
