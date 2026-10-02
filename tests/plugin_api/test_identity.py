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


def test_get_user_without_an_id_returns_none():
    with patch("src.plugin_api.identity.get_container") as container:
        assert get_user("") is None

    container.assert_not_called()


def test_get_user_returns_the_stored_user():
    user = SimpleNamespace(id="user-1")
    container = SimpleNamespace(user_repository=Mock(get_by_id=Mock(return_value=user)))

    with patch("src.plugin_api.identity.get_container", return_value=container):
        assert get_user("user-1") is user
