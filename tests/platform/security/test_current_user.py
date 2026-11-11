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


def _plain_request(headers=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


async def _current_user(token):
    principal = await current_user.get_principal(_plain_request(), token=token)
    return await current_user.get_current_user(principal)


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_user_from_token(self, mock_auth_manager, a_user):
        mock_auth_manager.get_user_from_token.return_value = a_user

        user = await _current_user("a.jwt.token")

        assert user is a_user
        mock_auth_manager.get_user_from_token.assert_called_once_with("a.jwt.token")

    @pytest.mark.asyncio
    async def test_raises_401_when_token_invalid(self, mock_auth_manager):
        mock_auth_manager.get_user_from_token.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await _current_user("bad.token")

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

        user = await _current_user("a.jwt.token")

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
        previous = current_user._key_resolver
        current_user.set_key_resolver(None)
        yield
        current_user.set_key_resolver(previous)

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
    async def test_the_partitioned_cookie_alone_identifies_an_iframe_img_request(self, mock_auth_manager, a_user, no_fallback):
        from src.platform.security.media_session import partitioned_media_cookie_name

        mock_auth_manager.get_user_from_token.side_effect = {"frame.jwt": a_user}.get

        viewer = await current_user.get_media_viewer(
            self._request(cookies={partitioned_media_cookie_name(): "frame.jwt"})
        )

        assert viewer is a_user

    @pytest.mark.asyncio
    async def test_a_key_shaped_bearer_is_only_a_jwt_while_no_resolver_is_registered(self, mock_auth_manager, no_fallback):
        mock_auth_manager.get_user_from_token.return_value = None

        viewer = await current_user.get_media_viewer(self._request({"Authorization": "Bearer pui_x"}))

        assert viewer is None
        mock_auth_manager.get_user_from_token.assert_called_once_with("pui_x")

    @pytest.mark.asyncio
    async def test_no_credentials_is_a_401(self, mock_auth_manager, no_fallback):
        viewer = await current_user.get_media_viewer(self._request())
        assert viewer is None

        with pytest.raises(HTTPException) as exc_info:
            await current_user.require_media_viewer(viewer)
        assert exc_info.value.status_code == 401
        mock_auth_manager.get_user_from_token.assert_not_called()
