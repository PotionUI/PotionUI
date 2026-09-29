from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.content_safety.routes import build_router
from src.platform.security.current_user import get_current_admin_user
from tests.features.content_safety.fakes import build


@pytest.fixture
def manager():
    built, _, _ = build("blur", present=False)
    return built


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(content_safety=manager)))
    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id="admin")
    with TestClient(app) as test_client:
        yield test_client


def test_status_reports_policy_tagger_and_backfill(client):
    data = client.get("/api/content-safety/status").json()["data"]

    assert data["policy"] == "blur"
    assert data["tagger"] == {"present": False, "device": "cpu", "downloading": False}
    assert set(data["backfill"]) == {"total", "rated", "running"}


def test_backfill_does_not_start_while_the_tagger_is_missing(client):
    data = client.post("/api/content-safety/backfill").json()["data"]

    assert data["started"] is False


def test_the_status_route_is_admin_gated():
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(content_safety=SimpleNamespace())))
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/content-safety/status").status_code in (401, 403)
