"""Tests for the FastAPI current-user auth dependencies."""
import asyncio

import pytest
from fastapi import HTTPException
from unittest.mock import Mock

from src.platform.security import current_user
from src.platform.security.user import User, AccountType


@pytest.fixture
def mock_auth_manager():
    manager = Mock()
    previous = current_user._auth
    current_user.set_auth(manager)
    yield manager
    current_user._auth = previous


@pytest.fixture
def a_user():
    return User(
        id="user-1",
        username="alice",
        email="alice@example.com",
        password_hash="hash",
        account_type=AccountType.USER,
    )


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_user_from_token(self, mock_auth_manager, a_user):
        mock_auth_manager.get_user_from_token.return_value = a_user

        user = await current_user.get_current_user(token="a.jwt.token")

        assert user is a_user
        mock_auth_manager.get_user_from_token.assert_called_once_with("a.jwt.token")

    @pytest.mark.asyncio
    async def test_raises_401_when_token_invalid(self, mock_auth_manager):
        mock_auth_manager.get_user_from_token.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await current_user.get_current_user(token="bad.token")

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_token_lookup_runs_off_the_event_loop(self, mock_auth_manager, a_user, monkeypatch):
        """The blocking DB read behind get_user_from_token must go through
        asyncio.to_thread - this dependency runs on every authenticated
        request, so an inline call would block the single event loop."""
        mock_auth_manager.get_user_from_token.return_value = a_user
        recorded = []
        real_to_thread = asyncio.to_thread

        async def recording_to_thread(func, *args, **kwargs):
            recorded.append(func)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(
            'src.platform.security.current_user.asyncio.to_thread',
            recording_to_thread,
        )

        user = await current_user.get_current_user(token="a.jwt.token")

        assert user is a_user
        assert mock_auth_manager.get_user_from_token in recorded


class TestMediaViewer:
    @staticmethod
    def _request(headers=None, cookies=None):
        from starlette.requests import Request

        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if cookies:
            raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
        return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})

    @pytest.fixture
    def no_fallback(self):
        previous = current_user._media_bearer_fallback
        current_user.set_media_bearer_fallback(None)
        yield
        current_user._media_bearer_fallback = previous

    @pytest.mark.asyncio
    async def test_the_bearer_header_wins_over_the_cookie(self, mock_auth_manager, a_user, no_fallback):
        other = User(id="user-2", username="bob", email="b@example.com", password_hash="h", account_type=AccountType.USER)
        mock_auth_manager.get_user_from_token.side_effect = {"header.jwt": a_user, "cookie.jwt": other}.get

        viewer = await current_user.get_media_viewer(
            self._request({"Authorization": "Bearer header.jwt"}, {"potionui_media": "cookie.jwt"})
        )

        assert viewer is a_user

    @pytest.mark.asyncio
    async def test_the_cookie_alone_identifies_an_img_tag_request(self, mock_auth_manager, a_user, no_fallback):
        mock_auth_manager.get_user_from_token.side_effect = {"cookie.jwt": a_user}.get

        viewer = await current_user.get_media_viewer(self._request(cookies={"potionui_media": "cookie.jwt"}))

        assert viewer is a_user

    @pytest.mark.asyncio
    async def test_a_non_jwt_bearer_falls_back_to_the_registered_resolver(self, mock_auth_manager, a_user, no_fallback):
        mock_auth_manager.get_user_from_token.return_value = None
        current_user.set_media_bearer_fallback({"pui_mcp_x": a_user}.get)

        viewer = await current_user.get_media_viewer(self._request({"Authorization": "Bearer pui_mcp_x"}))

        assert viewer is a_user

    @pytest.mark.asyncio
    async def test_no_credentials_is_a_401(self, mock_auth_manager, no_fallback):
        viewer = await current_user.get_media_viewer(self._request())
        assert viewer is None

        with pytest.raises(HTTPException) as exc_info:
            await current_user.require_media_viewer(viewer)
        assert exc_info.value.status_code == 401
        mock_auth_manager.get_user_from_token.assert_not_called()
