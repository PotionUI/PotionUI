from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.features.generation.grids.routes import build_router
from src.features.plans.errors import LimitExceeded
from src.platform.security.current_user import get_current_active_user
from tests.features.generation.grids.conftest import axis, base_request

U1 = {"x-user": "u1"}
U2 = {"x-user": "u2"}
ADMIN = {"x-user": "admin"}


@pytest.fixture
def client(harness):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(_generation_grid_service=harness.service)))

    def current_user(request: Request):
        name = request.headers.get("x-user")
        if name not in harness.users:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return harness.users[name]

    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app)


def payload(x=("a", "b"), y=(10, 20), **extra):
    return {
        "request": base_request().model_dump(),
        "x_axis": {"field": "sampler", "type": "select", "label": "Sampler", "values": [{"value": v, "label": str(v)} for v in x]},
        "y_axis": {"field": "steps", "type": "number", "label": "Steps", "values": [{"value": v, "label": str(v)} for v in y]}
        if y is not None
        else None,
        "lock_seed": True,
        **extra,
    }


def test_post_returns_the_grid_in_the_standard_envelope(client):
    response = client.post("/api/generations/grids", json=payload(), headers=U1)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    grid = body["data"]
    assert set(grid) == {"id", "preset_id", "tab_id", "x_axis", "y_axis", "lock_seed", "status", "created_at", "cells"}
    assert grid["created_at"].endswith("+00:00")
    assert set(grid["cells"][0]) == {
        "x", "y", "generation_id", "status", "axis_values", "seed", "thumbnail_url", "media_type", "error", "elapsed_seconds",
    }


def test_get_is_owner_or_admin_and_everyone_else_gets_404(client):
    grid_id = client.post("/api/generations/grids", json=payload(), headers=U1).json()["data"]["id"]

    assert client.get(f"/api/generations/grids/{grid_id}", headers=U1).status_code == 200
    assert client.get(f"/api/generations/grids/{grid_id}", headers=ADMIN).status_code == 200
    stranger = client.get(f"/api/generations/grids/{grid_id}", headers=U2)
    assert stranger.status_code == 404
    assert stranger.json()["detail"]["error"] == "grid_not_found"
    assert client.get(f"/api/generations/grids/{grid_id}").status_code == 401


def test_settings_route_is_not_shadowed_by_the_id_route(client, harness):
    harness.settings_store["compare_confirm_above"] = 30

    response = client.get("/api/generations/grids/settings", headers=U2)

    assert response.status_code == 200
    assert response.json()["data"] == {"confirm_above": 30, "hard_cap": 100}


def test_over_the_hard_cap_is_a_422_grid_too_large(client, harness):
    response = client.post(
        "/api/generations/grids", json=payload(x=range(11), y=range(10)), headers=U1
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "grid_too_large"
    assert harness.rows("generation_grids") == []


def test_a_plans_shortfall_is_the_plans_refusal_with_needed_and_remaining(client, harness):
    harness.guard.refusal = LimitExceeded(
        [{"kind": "generations_per_day", "code": "daily_generations_exceeded", "label": "Daily", "format": "count",
          "used": 7, "limit": 12, "incoming": 4, "needed": 4, "remaining": 5, "percent": 58.0, "resets_at": None,
          "message": "4 needed, 5 left today"}],
        "submit",
        "Ask your admin.",
    )

    response = client.post("/api/generations/grids", json=payload(), headers=U1)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "limit_exceeded"
    assert detail["code"] == "daily_generations_exceeded"
    assert (detail["needed"], detail["remaining"]) == (4, 5)
    assert harness.rows("generations") == []
    assert harness.rows("generation_grids") == []


def test_retry_failed_route(client, harness):
    grid_id = client.post("/api/generations/grids", json=payload(), headers=U1).json()["data"]["id"]
    harness.set_status("gen-000", "failed")

    assert client.post(f"/api/generations/grids/{grid_id}/retry-failed", headers=U2).status_code == 404
    response = client.post(f"/api/generations/grids/{grid_id}/retry-failed", headers=U1)

    assert response.status_code == 200
    assert response.json()["data"]["cells"][0]["status"] == "queued"
    assert len(harness.submitted) == 5


def test_delete_route_removes_the_grid_and_its_cells(client, harness):
    grid_id = client.post("/api/generations/grids", json=payload(), headers=U1).json()["data"]["id"]

    assert client.delete(f"/api/generations/grids/{grid_id}", headers=U2).status_code == 404
    assert client.delete(f"/api/generations/grids/{grid_id}", headers=U1).status_code == 200

    assert client.get(f"/api/generations/grids/{grid_id}", headers=U1).status_code == 404
    assert harness.rows("generations") == []


def test_the_grid_router_is_registered_before_the_generation_router():
    import inspect

    from src.bootstrap import routers

    source = inspect.getsource(routers.register_routers)

    assert source.index("build_generation_grids_router") < source.index("build_generation_router(container)")
