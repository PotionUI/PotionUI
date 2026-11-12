import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from src.features.api_keys import routes as api_key_routes
from src.features.auth import routes as auth_routes
from src.features.automation import routes as automation_routes
from src.features.downloads import routes as downloads_routes
from src.features.generation import routes as generation_routes
from src.features.notifications import routes as notification_routes
from src.features.settings import admin_websocket
from src.features.system_monitor import routes as system_routes
from src.platform.database.rows import now_utc
from src.platform.security import current_user
from src.platform.security.current_user import authenticate_websocket, refuse_websocket
from src.platform.websocket.download_connection_hub import DownloadConnectionHub
from src.platform.websocket.key_sockets import key_sockets
from src.platform.websocket.tickets import TICKET_TTL_SECONDS, ws_tickets

SOCKETS = {
    "/ws/generation": "read",
    "/ws/notifications": "read",
    "/ws/system": "read",
    "/ws/downloads": "admin",
    "/ws/automations": "admin",
    "/ws/admin": "admin",
}
READ_SOCKETS = [path for path, need in SOCKETS.items() if need == "read"]
ADMIN_SOCKETS = [path for path, need in SOCKETS.items() if need == "admin"]
CONNECTED = "connection_established"


class Holder:
    async def handle_websocket(self, websocket, client_id, *args, **kwargs):
        await websocket.accept()
        await websocket.send_json({"type": CONNECTED})
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    key_sockets._tracked.clear()
    ws_tickets._entries.clear()


@pytest.fixture
def sockets(world, monkeypatch):
    monkeypatch.setattr(system_routes, "monitor_visible_to", lambda repository, user: True)
    world.stack.settings.set_setting("api_key_scopes_allowed", ["read", "generate", "content", "mcp", "admin"])
    world.stack.settings.set_setting("mcp_enabled", True)
    holder = Holder()
    notifications = Mock()
    notifications.repository.unread_count.return_value = 0
    container = SimpleNamespace(
        _generation_controller=holder,
        system_monitor_controller=holder,
        plugin_repository=None,
        notification_controller=notifications,
        download_connection_hub=DownloadConnectionHub(),
        auth=Mock(),
        login_handoff=Mock(),
        content_policy_resolver=None,
        api_key_collaborators=world.stack.collaborators,
        user_repository=world.stack.users,
        mcp_tool_counts=world.tool_counts,
    )
    app = FastAPI()
    app.include_router(auth_routes.build_router(container))
    app.include_router(api_key_routes.build_router(container))
    app.include_router(generation_routes.build_ws_router(container))
    app.include_router(notification_routes.build_ws_router(container))
    app.include_router(system_routes.build_ws_router(container))
    app.include_router(downloads_routes.build_ws_router(container))
    app.include_router(automation_routes.build_ws_router(container))
    app.include_router(admin_websocket.build_router(container))

    @app.websocket("/ws/undeclared")
    async def undeclared(websocket: WebSocket):
        user, error = await authenticate_websocket(websocket)
        if user is None:
            await refuse_websocket(websocket, error)
            return
        await holder.handle_websocket(websocket, "c")

    return TestClient(app, raise_server_exceptions=False)


def ticket_for(sockets, path, headers):
    reply = sockets.post("/api/auth/ws-ticket", json={"path": path}, headers=headers)
    assert reply.status_code == 200, reply.text
    return reply.json()["data"]["ticket"]


def outcome(sockets, url, headers=None):
    try:
        with sockets.websocket_connect(url, headers=headers or {}) as ws:
            return ws.receive_json()["type"]
    except WebSocketDisconnect as closed:
        return closed.code


class TestTicketRoute:
    def test_a_session_gets_a_ticket_for_each_socket(self, world, sockets):
        for path in SOCKETS:
            reply = sockets.post("/api/auth/ws-ticket", json={"path": path}, headers=world.session("root"))

            assert reply.status_code == 200, path
            assert reply.json()["data"]["expires_in"] == TICKET_TTL_SECONDS
            assert len(reply.json()["data"]["ticket"]) == 43

    def test_the_route_needs_a_credential(self, sockets):
        assert sockets.post("/api/auth/ws-ticket", json={"path": "/ws/generation"}).status_code == 401

    def test_an_unknown_socket_is_a_404(self, world, sockets):
        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/ws/nothing"}, headers=world.session("alice"))

        assert reply.status_code == 404

    def test_a_http_path_is_not_a_socket(self, world, sockets):
        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/api/keys"}, headers=world.session("alice"))

        assert reply.status_code == 404

    @pytest.mark.parametrize("path", READ_SOCKETS)
    def test_a_read_key_gets_a_ticket_for_the_read_sockets(self, world, sockets, path):
        _, secret = world.mint(world.alice, scopes=("read",))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": path}, headers=world.bearer(secret))

        assert reply.status_code == 200

    @pytest.mark.parametrize("path", ADMIN_SOCKETS)
    def test_a_read_key_is_refused_a_ticket_for_an_admin_socket(self, world, sockets, path):
        _, secret = world.mint(world.alice, scopes=("read",))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": path}, headers=world.bearer(secret))

        assert reply.status_code == 403
        assert reply.json()["detail"]["error"] == "insufficient_scope"
        assert ws_tickets.size() == 0

    @pytest.mark.parametrize("path", ADMIN_SOCKETS)
    def test_an_admin_key_gets_a_ticket_for_an_admin_socket(self, world, sockets, path):
        _, secret = world.mint(world.root, scopes=("admin",), days=30)

        reply = sockets.post("/api/auth/ws-ticket", json={"path": path}, headers=world.bearer(secret))

        assert reply.status_code == 200

    def test_a_key_without_read_cannot_ask_for_a_ticket(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("mcp",))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/ws/generation"}, headers=world.bearer(secret))

        assert reply.status_code == 403

    def test_a_generate_key_without_read_is_refused_a_read_socket(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("read", "generate"))
        key, only_generate = world.mint(world.alice, scopes=("generate",))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/ws/generation"}, headers=world.bearer(only_generate))

        assert reply.status_code == 403

    def test_a_revoked_key_gets_no_ticket(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/ws/generation"}, headers=world.bearer(secret))

        assert reply.status_code == 401


class TestTicketHandshake:
    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_ticket_opens_each_socket_for_an_admin_session(self, world, sockets, path):
        ticket = ticket_for(sockets, path, world.session("root"))

        assert outcome(sockets, f"{path}?ticket={ticket}") == CONNECTED

    def test_a_ticket_works_once(self, world, sockets):
        ticket = ticket_for(sockets, "/ws/generation", world.session("alice"))

        assert outcome(sockets, f"/ws/generation?ticket={ticket}") == CONNECTED
        assert outcome(sockets, f"/ws/generation?ticket={ticket}") == 4001

    def test_a_ticket_expires(self, world, sockets, monkeypatch):
        ticket = ticket_for(sockets, "/ws/generation", world.session("alice"))
        later = time.monotonic() + TICKET_TTL_SECONDS + 1
        monkeypatch.setattr(ws_tickets, "_clock", lambda: later)

        assert outcome(sockets, f"/ws/generation?ticket={ticket}") == 4001

    def test_a_ticket_is_bound_to_its_socket(self, world, sockets):
        ticket = ticket_for(sockets, "/ws/notifications", world.session("alice"))

        assert outcome(sockets, f"/ws/generation?ticket={ticket}") == 4001
        assert outcome(sockets, f"/ws/notifications?ticket={ticket}") == 4001

    def test_a_made_up_ticket_is_refused(self, sockets):
        assert outcome(sockets, "/ws/generation?ticket=" + "a" * 43) == 4001

    def test_a_key_ticket_opens_the_socket_as_that_key(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        ticket = ticket_for(sockets, "/ws/notifications", world.bearer(secret))

        with sockets.websocket_connect(f"/ws/notifications?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == CONNECTED
            assert key_sockets.count_for_key(key.id) == 1

        assert key_sockets.count_for_key(key.id) == 0

    def test_a_session_ticket_is_not_tracked_as_a_key_socket(self, world, sockets):
        ticket = ticket_for(sockets, "/ws/notifications", world.session("alice"))

        with sockets.websocket_connect(f"/ws/notifications?ticket={ticket}") as ws:
            ws.receive_json()
            assert key_sockets._tracked == {}

    def test_a_key_revoked_between_ticket_and_handshake_is_refused(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        ticket = ticket_for(sockets, "/ws/notifications", world.bearer(secret))
        sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        assert outcome(sockets, f"/ws/notifications?ticket={ticket}") == 4001

    def test_a_non_admin_session_ticket_does_not_open_an_admin_only_socket(self, world, sockets):
        for path in ("/ws/downloads", "/ws/admin"):
            ticket = ticket_for(sockets, path, world.session("alice"))

            assert outcome(sockets, f"{path}?ticket={ticket}") == 4001, path

    def test_a_read_key_ticket_opens_a_read_socket(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("read",))
        ticket = ticket_for(sockets, "/ws/notifications", world.bearer(secret))

        assert outcome(sockets, f"/ws/notifications?ticket={ticket}") == CONNECTED


class TestHeaderHandshake:
    @pytest.mark.parametrize("path", READ_SOCKETS)
    def test_a_read_key_in_the_header_opens_the_read_sockets(self, world, sockets, path):
        _, secret = world.mint(world.alice, scopes=("read",))

        assert outcome(sockets, path, world.bearer(secret)) == CONNECTED

    @pytest.mark.parametrize("path", ADMIN_SOCKETS)
    def test_a_read_key_in_the_header_is_refused_on_the_admin_sockets(self, world, sockets, path):
        _, secret = world.mint(world.root, scopes=("read",))

        assert outcome(sockets, path, world.bearer(secret)) == 4001

    @pytest.mark.parametrize("path", ADMIN_SOCKETS)
    def test_an_admin_key_in_the_header_opens_the_admin_sockets(self, world, sockets, path):
        _, secret = world.mint(world.root, scopes=("admin",), days=30)

        assert outcome(sockets, path, world.bearer(secret)) == CONNECTED

    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_session_token_in_the_header_opens_every_socket(self, world, sockets, path):
        assert outcome(sockets, path, world.session("root")) == CONNECTED

    def test_a_key_with_only_mcp_is_refused(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("mcp",))

        assert outcome(sockets, "/ws/notifications", world.bearer(secret)) == 4001

    def test_a_made_up_key_is_refused(self, sockets):
        assert outcome(sockets, "/ws/notifications", {"Authorization": "Bearer pui_" + "x" * 46}) == 4001

    def test_a_made_up_session_token_is_refused(self, sockets):
        assert outcome(sockets, "/ws/notifications", {"Authorization": "Bearer nope"}) == 4001

    def test_no_credential_is_refused(self, sockets):
        assert outcome(sockets, "/ws/notifications") == 4001

    def test_a_revoked_key_in_the_header_is_refused(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice"))

        assert outcome(sockets, "/ws/notifications", world.bearer(secret)) == 4001

    def test_repeated_bad_keys_are_throttled(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("read",))
        bad = {"Authorization": "Bearer pui_" + "x" * 46}
        for _ in range(current_user._key_failures.max_attempts):
            outcome(sockets, "/ws/notifications", bad)

        assert outcome(sockets, "/ws/notifications", world.bearer(secret)) == 4001


class TestQueryTokenIsGone:
    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_session_token_in_the_query_is_refused(self, world, sockets, path):
        assert outcome(sockets, f"{path}?token=jwt.root.token") == 4001

    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_key_in_the_query_is_refused(self, world, sockets, path):
        _, secret = world.mint(world.root, scopes=("admin",), days=30)

        assert outcome(sockets, f"{path}?token={secret}") == 4001

    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_query_token_is_refused_even_beside_a_valid_header(self, world, sockets, path):
        assert outcome(sockets, f"{path}?token=anything", world.session("root")) == 4001

    @pytest.mark.parametrize("path", list(SOCKETS))
    def test_a_query_token_is_refused_even_beside_a_valid_ticket(self, world, sockets, path):
        ticket = ticket_for(sockets, path, world.session("root"))

        assert outcome(sockets, f"{path}?ticket={ticket}&token=anything") == 4001


def wait_for_close(ws, seconds):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ws.receive_json)
        try:
            future.result(timeout=seconds)
        except WebSocketDisconnect as closed:
            return closed
        except Exception:
            return None
    return None


class TestKeyLifetimeOnOpenSockets:
    def test_revoking_a_key_closes_its_socket_with_4001_within_a_second(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        ticket = ticket_for(sockets, "/ws/notifications", world.bearer(secret))

        with sockets.websocket_connect(f"/ws/notifications?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == CONNECTED
            started = time.monotonic()
            assert sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice")).status_code == 200
            closed = wait_for_close(ws, 1.0)

        assert closed is not None, "the socket was still open a second after the revoke"
        assert closed.code == 4001
        assert closed.reason == "API key revoked"
        assert time.monotonic() - started < 1.5

    def test_revoking_a_key_closes_a_header_authenticated_socket(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))

        with sockets.websocket_connect("/ws/notifications", headers=world.bearer(secret)) as ws:
            ws.receive_json()
            sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice"))
            closed = wait_for_close(ws, 1.0)

        assert closed is not None and closed.code == 4001

    def test_revoking_one_key_leaves_another_key_and_the_session_open(self, world, sockets):
        key, secret = world.mint(world.alice, scopes=("read",))
        _, other_secret = world.mint(world.alice, scopes=("read",))

        with sockets.websocket_connect("/ws/notifications", headers=world.bearer(secret)) as doomed, \
                sockets.websocket_connect("/ws/notifications", headers=world.bearer(other_secret)) as other, \
                sockets.websocket_connect("/ws/notifications", headers=world.session("alice")) as session:
            for ws in (doomed, other, session):
                ws.receive_json()
            sockets.delete(f"/api/keys/{key.id}", headers=world.session("alice"))
            assert wait_for_close(doomed, 1.0) is not None
            for ws in (other, session):
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}

    def test_a_key_expiring_closes_its_socket_at_the_next_heartbeat(self, world, sockets):
        key, secret = world.mint(world.root, scopes=("admin",), days=30)

        with sockets.websocket_connect("/ws/admin", headers=world.bearer(secret)) as ws:
            ws.receive_json()
            assert key_sockets.sweep(now_utc()) == 0
            assert key_sockets.sweep(now_utc() + timedelta(days=31)) == 1
            closed = wait_for_close(ws, 1.0)

        assert closed is not None and closed.code == 4001
        assert closed.reason == "API key expired"

    def test_a_session_socket_is_not_closed_by_a_sweep(self, world, sockets):
        with sockets.websocket_connect("/ws/notifications", headers=world.session("alice")) as ws:
            ws.receive_json()
            assert key_sockets.sweep(now_utc() + timedelta(days=3650)) == 0
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


class TestSessionFlowUnchanged:
    def test_ticket_then_connect_then_ping_then_reconnect_with_a_fresh_ticket(self, world, sockets):
        first = ticket_for(sockets, "/ws/notifications", world.session("alice"))
        with sockets.websocket_connect(f"/ws/notifications?ticket={first}") as ws:
            established = ws.receive_json()
            assert established["type"] == CONNECTED
            assert established["unread_count"] == 0
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}

        second = ticket_for(sockets, "/ws/notifications", world.session("alice"))
        assert second != first
        assert outcome(sockets, f"/ws/notifications?ticket={second}") == CONNECTED


class TestASocketThatDeclaresNothing:
    def test_it_refuses_a_key_in_the_header(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("read",))

        assert outcome(sockets, "/ws/undeclared", world.bearer(secret)) == 4001

    def test_it_refuses_a_key_a_ticket(self, world, sockets):
        _, secret = world.mint(world.alice, scopes=("read",))

        reply = sockets.post("/api/auth/ws-ticket", json={"path": "/ws/undeclared"}, headers=world.bearer(secret))

        assert reply.status_code == 403
        assert reply.json()["detail"]["error"] == "key_not_allowed"

    def test_it_still_serves_a_session(self, world, sockets):
        assert outcome(sockets, "/ws/undeclared", world.session("alice")) == CONNECTED
        ticket = ticket_for(sockets, "/ws/undeclared", world.session("alice"))
        assert outcome(sockets, f"/ws/undeclared?ticket={ticket}") == CONNECTED
