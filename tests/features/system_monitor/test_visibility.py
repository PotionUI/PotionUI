from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.features.plugins.operations import get_active_sidebar_widgets
from src.features.system_monitor import routes
from src.platform.runtime.gpu_profile import build_gpu_profile
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType

WIDGET = {
    "id": "system-monitor",
    "component": "SystemMonitorWidget.js",
    "position": "bottom",
    "order": 10,
    "require_role": "ADMIN",
    "role_setting": "visible_to",
}


class FakeRepo:
    def __init__(self, visible_to=None, enabled=True):
        self.visible_to = visible_to
        self.enabled = enabled

    def get_plugin_by_id(self, plugin_id):
        return SimpleNamespace(id=plugin_id, enabled=self.enabled)

    def get_plugin_setting(self, plugin_id, key, user_id=None):
        if key != "visible_to" or self.visible_to is None:
            return None
        return SimpleNamespace(setting_value=self.visible_to)

    def get_enabled_plugins(self):
        return [SimpleNamespace(id="system-monitor")] if self.enabled else []


class FakeController:
    async def get_system_stats(self, user):
        return {"success": True, "data": {"gpu": {}}}

    async def handle_websocket(self, websocket, client_id):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()


def _user(account_type):
    return SimpleNamespace(id="u", account_type=account_type)


def _client(repo, account_type, monkeypatch):
    user = _user(account_type)
    monkeypatch.setattr(routes, "detect_gpu_profile", lambda: build_gpu_profile((8, 9), 24.04, "RTX"))
    monkeypatch.setattr(routes, "authenticate_websocket_token", lambda token: (user, None))
    container = SimpleNamespace(system_monitor_controller=FakeController(), plugin_repository=repo)
    app = FastAPI()
    app.include_router(routes.build_router(container))
    app.include_router(routes.build_ws_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app)


def _registry():
    manifest = SimpleNamespace(id="system-monitor", sidebar_widgets=[WIDGET])
    return SimpleNamespace(get_plugin=lambda plugin_id: manifest)


def _ws_connects(client):
    try:
        with client.websocket_connect("/ws/system?token=t") as ws:
            return ws.receive_text() == "hello"
    except WebSocketDisconnect:
        return False


@pytest.mark.parametrize("path", ["/api/system/stats", "/api/system/gpu-profile"])
def test_regular_user_is_refused_by_default(path, monkeypatch):
    assert _client(FakeRepo(), AccountType.USER, monkeypatch).get(path).status_code == 403


@pytest.mark.parametrize("path", ["/api/system/stats", "/api/system/gpu-profile"])
def test_admin_only_setting_refuses_regular_user(path, monkeypatch):
    assert _client(FakeRepo("admins"), AccountType.USER, monkeypatch).get(path).status_code == 403


@pytest.mark.parametrize("path", ["/api/system/stats", "/api/system/gpu-profile"])
def test_admin_is_allowed_in_every_mode(path, monkeypatch):
    for visible_to in (None, "admins", "everyone"):
        assert _client(FakeRepo(visible_to), AccountType.ADMIN, monkeypatch).get(path).status_code == 200


@pytest.mark.parametrize("path", ["/api/system/stats", "/api/system/gpu-profile"])
def test_everyone_setting_opens_endpoints_to_regular_user(path, monkeypatch):
    assert _client(FakeRepo("everyone"), AccountType.USER, monkeypatch).get(path).status_code == 200


def test_everyone_setting_is_ignored_while_plugin_disabled(monkeypatch):
    client = _client(FakeRepo("everyone", enabled=False), AccountType.USER, monkeypatch)
    assert client.get("/api/system/stats").status_code == 403
    assert not _ws_connects(client)


def test_admin_keeps_stats_while_plugin_disabled(monkeypatch):
    client = _client(FakeRepo("admins", enabled=False), AccountType.ADMIN, monkeypatch)
    assert client.get("/api/system/stats").status_code == 200
    assert _ws_connects(client)


def test_websocket_refuses_regular_user_by_default(monkeypatch):
    assert not _ws_connects(_client(FakeRepo(), AccountType.USER, monkeypatch))


def test_websocket_admin_and_everyone(monkeypatch):
    assert _ws_connects(_client(FakeRepo(), AccountType.ADMIN, monkeypatch))
    assert _ws_connects(_client(FakeRepo("everyone"), AccountType.USER, monkeypatch))


@pytest.mark.parametrize("visible_to", [None, "admins", "everyone"])
@pytest.mark.parametrize("account_type", [AccountType.USER, AccountType.ADMIN])
def test_vram_fit_check_works_in_every_mode_and_returns_only_vram(visible_to, account_type, monkeypatch):
    body = _client(FakeRepo(visible_to), account_type, monkeypatch).get("/api/system/vram").json()
    assert body == {"vram_gb": 24.0}


def test_vram_is_null_without_gpu(monkeypatch):
    client = _client(FakeRepo(), AccountType.USER, monkeypatch)
    monkeypatch.setattr(routes, "detect_gpu_profile", lambda: build_gpu_profile(None, 0))
    assert client.get("/api/system/vram").json() == {"vram_gb": None}


def _widgets(repo, account_type):
    return [w["widget_id"] for w in get_active_sidebar_widgets(repo, _registry(), account_type.value)]


def test_widget_hidden_from_regular_user_by_default():
    assert _widgets(FakeRepo(), AccountType.USER) == []
    assert _widgets(FakeRepo("admins"), AccountType.USER) == []


def test_widget_shown_to_admin_and_to_everyone_when_opened():
    assert _widgets(FakeRepo(), AccountType.ADMIN) == ["system-monitor"]
    assert _widgets(FakeRepo("everyone"), AccountType.USER) == ["system-monitor"]


def test_widget_gone_for_everyone_when_plugin_disabled():
    assert _widgets(FakeRepo("everyone", enabled=False), AccountType.ADMIN) == []


def test_widget_without_role_is_unrestricted():
    manifest = SimpleNamespace(
        id="p", sidebar_widgets=[{"id": "w", "component": "W.js", "order": 1}]
    )
    registry = SimpleNamespace(get_plugin=lambda plugin_id: manifest)
    repo = SimpleNamespace(
        get_enabled_plugins=lambda: [SimpleNamespace(id="p")],
        get_plugin_setting=lambda *a: None,
    )
    assert [w["widget_id"] for w in get_active_sidebar_widgets(repo, registry, "USER")] == ["w"]


def test_shipped_manifest_widget_matches_the_access_constants():
    from pathlib import Path

    import yaml

    from src.features.system_monitor import access

    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "content/plugins/marketplace/system-monitor/manifest.yml").read_text(encoding="utf-8")
    )
    widget = manifest["sidebar_widgets"][0]
    assert manifest["id"] == access.PLUGIN_ID
    assert widget["require_role"] == access.ADMIN_ROLE
    assert widget["role_setting"] == access.ROLE_SETTING
    assert access.ROLE_SETTING in [s["name"] for s in manifest["settings"]]
