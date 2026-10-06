from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.routing import Match

from src.bootstrap.routers import register_routers
from src.features.filters.catalog import FilterCatalog
from src.features.filters.routes import FilterController, build_router
from src.features.filters.schema import FILTER_JSON_SCHEMA
from src.platform.security.current_user import get_current_active_user
from tests.features.filters.conftest import CUBE_2, make_user, plugin, registry, write_filter

STEPS = [{"op": "tone", "contrast": 12}, {"op": "vignette", "amount": 20}]


def build_client(collaborators, catalog=None):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(filter_controller=FilterController(collaborators))))

    def current_user(request: Request):
        return make_user(request.headers.get("x-user", "user-1"))

    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app)


@pytest.fixture
def client(collaborators):
    return build_client(collaborators)


def other():
    return {"x-user": "user-2"}


def create(client, headers=None, **overrides):
    body = {"name": "My Ember", "steps": STEPS}
    body.update(overrides)
    response = client.post("/api/filters/mine", json=body, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_create_returns_the_item_in_the_list_shape(client):
    item = create(client, description="warm", intensity=70)

    assert item["id"].startswith("mine:")
    assert item["source"] == "mine"
    assert item["owned"] is True
    assert (item["name"], item["description"], item["intensity"], item["group"]) == ("My Ember", "warm", 70, "Mine")
    assert item["steps"] == STEPS
    assert item["kinds"] == {"colour": 1, "spatial": 1}
    assert item["has_lut"] is False and item["lut_url"] is None
    assert item["unavailable_ops"] == [] and item["needs_plugin"] is None and item["backend_ok"] is True
    assert len(item["revision"]) == 8
    assert item["created_at"].endswith("+00:00")


def test_patch_renames_and_updates(client):
    item = create(client)

    response = client.patch(f"/api/filters/mine/{item['id']}", json={"name": "Renamed", "intensity": 30})

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["name"], data["intensity"], data["id"]) == ("Renamed", 30, item["id"])
    assert data["revision"] != item["revision"]


def test_patch_accepts_the_bare_ulid_as_well(client):
    item = create(client)

    response = client.patch(f"/api/filters/mine/{item['id'].split(':')[1]}", json={"name": "Bare"})

    assert response.json()["data"]["name"] == "Bare"


def test_delete_removes_the_filter(client):
    item = create(client)

    assert client.delete(f"/api/filters/mine/{item['id']}").status_code == 200
    assert client.get("/api/filters/mine").json()["data"] == []


def test_a_foreign_id_is_404_for_patch_and_delete(client):
    theirs = create(client, headers=other())

    patched = client.patch(f"/api/filters/mine/{theirs['id']}", json={"name": "Taken over"})
    deleted = client.delete(f"/api/filters/mine/{theirs['id']}")

    assert patched.status_code == 404 and deleted.status_code == 404
    assert patched.json()["detail"]["error"] == "filter_not_found"
    assert client.get("/api/filters/mine", headers=other()).json()["data"][0]["name"] == "My Ember"


def test_duplicate_name_is_409_regardless_of_case(client):
    create(client)

    response = client.post("/api/filters/mine", json={"name": "my ember", "steps": STEPS})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "filter_name_taken"


def test_the_101st_filter_is_409(client, collaborators):
    from src.features.filters.dto import MAX_USER_FILTERS
    from src.features.filters.records import UserFilter

    for index in range(MAX_USER_FILTERS):
        collaborators.repository.create(UserFilter(id="", owner_id="user-1", name=f"f{index}", steps=STEPS), MAX_USER_FILTERS)

    response = client.post("/api/filters/mine", json={"name": "more", "steps": STEPS})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "filter_limit_reached"


def test_invalid_steps_are_422_with_the_reason(client):
    response = client.post("/api/filters/mine", json={"name": "Bad", "steps": [{"op": "tone", "contrast": 999}]})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "filter_invalid"
    assert "steps[0].contrast" in response.json()["detail"]["message"]


def test_a_lut_filter_cannot_be_saved(client):
    response = client.post("/api/filters/mine", json={"name": "L", "steps": STEPS, "has_lut": True})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "filter_lut_unsupported"
    assert "LUT filters can't be copied yet" in response.json()["detail"]["message"]


def test_body_validation_rejects_bad_intensity_and_missing_fields(client):
    assert client.post("/api/filters/mine", json={"name": "x", "steps": STEPS, "intensity": 101}).status_code == 422
    assert client.post("/api/filters/mine", json={"steps": STEPS}).status_code == 422
    assert client.post("/api/filters/mine", json={"name": "   ", "steps": STEPS}).status_code == 422


def test_mine_is_not_swallowed_by_the_id_route(client):
    response = client.get("/api/filters/mine")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_the_router_is_registered_with_mine_before_the_lut_route():
    app = FastAPI()
    register_routers(app, MagicMock())

    def first_match(method, path):
        scope = {"type": "http", "method": method, "path": path, "root_path": ""}
        return next(route for route in app.routes if route.matches(scope)[0] == Match.FULL)

    assert first_match("GET", "/api/filters/mine").path == "/api/filters/mine"
    assert first_match("GET", "/api/filters").path == "/api/filters"
    assert first_match("GET", "/api/filters/ember/lut").path == "/api/filters/{filter_id}/lut"
    assert first_match("PATCH", "/api/filters/mine/abc").path == "/api/filters/mine/{filter_id}"


def test_schema_endpoint_serves_the_json_schema(client):
    response = client.get("/api/filters/schema")

    assert response.status_code == 200
    assert response.json() == FILTER_JSON_SCHEMA
    assert response.json()["required"] == ["schema", "id", "name", "steps"]


class TestLut:
    @pytest.fixture
    def lut_client(self, collaborators, filters_dir, tmp_path):
        write_filter(filters_dir / "local", "cubic", cube=CUBE_2, license="MIT")
        write_filter(filters_dir / "local", "plain")
        plugin_dir = tmp_path / "plug"
        write_filter(plugin_dir / "filters", "tinted", cube=CUBE_2, license="CC0")
        catalog = FilterCatalog(str(filters_dir), registry([plugin("demo-pack", plugin_dir, ["filters"])]))
        collaborators_with_plugin = type(collaborators)(repository=collaborators.repository, catalog=catalog)
        return build_client(collaborators_with_plugin)

    def test_serves_the_raw_cube_with_an_etag(self, lut_client):
        response = lut_client.get("/api/filters/cubic/lut")

        assert response.status_code == 200
        assert response.text == CUBE_2
        assert response.headers["etag"].startswith('"') and len(response.headers["etag"]) == 10

    def test_revalidates_with_if_none_match(self, lut_client):
        etag = lut_client.get("/api/filters/cubic/lut").headers["etag"]

        response = lut_client.get("/api/filters/cubic/lut", headers={"if-none-match": etag})

        assert response.status_code == 304
        assert lut_client.get("/api/filters/cubic/lut", headers={"if-none-match": '"stale"'}).status_code == 200

    def test_plugin_filter_ids_with_a_colon_resolve(self, lut_client):
        response = lut_client.get("/api/filters/demo-pack:tinted/lut")

        assert response.status_code == 200
        assert response.text == CUBE_2

    def test_listed_lut_url_is_fetchable(self, lut_client):
        item = next(f for f in lut_client.get("/api/filters").json()["filters"] if f["id"] == "demo-pack:tinted")

        assert lut_client.get(item["lut_url"]).status_code == 200

    @pytest.mark.parametrize("filter_id", ["plain", "nope", "mine:abc", "..%2F..%2Fetc%2Fpasswd"])
    def test_everything_without_a_lut_is_404(self, lut_client, filter_id):
        response = lut_client.get(f"/api/filters/{filter_id}/lut")

        assert response.status_code == 404
