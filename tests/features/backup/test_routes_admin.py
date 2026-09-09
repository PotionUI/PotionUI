"""The admin backup endpoints: the overview shape, 202 on a run, 409 while one
is in flight, and 404 for an archive that is not in the destination."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.backup.admin import BackupJob, BackupRunning
from src.features.backup.routes import build_admin_router
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import AccountType, User

ARCHIVE = {
    "name": "potionui-backup-20260909-030000.zip",
    "path": "/srv/potionui/backups/potionui-backup-20260909-030000.zip",
    "bytes": 41288314,
    "created_at": "2026-09-09T03:00:00+00:00",
    "tier": "media",
    "tiers": ["config", "media"],
    "app_version": "0.0.4",
    "migration_head": "023_backup_settings",
    "readable": True,
}

OVERVIEW = {
    "settings": {"destination": "backups", "retention": 7, "default_tier": "config"},
    "destination_abs": "/srv/potionui/backups",
    "exists": True,
    "writable": True,
    "archives": [ARCHIVE],
    "mirror": {"last_synced": "2026-09-09T03:00:00+00:00", "day_count": 12, "bytes": 8912334102},
    "last_backup": {"time": "2026-09-09T03:00:00+00:00", "bytes": 41288314, "tier": "media"},
    "cron_line": "0 3 * * * /srv/potionui/potionui backup --tier config --out /srv/potionui/backups",
    "job": None,
}

JOB = BackupJob(
    id="01JOB",
    status="running",
    tier="media",
    started_at="2026-09-09T12:00:00+00:00",
)


def _runs(**overrides):
    runs = Mock()
    runs.overview.return_value = dict(OVERVIEW)
    runs.current.return_value = None
    runs.start.return_value = JOB
    runs.delete_archive.return_value = True
    for key, value in overrides.items():
        setattr(runs, key, value)
    return runs


def _client(runs):
    app = FastAPI()
    app.include_router(build_admin_router(SimpleNamespace(backup_runs=runs)))
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id="admin", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )
    return TestClient(app)


class TestOverview:

    def test_the_panel_gets_settings_archives_mirror_and_the_cron_line(self):
        response = _client(_runs()).get("/api/admin/backups")

        assert response.status_code == 200
        assert response.json()["data"] == OVERVIEW

    def test_an_unwritable_destination_is_reported(self):
        runs = _runs()
        runs.overview.return_value = {**OVERVIEW, "exists": True, "writable": False}

        data = _client(runs).get("/api/admin/backups").json()["data"]

        assert data["writable"] is False

    def test_a_destination_that_is_not_there_yet_is_reported_as_creatable(self):
        runs = _runs()
        runs.overview.return_value = {**OVERVIEW, "exists": False, "writable": True}

        data = _client(runs).get("/api/admin/backups").json()["data"]

        assert data["exists"] is False
        assert data["writable"] is True


class TestRun:

    def test_a_run_is_accepted_with_the_requested_tier(self):
        runs = _runs()

        response = _client(runs).post("/api/admin/backups/run", json={"tier": "media"})

        assert response.status_code == 202
        assert response.json()["data"]["status"] == "running"
        assert response.json()["data"]["tier"] == "media"
        runs.start.assert_called_once_with("media")

    def test_no_body_runs_the_configured_default_tier(self):
        runs = _runs()

        response = _client(runs).post("/api/admin/backups/run")

        assert response.status_code == 202
        runs.start.assert_called_once_with(None)

    def test_a_second_run_is_refused_with_409(self):
        runs = _runs()
        runs.start.side_effect = BackupRunning("A backup is already in progress")

        response = _client(runs).post("/api/admin/backups/run", json={"tier": "config"})

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "backup_running"

    def test_an_unknown_tier_is_refused_with_400(self):
        runs = _runs()
        runs.start.side_effect = ValueError("unknown tier 'everything'")

        response = _client(runs).post("/api/admin/backups/run", json={"tier": "everything"})

        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "invalid_tier"


class TestJob:

    def test_no_run_this_process_reads_back_as_null(self):
        response = _client(_runs()).get("/api/admin/backups/job")

        assert response.status_code == 200
        assert response.json()["data"] is None

    def test_a_finished_run_reports_its_archive_and_size(self):
        runs = _runs()
        runs.current.return_value = BackupJob(
            id="01JOB", status="done", tier="config",
            started_at="2026-09-09T12:00:00+00:00",
            finished_at="2026-09-09T12:00:09+00:00",
            archive="/srv/potionui/backups/potionui-backup-20260909-120000.zip",
            bytes=41288314,
        )

        data = _client(runs).get("/api/admin/backups/job").json()["data"]

        assert data["status"] == "done"
        assert data["bytes"] == 41288314
        assert data["archive"].endswith("potionui-backup-20260909-120000.zip")


class TestDelete:

    def test_an_archive_in_the_destination_is_deleted(self):
        runs = _runs()

        response = _client(runs).delete(f"/api/admin/backups/{ARCHIVE['name']}")

        assert response.status_code == 200
        runs.delete_archive.assert_called_once_with(ARCHIVE["name"])

    def test_an_archive_that_is_not_there_is_a_404(self):
        runs = _runs()
        runs.delete_archive.return_value = False

        response = _client(runs).delete("/api/admin/backups/potionui-backup-19990101-000000.zip")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "archive_not_found"
