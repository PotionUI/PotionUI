import logging
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from src.platform.security import current_user
from src.platform.security.api_scopes import SESSION_ONLY, ApiScope as S, key_access, key_scope
from src.platform.security.current_user import (
    get_current_active_user,
    get_current_admin_user,
    get_current_user,
    require_media_viewer,
)
from src.platform.security.principal import KeyGrant, ResolvedKey
from src.platform.security.user import AccountType, User

SECRET = "pui_" + "a" * 40 + "ZZZZZZ"


def make_user(user_id="user-1", account_type=AccountType.USER):
    return User(
        id=user_id,
        username=f"name-{user_id}",
        email=f"{user_id}@example.com",
        password_hash="hash",
        account_type=account_type,
    )


def make_grant(scopes, user_id="user-1", key_id="key-1"):
    return KeyGrant(
        key_id=key_id,
        user_id=user_id,
        scopes=frozenset(scopes),
        display_prefix="pui_aaaaaaaa",
    )


class FakeResolver:
    def __init__(self):
        self.keys = {}
        self.resolved = []
        self.uses = []
        self.gate_refusal = None
        self.gate_calls = 0
        self.use_error = None

    def add(self, secret, owner, scopes):
        self.keys[secret] = ResolvedKey(user=owner, grant=make_grant(scopes, user_id=owner.id))

    def resolve(self, plaintext):
        self.resolved.append(plaintext)
        return self.keys.get(plaintext)

    def mcp_gate(self, grant):
        self.gate_calls += 1
        return self.gate_refusal

    def record_use(self, grant, via):
        if self.use_error:
            raise self.use_error
        self.uses.append((grant.key_id, via))


def echo(user):
    return {
        "id": user.id,
        "account_type": user.account_type.value,
        "owner_account_type": user.owner_account_type.value,
        "key": user.key.key_id if user.key else None,
    }


@pytest.fixture
def auth():
    manager = Mock()
    manager.get_user_from_token.return_value = None
    previous = current_user._auth
    current_user.set_auth(manager)
    yield manager
    current_user._auth = previous


@pytest.fixture
def resolver():
    fake = FakeResolver()
    previous = current_user._key_resolver
    current_user.set_key_resolver(fake)
    current_user._key_failures.reset("testclient")
    yield fake
    current_user.set_key_resolver(previous)
    current_user._key_failures.reset("testclient")


@pytest.fixture
def client(auth):
    app = FastAPI()

    reads = APIRouter(prefix="/r", dependencies=[key_access(read=S.READ, write=S.GENERATE)])

    @reads.get("/me")
    async def read_me(user: User = Depends(get_current_user)):
        return echo(user)

    @reads.post("/go")
    async def go(user: User = Depends(get_current_active_user)):
        return echo(user)

    @reads.get("/inline-admin")
    async def inline_admin(user: User = Depends(get_current_user)):
        return {"is_admin": user.account_type == AccountType.ADMIN}

    @reads.get("/admin-only", dependencies=[key_scope(S.READ)])
    async def admin_only(user: User = Depends(get_current_admin_user)):
        return echo(user)

    @reads.post("/session", dependencies=[SESSION_ONLY])
    async def session_only(user: User = Depends(get_current_user)):
        return echo(user)

    @reads.get("/mcp-or-read", dependencies=[key_scope({S.READ, S.MCP})])
    async def mcp_or_read(user: User = Depends(require_media_viewer)):
        return echo(user)

    @reads.get("/mcp-only", dependencies=[key_scope(S.MCP)])
    async def mcp_only(user: User = Depends(get_current_user)):
        return echo(user)

    app.include_router(reads)

    undeclared = APIRouter(prefix="/u")

    @undeclared.get("/me")
    async def undeclared_me(user: User = Depends(get_current_user)):
        return echo(user)

    app.include_router(undeclared)
    return TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


class TestJwtPathIsUnchanged:
    def test_a_jwt_resolves_exactly_as_before_when_no_resolver_is_set(self, client, auth):
        auth.get_user_from_token.return_value = make_user()

        response = client.get("/r/me", headers=bearer("a.jwt.token"))

        assert response.status_code == 200
        assert response.json() == {
            "id": "user-1", "account_type": "USER", "owner_account_type": "USER", "key": None,
        }
        auth.get_user_from_token.assert_called_once_with("a.jwt.token")

    def test_a_jwt_works_on_an_undeclared_route(self, client, auth, resolver):
        auth.get_user_from_token.return_value = make_user()

        assert client.get("/u/me", headers=bearer("a.jwt.token")).status_code == 200
        assert resolver.resolved == []

    def test_a_jwt_admin_keeps_admin_everywhere(self, client, auth, resolver):
        auth.get_user_from_token.return_value = make_user("root", AccountType.ADMIN)

        assert client.get("/r/inline-admin", headers=bearer("a.jwt.token")).json() == {"is_admin": True}
        assert client.get("/r/admin-only", headers=bearer("a.jwt.token")).status_code == 200

    def test_an_invalid_jwt_is_the_same_401_as_before(self, client, auth, resolver):
        response = client.get("/r/me", headers=bearer("bad.jwt.token"))

        assert response.status_code == 401
        assert response.json() == {"detail": "Could not validate credentials"}
        assert response.headers["www-authenticate"] == "Bearer"

    def test_a_missing_header_is_the_same_401_as_before(self, client, auth, resolver):
        response = client.get("/r/me")

        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}

    def test_a_key_shaped_bearer_is_decoded_as_a_jwt_while_no_resolver_is_registered(self, client, auth):
        response = client.get("/r/me", headers=bearer(SECRET))

        assert response.status_code == 401
        assert response.json() == {"detail": "Could not validate credentials"}
        auth.get_user_from_token.assert_called_once_with(SECRET)


class TestNoFallbackBetweenKeyAndJwt:
    def test_a_key_is_never_offered_to_the_jwt_decoder(self, client, auth, resolver):
        resolver.add(SECRET, make_user(), [S.READ])

        assert client.get("/r/me", headers=bearer(SECRET)).status_code == 200
        auth.get_user_from_token.assert_not_called()

    def test_an_unknown_key_is_never_offered_to_the_jwt_decoder(self, client, auth, resolver):
        auth.get_user_from_token.return_value = make_user()

        response = client.get("/r/me", headers=bearer(SECRET))

        assert response.status_code == 401
        auth.get_user_from_token.assert_not_called()

    def test_a_jwt_is_never_offered_to_the_key_resolver(self, client, auth, resolver):
        auth.get_user_from_token.return_value = make_user()

        client.get("/r/me", headers=bearer("eyJhbGciOi.payload.signature"))

        assert resolver.resolved == []


class TestKeyResolution:
    def test_a_valid_key_resolves_to_its_owner_carrying_the_grant(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ])

        response = client.get("/r/me", headers=bearer(SECRET))

        assert response.status_code == 200
        assert response.json()["id"] == "user-1"
        assert response.json()["key"] == "key-1"

    def test_use_is_recorded_as_api(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ])

        client.get("/r/me", headers=bearer(SECRET))

        assert resolver.uses == [("key-1", "api")]

    def test_a_failure_to_record_use_does_not_fail_the_request(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ])
        resolver.use_error = RuntimeError("database is locked")

        assert client.get("/r/me", headers=bearer(SECRET)).status_code == 200

    def test_an_unknown_key_is_a_401_invalid_api_key(self, client, resolver):
        response = client.get("/r/me", headers=bearer(SECRET))

        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "invalid_api_key"
        assert response.headers["www-authenticate"] == "Bearer"
        assert resolver.uses == []

    def test_the_401_does_not_reveal_whether_the_route_exists_or_allows_keys(self, client, resolver):
        existing = client.get("/u/me", headers=bearer(SECRET))
        missing = client.get("/u/nope", headers=bearer(SECRET))

        assert existing.status_code == 401
        assert existing.json()["detail"]["error"] == "invalid_api_key"
        assert missing.status_code == 404

    def test_a_limiter_stops_a_client_that_keeps_sending_bad_keys(self, client, resolver):
        for _ in range(current_user._key_failures.max_attempts):
            assert client.get("/r/me", headers=bearer(SECRET)).status_code == 401

        response = client.get("/r/me", headers=bearer(SECRET))

        assert response.status_code == 429
        assert response.headers["retry-after"] == str(current_user._key_failures.window_seconds)
        assert len(resolver.resolved) == current_user._key_failures.max_attempts

    def test_a_valid_key_does_not_count_against_the_limiter(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ])

        for _ in range(current_user._key_failures.max_attempts + 3):
            assert client.get("/r/me", headers=bearer(SECRET)).status_code == 200


class TestRouteScopes:
    def test_an_undeclared_route_refuses_a_key_with_every_scope(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.READ, S.GENERATE, S.CONTENT, S.ADMIN, S.MCP])

        response = client.get("/u/me", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "key_not_allowed"
        assert resolver.uses == []

    def test_a_session_only_route_refuses_a_key_with_every_scope(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.READ, S.GENERATE, S.CONTENT, S.ADMIN, S.MCP])

        response = client.post("/r/session", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "key_not_allowed"

    def test_a_missing_scope_is_403_insufficient_scope_naming_the_scope(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ])

        response = client.post("/r/go", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "insufficient_scope"
        assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="generate"'
        assert resolver.uses == []

    def test_any_of_a_set_is_enough_and_the_header_lists_the_set(self, client, resolver):
        resolver.add("pui_read", make_user(), [S.READ])
        resolver.add("pui_content", make_user("u2"), [S.CONTENT])

        assert client.get("/r/mcp-or-read", headers=bearer("pui_read")).status_code == 200
        response = client.get("/r/mcp-or-read", headers=bearer("pui_content"))

        assert response.status_code == 403
        assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="read mcp"'

    def test_the_matching_write_scope_passes(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.GENERATE])

        assert client.post("/r/go", headers=bearer(SECRET)).status_code == 200

    def test_admin_implies_read_generate_and_content(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.ADMIN])

        assert client.get("/r/me", headers=bearer(SECRET)).status_code == 200
        assert client.post("/r/go", headers=bearer(SECRET)).status_code == 200

    def test_admin_does_not_imply_mcp(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.ADMIN])

        assert client.get("/r/mcp-only", headers=bearer(SECRET)).status_code == 403


class TestMcpScope:
    def test_an_mcp_only_match_is_recorded_as_mcp(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.MCP])

        assert client.get("/r/mcp-or-read", headers=bearer(SECRET)).status_code == 200
        assert resolver.uses == [("key-1", "mcp")]

    def test_a_key_with_read_and_mcp_on_a_shared_route_counts_as_api_and_skips_the_gate(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.READ, S.MCP])
        resolver.gate_refusal = "MCP is disabled on this instance"

        assert client.get("/r/mcp-or-read", headers=bearer(SECRET)).status_code == 200
        assert resolver.uses == [("key-1", "api")]
        assert resolver.gate_calls == 0

    def test_a_refusal_from_the_gate_is_403_and_names_why(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.MCP])
        resolver.gate_refusal = "MCP is disabled on this instance"

        response = client.get("/r/mcp-only", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.json()["detail"] == {
            "success": False, "error": "mcp_disabled", "message": "MCP is disabled on this instance",
        }
        assert resolver.uses == []


class TestAuthorityDowngrade:
    def test_a_key_without_admin_sees_its_admin_owner_as_a_plain_user(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.READ])

        body = client.get("/r/me", headers=bearer(SECRET)).json()

        assert body["account_type"] == "USER"
        assert body["owner_account_type"] == "ADMIN"

    def test_an_inline_admin_check_refuses_a_key_without_admin(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.READ])

        assert client.get("/r/inline-admin", headers=bearer(SECRET)).json() == {"is_admin": False}

    def test_a_key_with_admin_keeps_the_owners_authority(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.ADMIN])

        body = client.get("/r/me", headers=bearer(SECRET)).json()

        assert body["account_type"] == "ADMIN"
        assert client.get("/r/inline-admin", headers=bearer(SECRET)).json() == {"is_admin": True}

    def test_an_admin_dependency_refuses_a_key_without_admin_by_scope(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.READ, S.GENERATE, S.CONTENT])

        response = client.get("/r/admin-only", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope", scope="admin"'

    def test_an_admin_key_passes_an_admin_dependency(self, client, resolver):
        resolver.add(SECRET, make_user("root", AccountType.ADMIN), [S.ADMIN])

        assert client.get("/r/admin-only", headers=bearer(SECRET)).status_code == 200

    def test_a_key_never_exceeds_an_owner_who_stopped_being_admin(self, client, resolver):
        resolver.add(SECRET, make_user("was-admin", AccountType.USER), [S.ADMIN])

        response = client.get("/r/admin-only", headers=bearer(SECRET))

        assert response.status_code == 403
        assert response.json() == {"detail": "Administrator privileges required"}

    def test_the_owner_record_itself_is_not_mutated(self, client, resolver):
        owner = make_user("root", AccountType.ADMIN)
        resolver.add(SECRET, owner, [S.READ])

        client.get("/r/me", headers=bearer(SECRET))

        assert owner.account_type == AccountType.ADMIN
        assert owner.key is None


class TestMediaViewer:
    def test_a_key_authenticates_the_media_viewer(self, client, resolver):
        resolver.add(SECRET, make_user(), [S.MCP])

        assert client.get("/r/mcp-or-read", headers=bearer(SECRET)).json()["key"] == "key-1"

    def test_an_invalid_key_is_not_rescued_by_a_cookie(self, client, auth, resolver):
        auth.get_user_from_token.return_value = make_user()
        client.cookies.set("potionui_media", "cookie.jwt")

        response = client.get("/r/mcp-or-read", headers=bearer(SECRET))

        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "invalid_api_key"

    def test_the_cookie_never_authenticates_a_key(self, client, auth, resolver):
        resolver.add(SECRET, make_user(), [S.READ])
        client.cookies.set("potionui_media", SECRET)

        response = client.get("/r/mcp-or-read")

        assert response.status_code == 401
        assert resolver.resolved == []


class TestSecretsStayOutOfLogs:
    def test_a_rejected_key_is_logged_by_prefix_only(self, client, resolver, caplog):
        with caplog.at_level(logging.DEBUG):
            client.get("/r/me", headers=bearer(SECRET))

        assert caplog.records
        assert SECRET not in caplog.text
        assert SECRET[12:] not in caplog.text
        assert SECRET[:12] in caplog.text

    def test_an_accepted_key_is_logged_by_id_and_prefix_only(self, client, resolver, caplog):
        resolver.add(SECRET, make_user(), [S.READ])

        with caplog.at_level(logging.DEBUG):
            client.get("/r/me", headers=bearer(SECRET))

        assert "key-1" in caplog.text and "pui_aaaaaaaa" in caplog.text
        assert SECRET not in caplog.text and SECRET[12:] not in caplog.text

    def test_the_error_body_does_not_echo_the_key(self, client, resolver):
        response = client.get("/r/me", headers=bearer(SECRET))

        assert SECRET not in response.text
