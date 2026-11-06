from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.features.organize.routes import OrganizeController, build_admin_router, build_router
from src.platform.security.current_user import get_current_active_user
from tests.features.organize.conftest import collection_action, rule_body

BASE = "/api/admin/organize/instance"


@pytest.fixture
def client(world, seed):
    users = {"root": world.admin_user, "u1": world.user("u1"), "u2": seed.user("u2")}
    controller = OrganizeController(world.manager, world.backfill, world.admin_ops)
    app = FastAPI()
    container = SimpleNamespace(organize_controller=controller)
    app.include_router(build_router(container))
    app.include_router(build_admin_router(container))
    app.dependency_overrides[get_current_active_user] = _resolver(users)
    return TestClient(app)


def _resolver(users):
    def current_user(request: Request):
        return users[request.headers.get("x-user", "u1")]

    return current_user


ADMIN = {"x-user": "root"}


def instance_body(group_id, **extra):
    return rule_body(
        subject="model", name=extra.pop("name", "Krea"),
        conditions=[{"fact": "name", "operator": "contains", "value": "krea2", "ignore_separators": True}],
        actions=[{"action": "assign_model_group", "config": {"group_id": group_id}}], **extra,
    )


def make_group(world, name="krea-2"):
    from src.features.user_groups.repository import UserGroupRepository

    return UserGroupRepository().create_group(name).id


ROUTES = [
    ("get", "/catalog", None),
    ("get", "/templates", None),
    ("get", "/rules", None),
    ("post", "/rules", {"name": "x"}),
    ("post", "/rules/reorder", {"subject": "model", "rule_ids": []}),
    ("get", "/rules/abc", None),
    ("put", "/rules/abc", {"name": "x"}),
    ("patch", "/rules/abc", {"enabled": False}),
    ("delete", "/rules/abc", None),
    ("post", "/rules/abc/duplicate", None),
    ("post", "/rules/abc/apply-existing", None),
    ("post", "/preview", {"subject": "model"}),
    ("post", "/preview/matches", {"subject": "model"}),
    ("get", "/jobs", None),
    ("get", "/jobs/abc", None),
    ("post", "/jobs/abc/cancel", None),
    ("get", "/activity", None),
    ("get", "/runs/abc/items", None),
    ("post", "/runs/abc/retry", None),
    ("post", "/runs/abc/undo", None),
    ("get", "/facts/name/options", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_instance_route_refuses_a_non_admin(client, method, path, body):
    response = getattr(client, method)(BASE + path, headers={"x-user": "u1"}, **({"json": body} if body is not None else {}))

    assert response.status_code == 403, response.text


def test_instance_controls_route_refuses_a_non_admin(client):
    assert client.put(f"{BASE}/controls", json={"paused": True}, headers={"x-user": "u1"}).status_code == 403


def test_an_admin_creates_lists_edits_and_deletes_instance_rules(client, world):
    group = make_group(world)

    created = client.post(f"{BASE}/rules", json=instance_body(group), headers=ADMIN)
    rule = created.json()["data"]
    listed = client.get(f"{BASE}/rules", headers=ADMIN).json()["data"]
    fetched = client.get(f"{BASE}/rules/{rule['id']}", headers=ADMIN).json()["data"]
    put = client.put(f"{BASE}/rules/{rule['id']}", json={"name": "Renamed"}, headers=ADMIN).json()["data"]
    patched = client.patch(f"{BASE}/rules/{rule['id']}", json={"enabled": False}, headers=ADMIN).json()["data"]
    copy = client.post(f"{BASE}/rules/{rule['id']}/duplicate", headers=ADMIN).json()["data"]
    deleted = client.delete(f"{BASE}/rules/{rule['id']}", headers=ADMIN)

    assert created.status_code == 200
    assert (rule["scope"], rule["created_by"], rule["subject"]) == ("instance", "root", "model")
    assert [r["id"] for r in listed] == [rule["id"]]
    assert fetched["targets"]["groups"] == [{"id": group, "name": "krea-2", "exists": True}]
    assert (put["name"], put["updated_by_name"]) == ("Renamed", "root")
    assert patched["status"] == "off"
    assert copy["name"].endswith("(copy)") and copy["enabled"] is False
    assert deleted.json()["data"] == {"deleted": True}


def test_user_routes_never_see_instance_rules_and_instance_routes_never_see_user_rules(client, world):
    group = make_group(world)
    instance_rule = client.post(f"{BASE}/rules", json=instance_body(group), headers=ADMIN).json()["data"]
    mine = client.post("/api/organize/rules", json=rule_body(actions=[collection_action(name="Mine")]),
                       headers=ADMIN).json()["data"]

    user_list = client.get("/api/organize/rules", headers=ADMIN).json()["data"]
    instance_list = client.get(f"{BASE}/rules", headers=ADMIN).json()["data"]
    hidden = client.get(f"/api/organize/rules/{instance_rule['id']}", headers=ADMIN)
    hidden_delete = client.delete(f"/api/organize/rules/{instance_rule['id']}", headers=ADMIN)
    hidden_run = client.post(f"/api/organize/rules/{instance_rule['id']}/apply-existing", headers=ADMIN)
    other_way = client.get(f"{BASE}/rules/{mine['id']}", headers=ADMIN)

    assert [r["id"] for r in user_list] == [mine["id"]]
    assert [r["id"] for r in instance_list] == [instance_rule["id"]]
    assert hidden.status_code == hidden_delete.status_code == hidden_run.status_code == other_way.status_code == 404
    assert client.get(f"{BASE}/rules/{instance_rule['id']}", headers=ADMIN).status_code == 200


def test_user_routes_refuse_instance_actions_and_the_catalogs_are_disjoint(client, world):
    group = make_group(world)

    refused = client.post(
        "/api/organize/rules", json=rule_body(subject="model", actions=[
            {"action": "assign_model_group", "config": {"group_id": group}}
        ]), headers=ADMIN,
    )
    user_catalog = client.get("/api/organize/catalog", params={"subject": "model"}, headers=ADMIN).json()["data"]
    instance_catalog = client.get(f"{BASE}/catalog", headers=ADMIN).json()["data"]

    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"][0]["code"] == "action_scope"
    assert {a["key"] for a in user_catalog["actions"]}.isdisjoint({a["key"] for a in instance_catalog["actions"]})
    assert {a["key"] for a in instance_catalog["actions"]} >= {"assign_model_group", "add_model_tags", "set_model_type", "test.fetch"}


def test_instance_activity_and_jobs_do_not_leak_to_users(client, world, seed):
    group = make_group(world)
    seed.model("krea2.safetensors")
    rule = client.post(f"{BASE}/rules", json=instance_body(group), headers=ADMIN).json()["data"]

    job = client.post(f"{BASE}/rules/{rule['id']}/apply-existing", headers=ADMIN).json()["data"]
    activity = client.get(f"{BASE}/activity", headers=ADMIN).json()["data"]["runs"]
    items = client.get(f"{BASE}/runs/{activity[0]['id']}/items", params={"status": "updated"}, headers=ADMIN).json()["data"]
    as_user = client.get("/api/organize/activity", headers=ADMIN).json()["data"]["runs"]
    user_jobs = client.get("/api/organize/jobs", headers=ADMIN).json()["data"]
    user_items = client.get(f"/api/organize/runs/{activity[0]['id']}/items", headers=ADMIN)
    user_undo = client.post(f"/api/organize/runs/{activity[0]['id']}/undo", headers=ADMIN)

    assert job["scope"] == "instance" and activity[0]["started_by"] == "root"
    assert items["total"] == 1 and items["items"][0]["name"] == "krea2"
    assert as_user == [] and user_jobs == []
    assert user_items.status_code == user_undo.status_code == 404
    assert [j["id"] for j in client.get(f"{BASE}/jobs", headers=ADMIN).json()["data"]] == [job["id"]]


def test_preview_matches_route_pages(client, world, seed):
    group = make_group(world)
    for index in range(3):
        seed.model(f"krea2-{index}.safetensors")
    body = {
        "subject": "model",
        "conditions": [{"fact": "name", "operator": "contains", "value": "krea2"}],
        "actions": [{"action": "assign_model_group", "config": {"group_id": group}}],
        "page": 2, "page_size": 2,
    }

    data = client.post(f"{BASE}/preview/matches", json=body, headers=ADMIN).json()["data"]
    bad = client.post(f"{BASE}/preview/matches", json={**body, "page_size": 500}, headers=ADMIN)

    assert (data["total"], data["page"], len(data["items"])) == (3, 2, 1)
    assert bad.status_code == 422


def test_retry_route_and_undo_route(client, world, seed):
    group = make_group(world)
    model = seed.model("krea2.safetensors")
    rule = client.post(f"{BASE}/rules", json=instance_body(group), headers=ADMIN).json()["data"]
    client.post(f"{BASE}/rules/{rule['id']}/apply-existing", headers=ADMIN)
    run = client.get(f"{BASE}/activity", headers=ADMIN).json()["data"]["runs"][0]

    nothing = client.post(f"{BASE}/runs/{run['id']}/retry", headers=ADMIN)
    undone = client.post(f"{BASE}/runs/{run['id']}/undo", headers=ADMIN).json()["data"]

    assert nothing.status_code == 409 and nothing.json()["detail"]["error"] == "nothing_waiting"
    assert undone["items"] == 1
    assert seed.rows("SELECT * FROM user_group_models WHERE model_id = ?", (model,)) == []


def test_instance_templates_route(client):
    templates = client.get(f"{BASE}/templates", params={"subject": "model"}, headers=ADMIN).json()["data"]
    user_templates = client.get("/api/organize/templates", params={"subject": "model"}, headers=ADMIN).json()["data"]

    assert [t["id"] for t in templates] == ["instance-name-to-group"]
    assert [t["id"] for t in user_templates] == ["loras"]


def test_instance_controls_route_and_overview(client, world):
    group = make_group(world)
    client.post(f"{BASE}/rules", json=instance_body(group), headers=ADMIN)

    controls = client.put(f"{BASE}/controls", json={"rule_cap": 5}, headers=ADMIN).json()["data"]
    overview = client.get("/api/admin/organize/overview", headers=ADMIN).json()["data"]

    assert controls["effective_rule_cap"] == 5
    assert overview["instance"]["rules"] == 1


def test_facts_options_route_serves_the_instance_catalog(client):
    options = client.get(f"{BASE}/facts/model_type/options", params={"subject": "model"}, headers=ADMIN)

    assert options.status_code == 200
