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


def test_the_key_declarations_are_exported_and_are_the_platform_ones():
    from src.plugin_api import identity
    from src.platform.security import api_scopes

    for name in ("ApiScope", "SESSION_ONLY", "key_access", "key_scope"):
        assert name in identity.__all__
        assert getattr(identity, name) is getattr(api_scopes, name)


def test_a_plugin_route_that_declares_nothing_refuses_keys_and_a_declared_one_does_not():
    from fastapi import APIRouter, Depends

    from src.plugin_api.identity import ApiScope, get_current_active_user, key_access
    from src.platform.security.api_scopes import required_scopes

    router = APIRouter(prefix="/plugin")

    @router.get("/open", dependencies=[key_access(read=ApiScope.READ, write=None)])
    async def declared(user=Depends(get_current_active_user)):
        return {}

    @router.get("/closed")
    async def undeclared(user=Depends(get_current_active_user)):
        return {}

    routes = {route.path: route for route in router.routes}
    assert required_scopes(routes["/plugin/open"], "GET") == {ApiScope.READ}
    assert required_scopes(routes["/plugin/closed"], "GET") is None


def test_authenticate_websocket_token_is_exported_with_its_v010_shape():
    import src.plugin_api as plugin_api
    from src.plugin_api import identity

    assert plugin_api.authenticate_websocket_token is identity.authenticate_websocket_token
    assert "authenticate_websocket_token" in identity.__all__
    assert "authenticate_websocket_token" in plugin_api.__all__


def test_authenticate_websocket_token_hands_the_token_to_the_session_check():
    from src.plugin_api.identity import authenticate_websocket_token

    user = SimpleNamespace(id="user-1")
    auth = Mock(authenticate_websocket=Mock(return_value=(user, None)))

    with patch("src.platform.security.current_user.get_auth", return_value=auth):
        assert authenticate_websocket_token("jwt") == (user, None)

    auth.authenticate_websocket.assert_called_once_with("jwt")


def test_authenticate_websocket_token_defaults_to_no_token():
    from src.plugin_api.identity import authenticate_websocket_token

    auth = Mock(authenticate_websocket=Mock(return_value=(None, "Authentication required")))

    with patch("src.platform.security.current_user.get_auth", return_value=auth):
        assert authenticate_websocket_token() == (None, "Authentication required")


def test_authenticate_websocket_token_reports_an_uninitialised_auth_service():
    from src.plugin_api.identity import authenticate_websocket_token

    with patch("src.platform.security.current_user.get_auth", side_effect=RuntimeError("not ready")):
        assert authenticate_websocket_token("jwt") == (None, "Authentication service not available")
