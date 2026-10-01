from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.features.formulas.routes import FormulaController, build_router
from src.platform.security.current_user import get_current_active_user
from tests.features.formulas.conftest import make_user


@pytest.fixture
def client(collaborators):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(formula_controller=FormulaController(collaborators))))

    def current_user(request: Request):
        return make_user(request.headers.get("x-user", "user-1"))

    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app)


def body(**overrides):
    payload = {
        "preset_id": "preset-a",
        "mode": "video",
        "name": "Turbo",
        "groups": [{"id": "speed"}],
        "values": {"speed_profile": "turbo", "steps": 4, "prompt": "never"},
    }
    payload.update(overrides)
    return payload


def other(user_id="user-2"):
    return {"x-user": user_id}


def create(client, headers=None, **overrides):
    response = client.post("/api/formulas", json=body(**overrides), headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_create_returns_the_stored_formula(client):
    data = create(client)

    assert data["values"] == {"speed_profile": "turbo", "steps": 4}
    assert data["groups"][0]["label"] == "Speed and sampling"
    assert data["signatures"]["steps"]["max"] == 100
    assert data["created_at"].endswith("+00:00")
    assert "owner_id" not in data


def test_list_returns_only_the_callers_formulas_for_the_scope(client):
    mine = create(client)
    create(client, headers=other(), name="Theirs")

    response = client.get("/api/formulas", params={"preset_id": "preset-a", "mode": "video"})

    assert [item["id"] for item in response.json()["data"]] == [mine["id"]]


def test_list_requires_preset_and_mode(client):
    assert client.get("/api/formulas", params={"preset_id": "preset-a"}).status_code == 422


def test_update_renames(client):
    formula = create(client)

    response = client.put(f"/api/formulas/{formula['id']}", json={"name": "Fast", "note": "n"})

    assert response.json()["data"]["name"] == "Fast"
    assert response.json()["data"]["note"] == "n"


def test_update_replaces_content(client):
    formula = create(client)
    content = {"groups": [{"id": "size"}], "values": {"resolution": "832x480"}}

    response = client.put(f"/api/formulas/{formula['id']}", json={"content": content})

    assert response.json()["data"]["values"] == {"resolution": "832x480"}


def test_duplicate_adds_a_copy(client):
    formula = create(client)

    response = client.post(f"/api/formulas/{formula['id']}/duplicate")

    assert response.json()["data"]["name"] == "Turbo copy"


def test_delete_removes_the_formula(client):
    formula = create(client)

    assert client.delete(f"/api/formulas/{formula['id']}").status_code == 200
    assert client.delete(f"/api/formulas/{formula['id']}").status_code == 404


def test_plan_returns_changes_same_and_skips(client):
    formula = create(client)

    response = client.post(
        f"/api/formulas/{formula['id']}/plan",
        json={"form_name": None, "current_values": {"steps": 4}, "lora_mode": "replace"},
    )

    data = response.json()["data"]
    assert [item["name"] for item in data["changes"]] == ["speed_profile"]
    assert [item["name"] for item in data["same"]] == ["steps"]
    assert data["skips"] == []


def test_name_conflict_answers_409(client):
    create(client)

    response = client.post("/api/formulas", json=body())

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "formula_name_exists"


def test_a_signature_mismatch_answers_422(client):
    response = client.post(
        "/api/formulas", json=body(signatures={"steps": {"type": "slider", "min": 0, "max": 9, "step": 1}})
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "signature_mismatch"


def test_oversized_values_are_refused_before_storage(client):
    response = client.post("/api/formulas", json=body(values={"steps": "x" * 70000}))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("put", "", {"name": "Stolen"}),
        ("post", "/duplicate", None),
        ("post", "/plan", {"form_name": None, "current_values": {}, "lora_mode": "replace"}),
        ("delete", "", None),
    ],
)
def test_another_users_formula_answers_404_on_every_route(client, method, path, payload):
    formula = create(client)

    response = getattr(client, method)(f"/api/formulas/{formula['id']}{path}", headers=other(), **({"json": payload} if payload else {}))

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "formula_not_found"
    listed = client.get("/api/formulas", params={"preset_id": "preset-a", "mode": "video"}).json()["data"]
    assert [item["name"] for item in listed] == ["Turbo"]


def test_a_missing_id_answers_404(client):
    assert client.delete("/api/formulas/nope").status_code == 404
