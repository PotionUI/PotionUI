"""Tests for the `src.plugin_api.identity` surface."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.plugin_api.identity import get_user, list_user_ids


def test_list_user_ids_returns_every_user_id():
    users = [SimpleNamespace(id="user-1"), SimpleNamespace(id="user-2")]
    user_repository = Mock(get_all=Mock(return_value=users))
    container = SimpleNamespace(user_repository=user_repository)

    with patch("src.plugin_api.identity.get_container", return_value=container):
        result = list_user_ids()

    assert result == ["user-1", "user-2"]


def test_list_user_ids_empty_instance_returns_empty_list():
    user_repository = Mock(get_all=Mock(return_value=[]))
    container = SimpleNamespace(user_repository=user_repository)

    with patch("src.plugin_api.identity.get_container", return_value=container):
        result = list_user_ids()

    assert result == []


def test_get_user_returns_the_user_with_that_id():
    user = SimpleNamespace(id="user-1")
    user_repository = Mock(get_by_id=Mock(return_value=user))
    container = SimpleNamespace(user_repository=user_repository)

    with patch("src.plugin_api.identity.get_container", return_value=container):
        result = get_user("user-1")

    assert result is user
    user_repository.get_by_id.assert_called_once_with("user-1")


def test_get_user_returns_none_for_an_unknown_id():
    user_repository = Mock(get_by_id=Mock(return_value=None))
    container = SimpleNamespace(user_repository=user_repository)

    with patch("src.plugin_api.identity.get_container", return_value=container):
        result = get_user("missing")

    assert result is None


def test_get_user_returns_none_for_a_user_who_is_not_active():
    user = SimpleNamespace(id="user-1")
    user_repository = Mock(get_by_id=Mock(return_value=user))
    container = SimpleNamespace(user_repository=user_repository)

    with patch("src.plugin_api.identity.get_container", return_value=container), patch(
        "src.plugin_api.identity.is_active_user", return_value=False
    ):
        result = get_user("user-1")

    assert result is None


def test_get_user_without_an_id_returns_none():
    with patch("src.plugin_api.identity.get_container") as container:
        assert get_user("") is None

    container.assert_not_called()


def test_the_request_dependency_and_get_user_share_one_activeness_rule():
    import asyncio

    import pytest
    from fastapi import HTTPException

    from src.platform.security import current_user

    user = SimpleNamespace(id="user-1")
    with patch("src.platform.security.current_user.is_active_user", return_value=False):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(current_user.get_current_active_user(user))
    assert refused.value.status_code == 403
    assert asyncio.run(current_user.get_current_active_user(user)) is user
    assert current_user.is_active_user(user) is True
    assert current_user.is_active_user(None) is False
