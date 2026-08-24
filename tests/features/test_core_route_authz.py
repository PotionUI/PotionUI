"""Authorization regression tests for core-route admin gates.

Each fixed route is driven through its REAL FastAPI router (built with a mock
container) with the leaf `get_current_active_user` dependency overridden to a
chosen role, so the real admin gate under test runs. A regular user must be
denied (403); an admin must clear the gate (never 403 - it may still fail
downstream on the mock controller).
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType

from src.features.models.routes import build_router as build_models_router
from src.features.settings.routes import build_router as build_settings_router
from src.features.system_monitor.routes import build_router as build_system_router
from src.features.presets.routes import build_router as build_presets_router
from src.features.providers.routes import build_router as build_providers_router
from src.features.automation.routes import build_router as build_automation_router
from src.features.sessions.routes import build_router as build_sessions_router
from src.features.media_index.routes import build_router as build_media_index_router
from src.features.downloads.routes import build_router as build_downloads_router
from src.features.prompt_database.routes import build_router as build_prompt_database_router
from src.features.pipes.routes import build_router as build_pipes_router


def _user(account_type):
    return User(
        id="u1", username="u", email="u@example.com",
        password_hash="h", account_type=account_type,
    )


def _client(build_router, role):
    app = FastAPI()
    app.include_router(build_router(MagicMock()))

    async def _fake_active_user():
        return _user(role)

    app.dependency_overrides[get_current_active_user] = _fake_active_user
    return TestClient(app, raise_server_exceptions=False)


# (build_router, method, path, json) for every route this audit moved to admin.
GATED = [
    (build_models_router, "post", "/api/models/index", None),
    (build_models_router, "post", "/api/models/info/fetch", {"model_ids": []}),
    (build_models_router, "post", "/api/models/cleanup", None),
    (build_models_router, "post", "/api/models/thumbnails/generate", None),
    (build_models_router, "post", "/api/models/download", {"url": "http://x", "model_type": "checkpoint"}),
    (build_models_router, "put", "/api/models/attributes/def1", {"label": "x"}),
    (build_models_router, "put", "/api/models/m1/description", {"description": "d"}),
    (build_models_router, "put", "/api/models/m1/tags", {"tag_ids": []}),
    (build_models_router, "delete", "/api/models/m1", None),
    (build_settings_router, "post", "/api/settings/models/rescan", None),
    (build_system_router, "post", "/api/system/monitoring/interval?interval=1.0", None),
    (build_presets_router, "post", "/api/presets/p1/reload", None),
    (build_providers_router, "post", "/api/providers/civitai/initialize", None),
    # automation: whole router is admin-gated - a representative spread.
    (build_automation_router, "get", "/api/automations/", None),
    (build_automation_router, "get", "/api/automations/a1", None),
    (build_automation_router, "post", "/api/automations/a1/run", {}),
    (build_automation_router, "delete", "/api/automations/a1", None),
    (build_automation_router, "get", "/api/automations/templates", None),
    # media index: whole router is admin-gated.
    (build_media_index_router, "get", "/api/media-index/status", None),
    (build_media_index_router, "post", "/api/media-index/backfill", {}),
    (build_media_index_router, "post", "/api/media-index/process", {}),
    (build_media_index_router, "get", "/api/media-index/models-status", None),
    # prompt database: only the embedding-status admin endpoint is gated here.
    (build_prompt_database_router, "get", "/api/prompts/embedding-status", None),
    # downloads: whole router is admin-gated - a representative spread.
    (build_downloads_router, "get", "/api/downloads", None),
    (build_downloads_router, "get", "/api/downloads/settings", None),
    (build_downloads_router, "put", "/api/downloads/settings", {}),
    (build_downloads_router, "post", "/api/downloads/model", {"url": "http://x"}),
    (build_downloads_router, "post", "/api/downloads/media", {"url": "http://x"}),
    (build_downloads_router, "post", "/api/downloads/batch", {"urls": []}),
    (build_downloads_router, "post", "/api/downloads/hf-repo", {"repo_id": "org/m"}),
    (build_downloads_router, "post", "/api/downloads/clear-completed", None),
    (build_downloads_router, "post", "/api/downloads/clear-cancelled", None),
    (build_downloads_router, "get", "/api/downloads/d1", None),
    (build_downloads_router, "post", "/api/downloads/d1/pause", None),
    (build_downloads_router, "post", "/api/downloads/d1/resume", None),
    (build_downloads_router, "post", "/api/downloads/d1/cancel", None),
    (build_downloads_router, "post", "/api/downloads/d1/retry", None),
    (build_downloads_router, "delete", "/api/downloads/d1", None),
    # pipes: installing requirements runs pip/git, so the whole router is gated.
    (build_pipes_router, "get", "/api/pipes/p1", None),
    (build_pipes_router, "post", "/api/pipes/p1/install", None),
]


@pytest.mark.parametrize("build,method,path,body", GATED)
def test_regular_user_denied(build, method, path, body):
    client = _client(build, AccountType.USER)
    resp = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert resp.status_code == 403


@pytest.mark.parametrize("build,method,path,body", GATED)
def test_admin_clears_gate(build, method, path, body):
    client = _client(build, AccountType.ADMIN)
    resp = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert resp.status_code != 403


def test_sessions_test_route_removed():
    """The unauthenticated GET /api/sessions/test debug route was deleted.

    With no override the request runs the real auth dependency: `/test` is now
    just captured by the auth-required `/{session_id}` route, so an
    unauthenticated call gets 401 instead of the old 200 debug payload.
    """
    app = FastAPI()
    app.include_router(build_sessions_router(MagicMock()))
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/sessions/test").status_code == 401
