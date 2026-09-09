"""The admin log-tail endpoint: admin-gated, reads the configured log file,
validates its query parameters."""

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.logs.routes import build_admin_router
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import AccountType, User


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_admin_router(SimpleNamespace()))
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id="admin", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )
    return TestClient(app)


class TestGetLogTail:

    def test_it_returns_the_tail_from_the_configured_log_file(self, tmp_path):
        path = tmp_path / "potionui.log"
        path.write_text("2026-09-09 10:00:00 |     INFO | is | hello\n")

        with patch("src.features.logs.routes.log_directory", return_value=tmp_path), \
             patch("src.features.logs.routes.LOG_FILE_NAME", "potionui.log"):
            response = _client().get("/api/admin/logs/tail")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["file"] == str(path)
        assert body["data"]["lines"] == [
            {"ts": "2026-09-09 10:00:00", "level": "INFO", "logger": "is", "message": "hello"}
        ]

    def test_file_logging_off_comes_back_with_no_file(self):
        with patch("src.features.logs.routes.log_directory", return_value=None):
            response = _client().get("/api/admin/logs/tail")

        assert response.status_code == 200
        body = response.json()["data"]
        assert body == {"lines": [], "truncated": False, "file": None, "size_bytes": 0}

    def test_lines_passes_through_to_the_tail_reader(self, tmp_path):
        path = tmp_path / "potionui.log"
        path.write_text("\n".join(
            f"2026-09-09 10:00:{i:02d} |     INFO | is | line {i}" for i in range(10)
        ) + "\n")

        with patch("src.features.logs.routes.log_directory", return_value=tmp_path), \
             patch("src.features.logs.routes.LOG_FILE_NAME", "potionui.log"):
            response = _client().get("/api/admin/logs/tail", params={"lines": 3})

        assert len(response.json()["data"]["lines"]) == 3

    def test_level_passes_through_to_the_tail_reader(self, tmp_path):
        path = tmp_path / "potionui.log"
        path.write_text(
            "2026-09-09 10:00:00 |     INFO | is | i\n"
            "2026-09-09 10:00:01 |  WARNING | is | w\n"
        )

        with patch("src.features.logs.routes.log_directory", return_value=tmp_path), \
             patch("src.features.logs.routes.LOG_FILE_NAME", "potionui.log"):
            response = _client().get("/api/admin/logs/tail", params={"level": "WARNING"})

        lines = response.json()["data"]["lines"]
        assert [line["level"] for line in lines] == ["WARNING"]

    def test_lines_below_one_is_rejected_with_422(self):
        response = _client().get("/api/admin/logs/tail", params={"lines": 0})

        assert response.status_code == 422

    def test_lines_above_the_cap_is_rejected_with_422(self):
        response = _client().get("/api/admin/logs/tail", params={"lines": 5001})

        assert response.status_code == 422

    def test_an_unrecognized_level_is_rejected_with_422(self):
        response = _client().get("/api/admin/logs/tail", params={"level": "NOT_A_LEVEL"})

        assert response.status_code == 422


class TestAdminOnly:

    def test_the_endpoint_sits_behind_the_admin_dependency(self):
        app = FastAPI()
        app.include_router(build_admin_router(SimpleNamespace()))

        paths = {route.path for route in app.routes if getattr(route, "path", "").startswith("/api/admin")}
        assert paths == {"/api/admin/logs/tail"}
        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/admin/logs"):
                assert get_current_admin_user in [d.call for d in route.dependant.dependencies]
