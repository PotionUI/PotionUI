"""Tests for the admin-gated per-generation profile endpoint.

Covers both layers:
  - the controller method ``get_generation_profile`` (report rendering, raw
    jsonl / log download, 404 on missing, unsafe id treated as missing), and
  - the route-level admin gate (``get_current_admin_user``): a non-admin is
    rejected with 403, an admin gets the profile.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.testclient import TestClient

from src.features.generation import profile_paths
from src.features.generation.routes import GenerationController, build_router
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation import GenerationHistoryManager
from src.features.generation.run_report_recorder import RunReportRecorder
from src.platform.filesystem import FileStore
from src.platform.security.current_user import (
    get_current_active_user,
    get_current_admin_user,
)
from src.platform.security.user import User, AccountType


GEN_ID = "gen-profile-123"


def _write_profile(base_dir, generation_id=GEN_ID, with_log=True):
    """Lay down a minimal but valid profile.jsonl (+ generation.log) under the
    same layout the profiler writes, returning the profile dir."""
    pdir = profile_paths.profile_dir(base_dir, generation_id)
    pdir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"t": 0.0, "wall": 0.0, "kind": "event", "event": "generation.start",
         "rss_gb": 2.0, "avail_gb": 10.0, "swap_gb": 0.0, "cpu": 5.0,
         "vram_alloc_gb": {}, "vram_reserved_gb": {}, "pinned_cum_gb": 0.0},
        {"t": 2.0, "wall": 2.0, "kind": "event", "event": "generation.end",
         "rss_gb": 3.0, "avail_gb": 9.0, "swap_gb": 0.0, "cpu": 5.0,
         "vram_alloc_gb": {}, "vram_reserved_gb": {}, "pinned_cum_gb": 0.0},
    ]
    with open(pdir / "profile.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    if with_log:
        (pdir / "generation.log").write_text(
            "2026-07-15 10:00:00,000 ERROR some.module: boom\n"
            "2026-07-15 10:00:00,100 INFO  some.module: quiet\n"
        )
    return pdir


# --------------------------------------------------------------------------- #
# Controller-level tests                                                       #
# --------------------------------------------------------------------------- #

class TestGetGenerationProfileController:

    @pytest.fixture
    def file_service(self, tmp_path):
        mock = Mock(spec=FileStore)
        mock.base_storage_dir = tmp_path
        return mock

    @pytest.fixture
    def controller(self, file_service):
        return GenerationController(
            Mock(spec=GenerationOrchestrator),
            Mock(spec=GenerationHistoryManager),
            file_service,
            Mock(spec=RunReportRecorder),
        )

    @pytest.fixture
    def admin_user(self):
        return SimpleNamespace(id="admin-1", account_type=AccountType.ADMIN)

    @pytest.mark.asyncio
    async def test_default_serves_raw_jsonl(self, controller, file_service, admin_user):
        _write_profile(file_service.base_storage_dir)
        result = await controller.get_generation_profile(GEN_ID, admin_user)
        assert isinstance(result, FileResponse)
        assert result.media_type == "application/x-ndjson"
        assert result.filename == "profile.jsonl"

    @pytest.mark.asyncio
    async def test_log_serves_generation_log(self, controller, file_service, admin_user):
        _write_profile(file_service.base_storage_dir)
        result = await controller.get_generation_profile(GEN_ID, admin_user, file="log")
        assert isinstance(result, FileResponse)
        assert result.filename == "generation.log"

    @pytest.mark.asyncio
    async def test_report_format_renders_text(self, controller, file_service, admin_user):
        _write_profile(file_service.base_storage_dir)
        result = await controller.get_generation_profile(GEN_ID, admin_user, format="report")
        assert isinstance(result, PlainTextResponse)
        body = result.body.decode()
        assert "STAGE TABLE" in body
        assert "generation.start" in body
        assert "SUMMARY" in body
        # Log highlights are folded in from the sibling generation.log.
        assert "LOG HIGHLIGHTS" in body
        assert "boom" in body
        assert "quiet" not in body

    @pytest.mark.asyncio
    async def test_missing_profile_returns_404(self, controller, admin_user):
        with pytest.raises(HTTPException) as exc:
            await controller.get_generation_profile("no-such-gen", admin_user)
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "profile_not_found"

    @pytest.mark.asyncio
    async def test_report_format_missing_returns_404(self, controller, admin_user):
        with pytest.raises(HTTPException) as exc:
            await controller.get_generation_profile("no-such-gen", admin_user, format="report")
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "profile_not_found"

    @pytest.mark.asyncio
    async def test_unsafe_id_treated_as_missing(self, controller, file_service, admin_user):
        # Even if a profile exists for a real id, a traversal id must not reach it.
        _write_profile(file_service.base_storage_dir)
        with pytest.raises(HTTPException) as exc:
            await controller.get_generation_profile("../" + GEN_ID, admin_user)
        assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Route-level admin-gating tests                                              #
# --------------------------------------------------------------------------- #

def _user(account_type):
    return User(
        id=f"{account_type.value}-1",
        username=str(account_type.value),
        email=f"{account_type.value}@example.com",
        password_hash="$2b$12$x",
        account_type=account_type,
        created_at=datetime.utcnow(),
        last_login=None,
    )


class TestGetGenerationProfileRouteGating:

    @pytest.fixture
    def app_and_dir(self, tmp_path):
        file_service = Mock(spec=FileStore)
        file_service.base_storage_dir = tmp_path
        controller = GenerationController(
            Mock(spec=GenerationOrchestrator),
            Mock(spec=GenerationHistoryManager),
            file_service,
            Mock(spec=RunReportRecorder),
        )
        container = SimpleNamespace(_generation_controller=controller)
        router = build_router(container)
        app = FastAPI()
        app.include_router(router)
        return app, tmp_path

    def test_non_admin_forbidden(self, app_and_dir):
        app, base_dir = app_and_dir
        _write_profile(base_dir)  # profile exists; gate must still refuse a non-admin
        app.dependency_overrides[get_current_active_user] = lambda: _user(AccountType.USER)
        client = TestClient(app)
        resp = client.get(f"/api/generations/{GEN_ID}/profile")
        assert resp.status_code == 403

    def test_admin_happy_path_report(self, app_and_dir):
        app, base_dir = app_and_dir
        _write_profile(base_dir)
        app.dependency_overrides[get_current_admin_user] = lambda: _user(AccountType.ADMIN)
        client = TestClient(app)
        resp = client.get(f"/api/generations/{GEN_ID}/profile", params={"format": "report"})
        assert resp.status_code == 200
        assert "STAGE TABLE" in resp.text
        assert "boom" in resp.text

    def test_admin_missing_profile_404(self, app_and_dir):
        app, _ = app_and_dir
        app.dependency_overrides[get_current_admin_user] = lambda: _user(AccountType.ADMIN)
        client = TestClient(app)
        resp = client.get(f"/api/generations/{GEN_ID}/profile")
        assert resp.status_code == 404
