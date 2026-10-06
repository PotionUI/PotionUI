from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.features.filters.catalog import FilterCatalog
from src.features.filters.collaborators import FilterCollaborators
from src.features.filters.routes import FilterController, build_router
from src.platform.security.current_user import get_current_active_user
from tests.features.filters.conftest import CUBE_2, make_user, plugin, registry, write_filter

STEPS = [{"op": "tone", "contrast": 12}, {"op": "vignette", "amount": 20}]
WARM_OP = {
    "id": "demo-pack.warmth",
    "label": "Warmth",
    "kind": "colour",
    "params": [{"id": "amount", "label": "Amount", "type": "int", "min": 0, "max": 100, "default": 0}],
    "python": "ops.py:Warmth",
}


def client_for(collaborators):
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(filter_controller=FilterController(collaborators))))

    def current_user(request: Request):
        return make_user(request.headers.get("x-user", "user-1"))

    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app)


@pytest.fixture
def populated(collaborators, filters_dir, tmp_path):
    write_filter(filters_dir / "marketplace", "ember", name="Ember", group="Colour", order=20, tags=["warm"])
    write_filter(filters_dir / "marketplace", "noir", name="Noir", group="Black & white", order=10)
    write_filter(filters_dir / "marketplace", "matte", name="Matte", group="Film", order=10)
    write_filter(filters_dir / "local", "ember", name="My Ember", group="Colour", order=20)
    write_filter(filters_dir / "local", "harbor", name="Harbor", group="Local", cube=CUBE_2, license="CC0", credit="by me")
    write_filter(filters_dir / "local", "broken", steps=[{"op": "nope"}])
    plugin_dir = tmp_path / "plug"
    write_filter(
        plugin_dir / "filters", "toasty", name="Toasty", group="Plugin",
        steps=[{"op": "demo-pack.warmth", "amount": 25}],
    )
    declaring = plugin("demo-pack", plugin_dir, ["filters"], [WARM_OP])
    catalog = FilterCatalog(str(filters_dir), registry([declaring]))
    built = FilterCollaborators(repository=collaborators.repository, catalog=catalog)
    return client_for(built), built


def create(client, headers=None, **overrides):
    body = {"name": "Mine One", "steps": STEPS}
    body.update(overrides)
    response = client.post("/api/filters/mine", json=body, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def listing(client, headers=None):
    response = client.get("/api/filters", headers=headers or {})
    assert response.status_code == 200
    return response.json()


def test_payload_has_the_documented_top_level_shape(populated):
    client, _ = populated

    body = listing(client)

    assert set(body) == {"schema", "filters", "ops", "groups", "load_errors"}
    assert body["schema"] == 1
    assert body["load_errors"] == {"local/broken": ["steps[0].op: unknown op 'nope'"]}


def test_every_item_carries_the_documented_fields(populated):
    client, _ = populated
    expected = {
        "id", "name", "description", "group", "order", "intensity", "tags", "source", "plugin_id", "overrides",
        "owned", "has_lut", "lut_size", "lut_url", "steps", "kinds", "unavailable_ops", "needs_plugin",
        "backend_ok", "revision",
    }
    create(client)

    for item in listing(client)["filters"]:
        assert expected <= set(item), item["id"]


def test_sources_ownership_and_overrides_are_tagged(populated):
    client, _ = populated
    mine = create(client)

    by_id = {f["id"]: f for f in listing(client)["filters"]}

    assert by_id["ember"]["source"] == "local" and by_id["ember"]["overrides"] is True
    assert by_id["noir"]["source"] == "builtin" and by_id["noir"]["overrides"] is False
    assert by_id["demo-pack:toasty"]["source"] == "plugin" and by_id["demo-pack:toasty"]["plugin_id"] == "demo-pack"
    assert by_id[mine["id"]]["source"] == "mine"
    assert {i: f["owned"] for i, f in by_id.items()} == {
        "ember": False, "noir": False, "matte": False, "harbor": False, "demo-pack:toasty": False, mine["id"]: True,
    }


def test_lut_fields(populated):
    client, _ = populated

    harbor = next(f for f in listing(client)["filters"] if f["id"] == "harbor")

    assert harbor["has_lut"] is True
    assert harbor["lut_size"] == 2
    assert harbor["lut_url"] == "/api/filters/harbor/lut"
    assert harbor["credit"] == "by me"


def test_other_users_filters_never_appear(populated):
    client, _ = populated
    create(client, name="Private to one")
    create(client, headers={"x-user": "user-2"}, name="Private to two")

    one = [f["name"] for f in listing(client)["filters"] if f["source"] == "mine"]
    two = [f["name"] for f in listing(client, {"x-user": "user-2"})["filters"] if f["source"] == "mine"]

    assert one == ["Private to one"]
    assert two == ["Private to two"]


def test_groups_follow_builtin_order_then_others_then_mine(populated):
    client, _ = populated
    create(client)

    assert listing(client)["groups"] == ["Colour", "Film", "Black & white", "Local", "Plugin", "Mine"]


def test_groups_without_mine_when_the_user_has_none(populated):
    client, _ = populated

    assert "Mine" not in listing(client)["groups"]


def test_items_are_sorted_by_group_then_mine_last_then_order_and_name(populated):
    client, _ = populated
    create(client, name="Aaa", group="Colour")

    colour = [f["id"] for f in listing(client)["filters"] if f["group"] == "Colour"]

    assert colour[0] == "ember"
    assert colour[-1].startswith("mine:")


def test_ops_lists_core_ops_then_enabled_plugin_ops(populated):
    client, _ = populated

    ops = listing(client)["ops"]

    core_ids = [o["id"] for o in ops if o["source"] == "core"]
    assert core_ids == sorted(core_ids) and len(core_ids) == 11
    assert [o["source"] for o in ops] == ["core"] * 11 + ["plugin"]
    assert ops[-1]["id"] == "demo-pack.warmth"
    assert ops[-1]["source"] == "plugin" and ops[-1]["plugin_id"] == "demo-pack"
    tone = next(o for o in ops if o["id"] == "tone")
    assert tone["kind"] == "colour"
    assert tone["params"][0] == {
        "id": "brightness", "label": "Brightness", "type": "int", "min": -100, "max": 100, "default": 0, "unit": None,
    }
    curves = next(o for o in ops if o["id"] == "curves")
    assert curves["params"][0]["type"] == "curve" and curves["params"][0]["default"] == [[0, 0], [1, 1]]


def test_plugin_filter_with_its_op_is_available(populated):
    client, _ = populated

    toasty = next(f for f in listing(client)["filters"] if f["id"] == "demo-pack:toasty")

    assert toasty["unavailable_ops"] == []
    assert toasty["backend_ok"] is True
    assert toasty["kinds"] == {"colour": 1, "spatial": 0}


def test_a_user_filter_whose_plugin_is_off_is_flagged_not_dropped(collaborators, filters_dir, tmp_path):
    declaring = plugin("demo-pack", tmp_path, filter_ops=[WARM_OP])
    on = FilterCollaborators(collaborators.repository, FilterCatalog(str(filters_dir), registry([declaring])))
    off = FilterCollaborators(collaborators.repository, FilterCatalog(str(filters_dir), registry([], [declaring])))
    created = create(client_for(on), name="Warm", steps=[{"op": "demo-pack.warmth", "amount": 30}])

    item = next(f for f in listing(client_for(off))["filters"] if f["id"] == created["id"])

    assert item["unavailable_ops"] == ["demo-pack.warmth"]
    assert item["needs_plugin"] == "demo-pack"
    assert item["backend_ok"] is False
    assert item["steps"] == [{"op": "demo-pack.warmth", "amount": 30}]
    assert "demo-pack.warmth" not in [o["id"] for o in listing(client_for(off))["ops"]]


def test_the_revision_tracks_the_steps_and_intensity(populated):
    client, _ = populated
    item = create(client)
    again = client.patch(f"/api/filters/mine/{item['id']}", json={"intensity": 10}).json()["data"]
    same = client.patch(f"/api/filters/mine/{item['id']}", json={"intensity": 10}).json()["data"]

    assert again["revision"] != item["revision"]
    assert same["revision"] == again["revision"]


def test_the_shipped_catalog_lists_twelve_builtins(collaborators):
    from tests.features.filters.conftest import REPO_ROOT

    real = FilterCollaborators(collaborators.repository, FilterCatalog(str(REPO_ROOT / "content" / "filters")))

    body = listing(client_for(real))

    assert len(body["filters"]) == 12
    assert body["groups"] == ["Colour", "Film", "Black & white"]
    assert all(f["source"] == "builtin" and f["backend_ok"] and not f["owned"] for f in body["filters"])
    assert body["load_errors"] == {}
