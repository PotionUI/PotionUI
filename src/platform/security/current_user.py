"""
FastAPI authentication dependencies.

This module provides FastAPI dependencies for authentication:
- oauth2_scheme: OAuth2 password bearer scheme
- get_current_user: Get authenticated user from JWT token
- get_current_active_user: Ensure user is active
- authenticate_websocket_token: WebSocket authentication helper
"""
import asyncio
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from src.platform.security import AuthManager
from src.platform.security.user import User, AccountType

# OAuth2 scheme - tokenUrl should match the login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Global reference to auth manager (set during app startup)
_auth_manager: Optional[AuthManager] = None


def set_auth_manager(manager: AuthManager) -> None:
    """
    Set the global auth manager instance.

    This should be called during application startup to provide
    the auth manager for dependency injection.

    Args:
        manager: AuthManager instance from DI container
    """
    global _auth_manager
    _auth_manager = manager


def get_auth_manager() -> AuthManager:
    """
    Get the global auth manager instance.

    Returns:
        AuthManager instance

    Raises:
        RuntimeError: If auth manager not initialized
    """
    if _auth_manager is None:
        raise RuntimeError("AuthManager not initialized. Call set_auth_manager() during app startup.")
    return _auth_manager


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    Get current user from JWT token.

    This is a FastAPI dependency that extracts and validates the JWT token
    from the Authorization header and returns the corresponding user.

    Args:
        token: JWT token from Authorization header (injected by FastAPI)

    Returns:
        User object for the authenticated user

    Raises:
        HTTPException: 401 if token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    auth_manager = get_auth_manager()
    # get_user_from_token does a blocking DB read; every authenticated request
    # goes through this dependency, so running it inline would put a sync
    # sqlite round-trip on the single event loop ahead of the handler.
    user = await asyncio.to_thread(auth_manager.get_user_from_token, token)

    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensure user is active.

    This dependency wraps get_current_user and can be extended
    to add additional checks (e.g., is_active, is_verified flags).

    Args:
        current_user: Currently authenticated user (injected by FastAPI)

    Returns:
        User object if active
    """
    # For now, all authenticated users are considered active.
    # Add additional checks here if needed (e.g., current_user.is_active)
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Ensure the authenticated user is an administrator.

    Use this dependency to gate endpoints that manage global, cross-user
    state (LLM provider configs, user assignments, backend management, etc.).

    Args:
        current_user: Currently authenticated active user (injected by FastAPI)

    Returns:
        User object if the user is an administrator

    Raises:
        HTTPException: 403 if the user is not an administrator
    """
    if current_user.account_type != AccountType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return current_user


def authenticate_websocket_token(token: Optional[str] = None) -> Tuple[Optional[User], Optional[str]]:
    """
    Authenticate WebSocket token without accepting/closing connection.

    Unlike HTTP endpoints, WebSocket authentication doesn't use FastAPI's
    dependency injection. This helper function validates tokens for
    WebSocket connections.

    Args:
        token: JWT token, can be None

    Returns:
        Tuple of (user, error_message)
        - On success: (user, None)
        - On failure: (None, error_message)
    """
    import logging
    try:
        auth_manager = get_auth_manager()
        return auth_manager.authenticate_websocket(token)
    except RuntimeError as e:
        logging.error(f"WebSocket auth initialization error: {e}")
        return (None, "Authentication service not available")
