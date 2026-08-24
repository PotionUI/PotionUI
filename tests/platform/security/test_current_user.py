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
    previous = current_user._auth_manager
    current_user.set_auth_manager(manager)
    yield manager
    current_user._auth_manager = previous


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
