"""The admin thumbnail endpoints: overview shape, 202 on start, 409 while one
runs, cancel, and the job read-back."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.features.media.routes import build_admin_router
from src.features.media.thumbnail_regeneration import ThumbnailJob, ThumbnailJobRunning
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import AccountType, User


def _settings(**values):
    settings = Mock()
    settings.get_setting.side_effect = lambda key, default=None, user_id=None: values.get(key, default)
    return settings


def _regeneration(**overrides):
    regeneration = Mock()
    regeneration.counts.return_value = {"images": 10, "videos": 4, "uploads": 2, "stale": 6}
    regeneration.usage.return_value = {
        "static_bytes": 1000,
        "animated_bytes": 9000,
        "total_bytes": 10000,
        "measured_at": "2026-09-09T00:00:00+00:00",
    }
    regeneration.current.return_value = None
    for key, value in overrides.items():
        setattr(regeneration, key, value)
    return regeneration


def _job(status="running", **overrides):
    job = ThumbnailJob(id="J1", status=status, total=6, done=1, started_at="2026-09-09T00:00:00+00:00")
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _app(settings, regeneration):
    container = SimpleNamespace(settings=settings, thumbnail_regeneration=regeneration)
    app = FastAPI()
    app.include_router(build_admin_router(container))
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id="admin", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )
    return app


class TestThumbnailOverview:

    def test_overview_carries_settings_profiles_counts_usage_and_job(self):
        app = _app(_settings(), _regeneration())

        body = TestClient(app).get("/api/admin/thumbnails").json()

        assert body["success"] is True
        data = body["data"]
        assert set(data) == {"settings", "profiles", "active_profile", "counts", "usage", "job"}
        assert data["settings"] == {
            "sizes": ["medium"],
            "video_fps": 12,
            "video_seconds": 3,
            "video_quality": 50,
            "image_quality": 85,
        }
        assert data["active_profile"] == "balanced"
        assert data["counts"] == {"images": 10, "videos": 4, "uploads": 2, "stale": 6}
        assert data["usage"]["total_bytes"] == 10000
        assert data["job"] is None

    def test_every_named_profile_is_offered_with_an_estimate(self):
        app = _app(_settings(), _regeneration())

        profiles = TestClient(app).get("/api/admin/thumbnails").json()["data"]["profiles"]

        assert set(profiles) == {"compact", "balanced", "full"}
        for payload in profiles.values():
            assert set(payload) == {
                "sizes", "video_fps", "video_seconds", "video_quality", "image_quality", "estimated_bytes",
            }
        assert profiles["full"]["estimated_bytes"] > profiles["balanced"]["estimated_bytes"]
        assert profiles["balanced"]["estimated_bytes"] > profiles["compact"]["estimated_bytes"]

    def test_hand_tuned_values_read_back_as_a_custom_profile(self):
        app = _app(_settings(thumbnail_video_fps=15), _regeneration())

        data = TestClient(app).get("/api/admin/thumbnails").json()["data"]

        assert data["active_profile"] == "custom"
        assert data["settings"]["video_fps"] == 15

    def test_usage_is_null_when_the_driver_is_not_local(self):
        regeneration = _regeneration()
        regeneration.usage.return_value = None
        app = _app(_settings(), regeneration)

        assert TestClient(app).get("/api/admin/thumbnails").json()["data"]["usage"] is None

    def test_a_running_job_is_reported_in_the_overview(self):
        regeneration = _regeneration()
        regeneration.current.return_value = _job()
        app = _app(_settings(), regeneration)

        job = TestClient(app).get("/api/admin/thumbnails").json()["data"]["job"]

        assert job["id"] == "J1"
        assert job["status"] == "running"
        assert job["total"] == 6


class TestRegenerateEndpoints:

    def test_starting_a_run_answers_202_with_the_job(self):
        regeneration = _regeneration()
        regeneration.start.return_value = _job()
        app = _app(_settings(), regeneration)

        response = TestClient(app).post("/api/admin/thumbnails/regenerate")

        assert response.status_code == 202
        assert response.json()["data"]["status"] == "running"

    def test_starting_a_second_run_answers_409(self):
        regeneration = _regeneration()
        regeneration.start.side_effect = ThumbnailJobRunning("already running")
        app = _app(_settings(), regeneration)

        response = TestClient(app).post("/api/admin/thumbnails/regenerate")

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "thumbnail_job_running"

    def test_cancelling_answers_the_job_it_is_stopping(self):
        regeneration = _regeneration()
        regeneration.cancel.return_value = _job(status="cancelling")
        app = _app(_settings(), regeneration)

        response = TestClient(app).post("/api/admin/thumbnails/regenerate/cancel")

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "cancelling"

    def test_cancelling_with_nothing_running_answers_404(self):
        regeneration = _regeneration()
        regeneration.cancel.return_value = None
        app = _app(_settings(), regeneration)

        response = TestClient(app).post("/api/admin/thumbnails/regenerate/cancel")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "thumbnail_job_not_found"

    def test_the_job_endpoint_carries_the_full_job_shape(self):
        regeneration = _regeneration()
        regeneration.current.return_value = _job(
            status="done", done=6, failed=1, current=None,
            finished_at="2026-09-09T00:01:00+00:00", last_error="boom",
            errors=[{"file_id": "F1", "path": "generations/a/0.png", "error": "boom"}],
        )
        app = _app(_settings(), regeneration)

        job = TestClient(app).get("/api/admin/thumbnails/job").json()["data"]

        assert set(job) == {
            "id", "status", "total", "done", "failed", "current",
            "started_at", "finished_at", "last_error", "errors",
        }
        assert job["errors"][0]["file_id"] == "F1"

    def test_the_job_endpoint_answers_null_before_any_run(self):
        app = _app(_settings(), _regeneration())

        assert TestClient(app).get("/api/admin/thumbnails/job").json()["data"] is None


class TestAdminGate:

    def test_a_regular_user_is_refused(self):
        container = SimpleNamespace(settings=_settings(), thumbnail_regeneration=_regeneration())
        app = FastAPI()
        app.include_router(build_admin_router(container))

        def refuse():
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[get_current_admin_user] = refuse

        assert TestClient(app).get("/api/admin/thumbnails").status_code == 403


if __name__ == "__main__":
    unittest.main()
