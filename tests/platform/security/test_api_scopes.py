import pytest
from fastapi import APIRouter, Depends, FastAPI

from src.platform.security.api_scopes import (
    SESSION_ONLY,
    ApiScope as S,
    expand_scopes,
    key_access,
    key_scope,
    required_scopes,
)
from src.platform.security.current_user import get_current_active_user, get_current_admin_user, get_current_user


def _route(app, path, method):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            return route
    raise AssertionError(f"no route {method} {path}")


def _handler():
    async def handler():
        return {}

    return handler


@pytest.fixture
def app():
    app = FastAPI()

    declared = APIRouter(prefix="/declared", dependencies=[key_access(read=S.READ, write=S.GENERATE)])
    declared.get("/items")(_handler())
    declared.post("/items")(_handler())
    declared.put("/items")(_handler())
    declared.delete("/items")(_handler())
    declared.get("/override", dependencies=[key_scope(S.CONTENT)])(_handler())
    declared.get("/either", dependencies=[key_scope({S.READ, S.MCP})])(_handler())
    declared.get("/two-markers", dependencies=[key_scope(S.CONTENT), key_scope(S.GENERATE)])(_handler())
    declared.post("/session", dependencies=[SESSION_ONLY])(_handler())
    app.include_router(declared)

    half = APIRouter(prefix="/half", dependencies=[key_access(read=S.READ, write=None)])
    half.get("/items")(_handler())
    half.post("/items")(_handler())
    half.post("/admin-write", dependencies=[Depends(get_current_admin_user)])(_handler())
    app.include_router(half)

    admin = APIRouter(prefix="/admin", dependencies=[key_access(read=S.READ, write=S.CONTENT)])
    admin.get("/direct", dependencies=[Depends(get_current_admin_user)])(_handler())

    async def nested_admin(user=Depends(get_current_admin_user)):
        return user

    admin.get("/nested")(_handler_with(nested_admin))
    admin.get("/plain")(_handler())
    admin.post("/sess", dependencies=[SESSION_ONLY, Depends(get_current_admin_user)])(_handler())
    app.include_router(admin)

    bare = APIRouter(prefix="/bare")
    bare.get("/items")(_handler())
    bare.get("/user")(_handler_with(get_current_user))
    bare.get("/active")(_handler_with(get_current_active_user))
    bare.get("/admin")(_handler_with(get_current_admin_user))
    app.include_router(bare)
    return app


def _handler_with(dependency):
    async def handler(user=Depends(dependency)):
        return {}

    return handler


class TestMethodDefaults:
    def test_reads_use_the_read_scope(self, app):
        assert required_scopes(_route(app, "/declared/items", "GET"), "GET") == {S.READ}

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_writes_use_the_write_scope(self, app, method):
        assert required_scopes(_route(app, "/declared/items", method), method) == {S.GENERATE}

    @pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "head"])
    def test_head_and_options_are_reads(self, app, method):
        assert required_scopes(_route(app, "/declared/items", "GET"), method) == {S.READ}

    def test_a_half_set_to_none_refuses_keys(self, app):
        assert required_scopes(_route(app, "/half/items", "GET"), "GET") == {S.READ}
        assert required_scopes(_route(app, "/half/items", "POST"), "POST") is None


class TestRouteOverride:
    def test_a_route_marker_overrides_the_router_marker_for_every_method(self, app):
        assert required_scopes(_route(app, "/declared/override", "GET"), "GET") == {S.CONTENT}

    def test_the_last_marker_wins(self, app):
        assert required_scopes(_route(app, "/declared/two-markers", "GET"), "GET") == {S.GENERATE}

    def test_a_set_means_any_of_them(self, app):
        assert required_scopes(_route(app, "/declared/either", "GET"), "GET") == {S.READ, S.MCP}

    def test_session_only_refuses_keys_on_every_method(self, app):
        route = _route(app, "/declared/session", "POST")
        assert required_scopes(route, "POST") is None
        assert required_scopes(route, "GET") is None


class TestNoMarker:
    def test_an_undeclared_route_refuses_keys(self, app):
        assert required_scopes(_route(app, "/bare/items", "GET"), "GET") is None

    def test_no_route_refuses_keys(self):
        assert required_scopes(None, "GET") is None


class TestAdminDependency:
    @pytest.mark.parametrize("path", ["/admin/direct", "/admin/nested", "/bare/admin"])
    def test_an_admin_dependency_requires_admin_wherever_it_sits(self, app, path):
        assert required_scopes(_route(app, path, "GET"), "GET") == {S.ADMIN}

    def test_user_dependencies_do_not_count_as_admin(self, app):
        assert required_scopes(_route(app, "/admin/plain", "GET"), "GET") == {S.READ}
        assert required_scopes(_route(app, "/bare/user", "GET"), "GET") is None
        assert required_scopes(_route(app, "/bare/active", "GET"), "GET") is None

    def test_an_admin_dependency_beats_a_router_that_gives_keys_no_writes(self, app):
        assert required_scopes(_route(app, "/half/admin-write", "POST"), "POST") == {S.ADMIN}

    def test_session_only_beats_an_admin_dependency(self, app):
        assert required_scopes(_route(app, "/admin/sess", "POST"), "POST") is None


class TestPolicyIsCached:
    def test_the_route_is_inspected_once(self, app, monkeypatch):
        from src.platform.security import api_scopes

        route = _route(app, "/declared/items", "GET")
        required_scopes(route, "GET")
        monkeypatch.setattr(api_scopes, "_depends_on_admin", lambda dependant: pytest.fail("inspected twice"))

        assert required_scopes(route, "GET") == {S.READ}


class TestExpansion:
    def test_admin_implies_read_generate_and_content(self):
        assert expand_scopes([S.ADMIN]) == {S.ADMIN, S.READ, S.GENERATE, S.CONTENT}

    def test_admin_does_not_imply_mcp(self):
        assert S.MCP not in expand_scopes([S.ADMIN])

    def test_other_scopes_imply_nothing(self):
        assert expand_scopes([S.READ, S.MCP]) == {S.READ, S.MCP}
