"""The admin housekeeping endpoints: overview shape, 202 on a run, 409 while
one is already in flight."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.housekeeping.routes import build_admin_router
from src.features.housekeeping.worker import HousekeepingRunning
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import AccountType, User

RETENTION = {
    "tmp_retention_days": 7,
    "run_report_retention_days": 30,
    "llm_trace_retention_days": 7,
}

LAST_RUN = {
    "started_at": "2026-09-09T10:00:00+00:00",
    "finished_at": "2026-09-09T10:00:04+00:00",
    "tasks": {
        "tmp": {"removed": 1204, "bytes_freed": 7730941132, "errors": []},
        "run_reports": {"rows_removed": 310, "errors": []},
        "llm_traces": {"rows_removed": 42, "errors": []},
    },
    "errors": [],
}


def _worker(**overrides):
    worker = Mock()
    worker.retention.return_value = dict(RETENTION)
    worker.running = False
    worker.last_run = None
    worker.next_run_at = "2026-09-10T10:00:00+00:00"
    worker.preview.return_value = {
        "tmp": {"files": 1204, "bytes": 7730941132},
        "run_reports": 310,
        "llm_traces": 42,
    }
    for key, value in overrides.items():
        setattr(worker, key, value)
    return worker


def _client(worker):
    app = FastAPI()
    app.include_router(build_admin_router(SimpleNamespace(housekeeping_worker=worker)))
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id="admin", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )
    return TestClient(app)


class TestOverview:

    def test_it_carries_the_windows_the_schedule_and_the_estimate(self):
        body = _client(_worker()).get("/api/admin/housekeeping").json()

        assert body["success"] is True
        data = body["data"]
        assert set(data) == {"settings", "running", "last_run", "next_run_at", "preview"}
        assert data["settings"] == RETENTION
        assert data["running"] is False
        assert data["last_run"] is None
        assert data["next_run_at"] == "2026-09-10T10:00:00+00:00"
        assert data["preview"] == {
            "tmp": {"files": 1204, "bytes": 7730941132},
            "run_reports": 310,
            "llm_traces": 42,
        }

    def test_a_finished_pass_is_reported_verbatim(self):
        body = _client(_worker(last_run=LAST_RUN)).get("/api/admin/housekeeping").json()

        assert body["data"]["last_run"] == LAST_RUN

    def test_it_reports_a_pass_that_is_running(self):
        body = _client(_worker(running=True)).get("/api/admin/housekeeping").json()

        assert body["data"]["running"] is True


class TestRun:

    def test_a_finished_pass_comes_back_as_202_with_its_summary(self):
        async def run_now():
            return LAST_RUN

        worker = _worker(run_now=run_now)

        response = _client(worker).post("/api/admin/housekeeping/run")

        assert response.status_code == 202
        assert response.json()["data"] == LAST_RUN

    def test_a_pass_already_in_flight_is_refused_with_409(self):
        async def run_now():
            raise HousekeepingRunning("A housekeeping pass is already running")

        response = _client(_worker(run_now=run_now)).post("/api/admin/housekeeping/run")

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "housekeeping_running"


class TestAdminOnly:

    def test_both_endpoints_sit_behind_the_admin_dependency(self):
        app = FastAPI()
        app.include_router(build_admin_router(SimpleNamespace(housekeeping_worker=_worker())))

        paths = {route.path for route in app.routes if getattr(route, "path", "").startswith("/api/admin")}
        assert paths == {"/api/admin/housekeeping", "/api/admin/housekeeping/run"}
        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/admin/housekeeping"):
                assert get_current_admin_user in [d.call for d in route.dependant.dependencies]
