"""The admin housekeeping endpoints: overview shape, 202 on a run, 409 while
one is already in flight, and generation-deletion preview/delete."""

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


def _client(worker=None, generation_repository=None, generation_history_facade=None):
    app = FastAPI()
    container = SimpleNamespace(
        housekeeping_worker=worker or _worker(),
        generation_repository=generation_repository or Mock(),
        generation_history_facade=generation_history_facade or Mock(),
    )
    app.include_router(build_admin_router(container))
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


class TestPreviewGenerations:

    def test_it_reports_how_many_generations_the_criteria_match(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("g1", "u1"), ("g2", "u1")]

        response = _client(generation_repository=repository).get(
            "/api/admin/housekeeping/generations/preview",
            params={"older_than_days": 30},
        )

        assert response.status_code == 200
        assert response.json()["data"] == {"count": 2}
        repository.find_by_criteria.assert_called_once_with(
            user_id=None, tag_ids=None, older_than_days=30, created_from=None, created_to=None,
            without_media=False, statuses=None, keep_favorites=True,
        )

    def test_a_negative_age_is_refused(self):
        response = _client().get(
            "/api/admin/housekeeping/generations/preview",
            params={"older_than_days": -1},
        )
        assert response.status_code == 400

    def test_an_unknown_status_is_refused(self):
        response = _client().get(
            "/api/admin/housekeeping/generations/preview",
            params={"statuses": "pending"},
        )
        assert response.status_code == 400


class TestDeleteGenerations:

    def test_no_criteria_is_refused_with_400(self):
        response = _client().delete("/api/admin/housekeeping/generations")

        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "no_criteria"

    def test_matching_generations_are_deleted_grouped_by_owner(self):
        repository = Mock()
        repository.find_by_criteria.return_value = [("g1", "u1"), ("g2", "u2")]
        facade = Mock()
        facade.bulk_delete.side_effect = [
            {"deleted_count": 1, "total_files_deleted": 3},
            {"deleted_count": 1, "total_files_deleted": 1},
        ]

        response = _client(
            generation_repository=repository, generation_history_facade=facade,
        ).delete("/api/admin/housekeeping/generations", params={"without_media": True})

        assert response.status_code == 200
        assert response.json()["data"] == {"deleted_count": 2, "files_deleted": 4}
        assert facade.bulk_delete.call_count == 2


class TestAdminOnly:

    def test_every_endpoint_sits_behind_the_admin_dependency(self):
        app = FastAPI()
        container = SimpleNamespace(
            housekeeping_worker=_worker(),
            generation_repository=Mock(),
            generation_history_facade=Mock(),
        )
        app.include_router(build_admin_router(container))

        paths = {route.path for route in app.routes if getattr(route, "path", "").startswith("/api/admin")}
        assert paths == {
            "/api/admin/housekeeping",
            "/api/admin/housekeeping/run",
            "/api/admin/housekeeping/generations/preview",
            "/api/admin/housekeeping/generations",
        }
        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/admin/housekeeping"):
                assert get_current_admin_user in [d.call for d in route.dependant.dependencies]
