import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.api_keys import routes as api_key_routes
from src.features.api_keys.records import ApiKey, UserApiAccess
from src.features.mcp import routes as mcp_routes
from src.features.mcp.legacy_routes import build_legacy_router
from src.platform.security.api_scopes import ApiScope as S

V010_TOKEN_KEYS = {"id", "name", "token_prefix", "created_at", "last_used_at", "revoked_at"}
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


@pytest.fixture
def legacy(world):
    container = SimpleNamespace(
        api_key_collaborators=world.stack.collaborators,
        user_repository=world.stack.users,
        mcp_tool_collaborators=Mock(),
        mcp_tool_counts=world.tool_counts,
    )
    app = FastAPI()
    app.include_router(api_key_routes.build_router(container))
    app.include_router(mcp_routes.build_router(container))
    app.include_router(build_legacy_router(container))
    return TestClient(app, raise_server_exceptions=False)


def v010_token(world, owner, plaintext, token_id="t1", name="laptop", **overrides):
    values = dict(
        id=token_id, user_id=owner.id, name=name,
        secret_hash=hashlib.sha256(plaintext.encode()).hexdigest(), display_prefix=plaintext[:12], last_four=None,
        scopes=frozenset({S.MCP}), created_at="2026-10-01T00:00:00+00:00",
    )
    values.update(overrides)
    return world.stack.keys.create(ApiKey(**values))


class TestStatus:
    def test_v010_shape_with_both_switches_on(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)

        body = legacy.get("/api/mcp/status", headers=world.session("alice")).json()

        assert body["success"] is True
        assert body["data"] == {"enabled": True, "global_enabled": True, "user_enabled": True}

    def test_enabled_needs_both_switches(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        world.stack.access.save(UserApiAccess(world.alice.id, True, False), "2026-11-11T09:00:00Z", "root")

        data = legacy.get("/api/mcp/status", headers=world.session("alice")).json()["data"]

        assert data == {"enabled": False, "global_enabled": True, "user_enabled": False}

    def test_instance_switch_off_by_default(self, legacy, world):
        data = legacy.get("/api/mcp/status", headers=world.session("alice")).json()["data"]

        assert data == {"enabled": False, "global_enabled": False, "user_enabled": True}

    def test_needs_a_session(self, legacy):
        assert legacy.get("/api/mcp/status").status_code == 401


class TestTokens:
    def test_create_returns_the_v010_shape_with_the_plaintext_once(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)

        body = legacy.post("/api/mcp/tokens", json={"name": " desktop "}, headers=world.session("alice")).json()

        assert body["success"] is True
        data = body["data"]
        assert set(data) == V010_TOKEN_KEYS | {"token"}
        assert data["name"] == "desktop"
        assert data["token"].startswith("pui_")
        assert data["token_prefix"] == data["token"][:12]
        assert data["last_used_at"] is None and data["revoked_at"] is None

    def test_a_created_token_is_an_mcp_only_key_that_authenticates_the_mcp_endpoint(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        secret = legacy.post("/api/mcp/tokens", json={"name": "desktop"}, headers=world.session("alice")).json()["data"]["token"]

        key = world.stack.keys.list_for_user(world.alice.id)[0]
        reply = legacy.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert key.scopes == frozenset({S.MCP})
        assert key.expires_at is None
        assert reply.status_code == 200
        assert legacy.get("/api/keys", headers=world.bearer(secret)).status_code == 403

    def test_create_without_a_name_is_the_v010_soft_error(self, legacy, world):
        reply = legacy.post("/api/mcp/tokens", json={"name": "   "}, headers=world.session("alice"))

        assert reply.status_code == 200
        assert reply.json()["success"] is False
        assert reply.json()["error"] == "invalid_name"

    def test_create_without_the_name_field_is_422(self, legacy, world):
        assert legacy.post("/api/mcp/tokens", json={}, headers=world.session("alice")).status_code == 422

    def test_create_while_mcp_is_off_is_refused_with_a_named_error(self, legacy, world):
        reply = legacy.post("/api/mcp/tokens", json={"name": "desktop"}, headers=world.session("alice"))

        assert reply.status_code == 403
        assert reply.json()["detail"]["error"] == "mcp_disabled"

    def test_list_shows_only_mcp_keys_newest_first_in_the_v010_shape(self, legacy, world):
        v010_token(world, world.alice, "pui_mcp_" + "a" * 43, "old", created_at="2026-09-01T00:00:00+00:00")
        v010_token(world, world.alice, "pui_mcp_" + "b" * 43, "new", created_at="2026-10-01T00:00:00+00:00",
                   revoked_at="2026-10-02T00:00:00+00:00", revoked_by=world.alice.id)
        world.mint(world.alice, scopes=("read",), name="script")
        v010_token(world, world.bob, "pui_mcp_" + "c" * 43, "bobs")

        data = legacy.get("/api/mcp/tokens", headers=world.session("alice")).json()["data"]

        assert [item["id"] for item in data] == ["new", "old"]
        assert all(set(item) == V010_TOKEN_KEYS for item in data)
        assert data[0]["revoked_at"] == "2026-10-02T00:00:00+00:00"
        assert data[1]["token_prefix"] == ("pui_mcp_" + "a" * 43)[:12]
        assert all("token" not in item for item in data)

    def test_revoke_returns_the_v010_shape_and_the_key_stops_working(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        secret = "pui_mcp_" + "d" * 43
        v010_token(world, world.alice, secret, "t-live")
        assert legacy.post("/api/mcp", json=PING, headers=world.bearer(secret)).status_code == 200

        reply = legacy.delete("/api/mcp/tokens/t-live", headers=world.session("alice"))

        assert reply.json()["data"] == {"id": "t-live", "revoked": True}
        assert legacy.post("/api/mcp", json=PING, headers=world.bearer(secret)).status_code == 401

    def test_revoke_of_someone_elses_unknown_or_already_revoked_token_is_404(self, legacy, world):
        v010_token(world, world.bob, "pui_mcp_" + "e" * 43, "bobs")
        v010_token(world, world.alice, "pui_mcp_" + "f" * 43, "gone", revoked_at="2026-10-02T00:00:00+00:00")

        for token_id in ("bobs", "nope", "gone"):
            reply = legacy.delete(f"/api/mcp/tokens/{token_id}", headers=world.session("alice"))
            assert reply.status_code == 404, token_id
            assert reply.json()["detail"]["error"] == "token_not_found"

    def test_revoke_does_not_reach_a_key_without_the_mcp_scope(self, legacy, world):
        key, _ = world.mint(world.alice, scopes=("read",))

        assert legacy.delete(f"/api/mcp/tokens/{key.id}", headers=world.session("alice")).status_code == 404
        assert world.stack.keys.get_by_id(key.id).revoked_at is None

    def test_an_api_key_cannot_manage_tokens(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        _, secret = world.mint(world.alice, scopes=("read", "mcp"))

        assert legacy.get("/api/mcp/tokens", headers=world.bearer(secret)).status_code == 403


class TestMigratedToken:
    def test_a_v010_token_row_authenticates_the_mcp_endpoint_with_the_same_plaintext(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        secret = "pui_mcp_" + "g" * 43
        v010_token(world, world.alice, secret)

        reply = legacy.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert reply.status_code == 200
        assert reply.json()["result"] == {"ok": True}
        assert world.handle_method.await_args.args[3] == world.alice.id

    def test_a_user_switched_off_in_v010_stays_refused(self, legacy, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        secret = "pui_mcp_" + "h" * 43
        v010_token(world, world.alice, secret)
        world.stack.access.save(UserApiAccess(world.alice.id, True, False), "2026-11-11T09:00:00Z", "root")

        assert legacy.post("/api/mcp", json=PING, headers=world.bearer(secret)).status_code == 403


class TestMigratedTokenMedia:
    @pytest.fixture
    def media(self, world):
        from fastapi import APIRouter, Depends

        from src.platform.security.api_scopes import key_scope
        from src.platform.security.current_user import require_media_viewer

        router = APIRouter()

        @router.get("/media/file", dependencies=[key_scope(S.READ, S.MCP)])
        async def file(viewer=Depends(require_media_viewer)):
            return {"id": viewer.id}

        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    def test_a_v010_token_reads_media_as_its_owner(self, media, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        secret = "pui_mcp_" + "m" * 43
        v010_token(world, world.alice, secret)

        reply = media.get("/media/file", headers=world.bearer(secret))

        assert reply.json() == {"id": world.alice.id}

    def test_media_follows_the_same_switches_as_the_mcp_endpoint(self, media, world):
        secret = "pui_mcp_" + "n" * 43
        v010_token(world, world.alice, secret)

        assert media.get("/media/file", headers=world.bearer(secret)).status_code == 403


class TestAdminToggle:
    def test_get_returns_the_v010_shape(self, legacy, world):
        data = legacy.get(f"/api/mcp/admin/users/{world.alice.id}", headers=world.session("root")).json()["data"]

        assert data == {"user_id": world.alice.id, "enabled": True}

    def test_put_switches_the_user_off_in_the_new_table_and_keeps_the_key_switch(self, legacy, world):
        world.stack.access.save(UserApiAccess(world.alice.id, False, True), "2026-11-11T09:00:00Z", "root")

        reply = legacy.put(
            f"/api/mcp/admin/users/{world.alice.id}", json={"enabled": False}, headers=world.session("root")
        )

        assert reply.json()["data"] == {"user_id": world.alice.id, "enabled": False}
        assert world.stack.access.get(world.alice.id) == UserApiAccess(world.alice.id, False, False)

    def test_put_round_trips_through_the_new_admin_route(self, legacy, world):
        legacy.put(f"/api/mcp/admin/users/{world.alice.id}", json={"enabled": False}, headers=world.session("root"))

        assert world.stack.access.get(world.alice.id).mcp_enabled is False

    def test_an_unknown_user_is_404(self, legacy, world):
        for method, kwargs in (("get", {}), ("put", {"json": {"enabled": True}})):
            reply = getattr(legacy, method)("/api/mcp/admin/users/nope", headers=world.session("root"), **kwargs)
            assert reply.status_code == 404
            assert reply.json()["detail"]["error"] == "user_not_found"

    def test_a_non_admin_is_refused(self, legacy, world):
        assert legacy.get(f"/api/mcp/admin/users/{world.alice.id}", headers=world.session("alice")).status_code == 403
        reply = legacy.put(f"/api/mcp/admin/users/{world.alice.id}", json={"enabled": True}, headers=world.session("alice"))
        assert reply.status_code == 403

    def test_an_admin_api_key_cannot_use_it(self, legacy, world):
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
        _, secret = world.mint(world.root, scopes=("admin",), days=30)

        assert legacy.get(f"/api/mcp/admin/users/{world.alice.id}", headers=world.bearer(secret)).status_code == 403


class TestSchema:
    def test_every_legacy_route_is_marked_deprecated_in_openapi(self, legacy):
        paths = legacy.get("/openapi.json").json()["paths"]
        operations = [
            paths["/api/mcp/status"]["get"], paths["/api/mcp/tokens"]["get"], paths["/api/mcp/tokens"]["post"],
            paths["/api/mcp/tokens/{token_id}"]["delete"], paths["/api/mcp/admin/users/{user_id}"]["get"],
            paths["/api/mcp/admin/users/{user_id}"]["put"],
        ]

        assert all(operation.get("deprecated") is True for operation in operations)
