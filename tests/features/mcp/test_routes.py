import hashlib

from src.features.api_keys.records import ApiKey
from src.features.api_keys.records import UserApiAccess
from src.platform.security.api_scopes import ApiScope as S

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def detail(response):
    return response.json()["detail"]


def enable_mcp(world):
    world.stack.settings.set_setting("mcp_enabled", True)


class TestMcpEndpointAuth:
    def test_missing_bearer_header_is_401_with_www_authenticate(self, client):
        response = client.post("/api/mcp", json=PING)

        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"

    def test_a_non_key_bearer_that_is_not_a_session_is_401(self, client):
        response = client.post("/api/mcp", json=PING, headers={"Authorization": "Bearer not-a-real-token"})

        assert response.status_code == 401

    def test_an_unknown_key_is_401(self, client, world):
        response = client.post("/api/mcp", json=PING, headers=world.bearer("pui_" + "a" * 46))

        assert (response.status_code, detail(response)["error"]) == (401, "invalid_api_key")

    def test_a_revoked_key_is_401(self, client, world):
        enable_mcp(world)
        key, secret = world.mint(world.alice, scopes=("mcp",))
        client.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        response = client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert response.status_code == 401

    def test_an_expired_key_is_401(self, client, world):
        from src.platform.database import get_cursor

        enable_mcp(world)
        key, secret = world.mint(world.alice, scopes=("mcp",), days=30)
        with get_cursor() as cursor:
            cursor.execute("UPDATE api_keys SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (key.id,))

        assert client.post("/api/mcp", json=PING, headers=world.bearer(secret)).status_code == 401

    def test_a_browser_session_is_refused_with_a_message_naming_the_need_for_a_key(self, client, world):
        response = client.post("/api/mcp", json=PING, headers=world.session("alice"))

        assert response.status_code == 401
        assert detail(response)["message"] == "MCP needs an API key"
        world.handle_method.assert_not_called()

    def test_a_disabled_instance_is_403_even_with_a_good_key(self, client, world):
        world.stack.settings.set_setting("mcp_enabled", True)
        _, secret = world.mint(world.alice, scopes=("mcp",))
        world.stack.settings.set_setting("mcp_enabled", False)

        response = client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert response.status_code == 403
        assert detail(response)["message"] == "MCP is disabled on this instance"
        world.handle_method.assert_not_called()

    def test_a_user_with_mcp_switched_off_is_403_even_when_the_instance_is_on(self, client, world):
        enable_mcp(world)
        _, secret = world.mint(world.alice, scopes=("mcp",))
        world.stack.access.save(UserApiAccess(world.alice.id, True, False), "2026-11-11T09:00:00Z", "root")

        response = client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert response.status_code == 403
        assert detail(response)["message"] == "MCP is disabled for this user"

    def test_a_key_without_the_mcp_scope_is_refused_whatever_else_it_holds(self, client, world):
        enable_mcp(world)
        world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "admin"])
        _, secret = world.mint(world.root, scopes=("read", "generate", "content", "admin"), days=30)

        response = client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert response.status_code == 403
        assert detail(response)["error"] == "insufficient_scope"
        assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="mcp"'
        world.handle_method.assert_not_called()

    def test_a_key_with_mcp_on_a_fully_enabled_instance_reaches_the_protocol(self, client, world):
        enable_mcp(world)
        _, secret = world.mint(world.alice, scopes=("mcp",))

        response = client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert response.status_code == 200
        assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        args, kwargs = world.handle_method.call_args
        assert args[1] == "ping" and args[3] == world.alice.id
        assert kwargs["is_admin"] is False

    def test_an_admins_mcp_key_keeps_the_owners_entitlement_without_admin_authority(self, client, world):
        enable_mcp(world)
        _, secret = world.mint(world.root, scopes=("mcp",))

        client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert world.handle_method.call_args.kwargs["is_admin"] is True

    def test_a_notification_gets_202_with_no_body(self, client, world):
        enable_mcp(world)
        world.handle_method.return_value = None
        _, secret = world.mint(world.alice, scopes=("mcp",))

        response = client.post(
            "/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=world.bearer(secret)
        )

        assert response.status_code == 202

    def test_use_is_recorded_as_mcp(self, client, world):
        enable_mcp(world)
        key, secret = world.mint(world.alice, scopes=("mcp",))

        client.post("/api/mcp", json=PING, headers=world.bearer(secret))

        assert world.stack.keys.get_by_id(key.id).last_used_via == "mcp"

    def test_a_migrated_pui_mcp_token_keeps_working_with_its_old_plaintext(self, client, world):
        enable_mcp(world)
        plaintext = "pui_mcp_" + "Zx9-_" * 8 + "abc"
        world.stack.keys.create(ApiKey(
            id="legacy", user_id=world.alice.id, name="laptop",
            secret_hash=hashlib.sha256(plaintext.encode()).hexdigest(), display_prefix=plaintext[:12],
            last_four=None, scopes=frozenset({S.MCP}), created_at="2026-05-01T00:00:00+00:00",
        ))

        response = client.post("/api/mcp", json=PING, headers=world.bearer(plaintext))

        assert response.status_code == 200

    def test_an_mcp_key_reaches_nothing_else(self, client, world):
        enable_mcp(world)
        _, secret = world.mint(world.alice, scopes=("mcp",))

        for path in ("/api/keys", "/api/undeclared/me", "/api/admin/keys"):
            assert client.get(path, headers=world.bearer(secret)).status_code == 403, path

    def test_malformed_json_is_a_parse_error_after_authentication(self, client, world):
        enable_mcp(world)
        _, secret = world.mint(world.alice, scopes=("mcp",))

        response = client.post("/api/mcp", content=b"{not json", headers=world.bearer(secret))

        assert response.json()["error"]["code"] == -32700
