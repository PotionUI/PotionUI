from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.system_monitor import routes
from src.platform.runtime.gpu_profile import build_gpu_profile
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType


def _client():
    app = FastAPI()
    repo = SimpleNamespace(get_plugin_by_id=lambda _id: None, get_plugin_setting=lambda *_a: None)
    container = SimpleNamespace(system_monitor_controller=object(), plugin_repository=repo)
    app.include_router(routes.build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
        id="u", account_type=AccountType.ADMIN
    )
    return TestClient(app)


def test_gpu_profile_route_returns_the_detected_profile(monkeypatch):
    monkeypatch.setattr(routes, "detect_gpu_profile", lambda: build_gpu_profile((8, 9), 24, "RTX 4090"))
    body = _client().get("/api/system/gpu-profile").json()
    assert body["generation"] == "ada"
    assert body["vram_gb"] == 24.0
    assert "fp8" in body["fast_precisions"]
    assert "nvfp4" not in body["fast_precisions"]


def test_gpu_profile_route_without_gpu(monkeypatch):
    monkeypatch.setattr(routes, "detect_gpu_profile", lambda: build_gpu_profile(None, 0))
    body = _client().get("/api/system/gpu-profile").json()
    assert body["generation"] == "none"
    assert body["fast_precisions"] == []
