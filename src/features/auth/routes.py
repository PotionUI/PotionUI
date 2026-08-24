"""
Authentication controller for user registration, login, and token management.
"""
from collections import defaultdict, deque
import os
import time
from typing import TYPE_CHECKING, Deque, Dict, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.origin import is_loopback_host
from src.features.auth.dto import ChangePasswordRequest, UserCreate, Token, UserResponse, UserMeResponse
from src.platform.security.current_user import get_current_user
from src.platform.security import AuthManager
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class LoginAttemptLimiter:
    """Small per-process guard against brute-force login attempts."""

    def __init__(self) -> None:
        self.max_attempts = int(os.environ.get("POTIONUI_AUTH_LOGIN_ATTEMPTS", "5"))
        self.window_seconds = int(os.environ.get("POTIONUI_AUTH_LOGIN_WINDOW_SECONDS", "60"))
        self._attempts: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> Deque[float]:
        attempts = self._attempts[key]
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def check(self, key: str) -> None:
        if len(self._prune(key, time.monotonic())) >= self.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please try again later.",
                headers={"Retry-After": str(self.window_seconds)},
            )

    def record_failure(self, key: str) -> None:
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


class AuthController(BaseController):
    """
    Controller for authentication operations.

    Handles user registration, login, and current user information retrieval.
    Uses AuthManager for all authentication logic.
    """

    def __init__(self, auth_manager: AuthManager):
        super().__init__()
        self.auth = auth_manager
        self.login_limiter = LoginAttemptLimiter()
        # Same brute-force guard as login, keyed per user id instead of
        # per (ip, username): a change-password attempt is already authenticated.
        self.change_password_limiter = LoginAttemptLimiter()

    async def register(self, user_data: UserCreate, request: Request) -> APIResponse:
        """
        Register a new user.

        Args:
            user_data: User registration data
            request: FastAPI request object for IP/user-agent extraction

        Returns:
            APIResponse with user data and access token
        """
        try:
            # Extract request metadata for hooks
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")

            user, token = self.auth.register(
                username=user_data.username,
                email=user_data.email,
                password=user_data.password,
                ip_address=ip_address,
                user_agent=user_agent,
                origin_is_loopback=is_loopback_host(ip_address),
                claim_token=user_data.claim_token,
            )

            return self.success_response(
                data={
                    "user": UserResponse(**user.to_dict()).model_dump(),
                    "access_token": token,
                    "token_type": "bearer"
                },
                message="User registered successfully"
            )
        except ValueError as e:
            self.error_response(
                error="registration_failed",
                message=str(e),
                status_code=400
            )

    async def login(self, form_data: OAuth2PasswordRequestForm, request: Request, remember_me: bool = False) -> Token:
        """
        Login with username and password.

        Args:
            form_data: OAuth2 password request form
            request: FastAPI request object for IP/user-agent extraction

        Returns:
            Token with access_token

        Raises:
            HTTPException: If credentials are invalid
        """
        try:
            # Extract request metadata for hooks
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")

            limiter_key = f"{ip_address or 'unknown'}:{form_data.username.casefold()}"
            self.login_limiter.check(limiter_key)

            user, token = self.auth.authenticate(
                username=form_data.username,
                password=form_data.password,
                ip_address=ip_address,
                user_agent=user_agent,
                remember_me=remember_me
            )

            self.login_limiter.reset(limiter_key)

            return Token(access_token=token)
        except ValueError:
            self.login_limiter.record_failure(limiter_key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def get_me(self, current_user: User) -> APIResponse:
        """
        Get current user information.

        Args:
            current_user: Currently authenticated user

        Returns:
            APIResponse with user information
        """
        return self.success_response(
            data=UserMeResponse(**current_user.to_dict()).model_dump()
        )

    async def change_password(
        self, request: ChangePasswordRequest, current_user: User
    ) -> APIResponse:
        """
        Change the current user's own password.

        Args:
            request: Current + new password
            current_user: Currently authenticated user

        Returns:
            APIResponse confirming the change

        Raises:
            HTTPException: 429 if rate-limited, 400 on wrong current password
        """
        limiter_key = current_user.id

        self.change_password_limiter.check(limiter_key)

        try:
            self.auth.change_password(
                user_id=current_user.id,
                current_password=request.current_password,
                new_password=request.new_password,
            )
        except ValueError as e:
            self.change_password_limiter.record_failure(limiter_key)
            self.error_response(
                error="incorrect_password",
                message=str(e),
                status_code=400
            )
        else:
            self.change_password_limiter.reset(limiter_key)
            return self.success_response(message="Password changed successfully")


def build_router(container: "AppContainer") -> APIRouter:
    controller = AuthController(container.auth_manager)
    router = APIRouter(prefix="/api/auth", tags=["authentication"])

    @router.post("/register", response_model=APIResponse, summary="Register a new user account")
    async def register(user_data: UserCreate, request: Request) -> APIResponse:
        """Register a new user."""
        return await controller.register(user_data, request)

    @router.post("/login", response_model=Token, summary="Log in and issue an access token")
    async def login(
        request: Request,
        form_data: OAuth2PasswordRequestForm = Depends(),
        remember_me: bool = Form(False)
    ) -> Token:
        """Login with username and password."""
        return await controller.login(form_data, request, remember_me)

    @router.get("/me", response_model=APIResponse, summary="Get the current authenticated user")
    async def get_current_user_info(
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Get current user information."""
        return await controller.get_me(current_user)

    @router.post("/change-password", response_model=APIResponse, summary="Change your own password")
    async def change_password(
        request: ChangePasswordRequest,
        current_user: User = Depends(get_current_user)
    ) -> APIResponse:
        """Change the current user's own password."""
        return await controller.change_password(request, current_user)

    return router
