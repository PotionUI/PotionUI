"""Tests for the `src.plugin_api.identity` surface."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.plugin_api.identity import list_user_ids


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
