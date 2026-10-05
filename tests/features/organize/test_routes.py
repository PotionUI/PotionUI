from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.features.organize.routes import OrganizeController, build_admin_router, build_router
from src.platform.security.current_user import get_current_active_user
from tests.features.organize.conftest import collection_action, rule_body


@pytest.fixture
def users(seed):
    return {
        "u1": seed.user("u1"),
        "u2": seed.user("u2"),
        "admin": seed.user("admin", admin=True),
    }


@pytest.fixture
def client(manager, users):
    app = FastAPI()
    container = SimpleNamespace(organize_controller=OrganizeController(manager))
    app.include_router(build_router(container))
    app.include_router(build_admin_router(container))

    def current_user(request: Request):
        return users[request.headers.get("x-user", "u1")]

    app.dependency_overrides[get_current_active_user] = current_user
    return TestClient(app)


def as_user(user_id):
    return {"x-user": user_id}


def create(client, headers=None, **overrides):
    response = client.post("/api/organize/rules", json=rule_body(**overrides), headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_create_and_list_rules(client, seed):
    collection = seed.collection("u1")
    rule = create(client, actions=[collection_action(collection)])

    listed = client.get("/api/organize/rules", params={"subject": "generation"}).json()["data"]

    assert [r["id"] for r in listed] == [rule["id"]]
    assert client.get("/api/organize/rules", headers=as_user("u2")).json()["data"] == []


def test_invalid_rules_answer_422_with_problems(client):
    response = client.post("/api/organize/rules", json=rule_body(actions=[]))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_rule"
    assert detail["problems"][0]["code"] == "no_actions"


def test_another_users_rule_answers_404(client):
    rule = create(client, actions=[collection_action(name="Mine")])

    response = client.get(f"/api/organize/rules/{rule['id']}", headers=as_user("u2"))
    deleted = client.delete(f"/api/organize/rules/{rule['id']}", headers=as_user("u2"))

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "rule_not_found"
    assert deleted.status_code == 404
    assert client.get(f"/api/organize/rules/{rule['id']}").status_code == 200


def test_patch_put_reorder_and_delete(client):
    a = create(client, name="a", actions=[collection_action(name="A")])
    b = create(client, name="b", actions=[collection_action(name="B")])

    patched = client.patch(f"/api/organize/rules/{a['id']}", json={"enabled": False}).json()["data"]
    put = client.put(f"/api/organize/rules/{b['id']}", json={"name": "bee", "stop_after": True}).json()["data"]
    order = client.post("/api/organize/rules/reorder",
                        json={"subject": "generation", "rule_ids": [b["id"], a["id"]]}).json()["data"]
    deleted = client.delete(f"/api/organize/rules/{a['id']}")

    assert patched["status"] == "off"
    assert (put["name"], put["stop_after"]) == ("bee", True)
    assert [r["name"] for r in order] == ["bee", "a"]
    assert deleted.json()["data"] == {"deleted": True}


def test_catalog_summary_templates_and_options(client):
    catalog = client.get("/api/organize/catalog", params={"subject": "model"}).json()["data"]
    summary = client.get("/api/organize/summary").json()["data"]
    templates = client.get("/api/organize/templates", params={"subject": "upload"}).json()["data"]
    options = client.get("/api/organize/facts/media_kind/options", params={"q": "vid"}).json()["data"]
    missing = client.get("/api/organize/facts/nope/options")

    assert {f["key"] for f in catalog["facts"]} == {"model_type", "base_model", "tags"}
    assert summary["subjects"]["generation"]["total"] == 0
    assert templates and all(t["subject"] == "upload" for t in templates)
    assert options == [{"value": "video", "label": "Video"}]
    assert missing.status_code == 404


def test_preview_apply_activity_undo_and_provenance(client, seed):
    generation = seed.generation("u1")
    rule = create(client, actions=[collection_action(name="Everything")])

    preview = client.post("/api/organize/preview", json={"subject": "generation", "conditions": [],
                                                         "rule_id": rule["id"]}).json()["data"]
    job = client.post(f"/api/organize/rules/{rule['id']}/apply-existing", json={}).json()["data"]
    jobs = client.get("/api/organize/jobs").json()["data"]
    activity = client.get("/api/organize/activity").json()["data"]
    provenance = client.get(f"/api/organize/provenance/generation/{generation}").json()["data"]
    undo = client.post(f"/api/organize/runs/{activity['runs'][0]['id']}/undo").json()["data"]

    assert preview["matched"] == 1
    assert job["rule_id"] == rule["id"]
    assert [j["status"] for j in jobs] == ["completed"]
    assert activity["runs"][0]["applied"] == 1
    assert provenance[0]["rule_name"] == "Krea landscapes"
    assert undo["undone"] == 1
    assert client.get(f"/api/organize/jobs/{job['id']}", headers=as_user("u2")).status_code == 404
    assert client.post(f"/api/organize/runs/{activity['runs'][0]['id']}/undo", headers=as_user("u2")).status_code == 404


def test_collection_echo_route(client, seed):
    collection = seed.collection("u1")
    rule = create(client, actions=[collection_action(collection)])

    echo = client.get(f"/api/organize/collections/history/{collection}/rules").json()["data"]
    theirs = client.get(f"/api/organize/collections/history/{collection}/rules", headers=as_user("u2")).json()["data"]

    assert echo == [{"id": rule["id"], "name": "Krea landscapes", "status": "active"}]
    assert theirs == []


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/admin/organize/overview", None),
    ("put", "/api/admin/organize/controls", {"paused_all": True}),
    ("put", "/api/admin/organize/users/u2", {"paused": True}),
])
def test_admin_routes_refuse_users(client, method, path, body):
    response = getattr(client, method)(path, **({"json": body} if body is not None else {}))

    assert response.status_code == 403


def test_admin_overview_has_counts_but_no_rule_contents(client, seed):
    create(client, name="Secret plans", actions=[collection_action(name="Hidden folder")],
           conditions=[{"fact": "prompt", "operator": "contains", "value": "private words"}])
    seed.generation("u1", prompt="private words")
    rule_id = client.get("/api/organize/rules").json()["data"][0]["id"]
    client.post(f"/api/organize/rules/{rule_id}/apply-existing", json={})

    response = client.get("/api/admin/organize/overview", headers=as_user("admin"))

    assert response.status_code == 200
    data = response.json()["data"]
    text = response.text
    assert "Secret plans" not in text and "Hidden folder" not in text and "private words" not in text
    row = next(u for u in data["users"] if u["user_id"] == "u1")
    assert (row["rules"], row["enabled_rules"], row["items_filed_total"], row["items_filed_24h"]) == (1, 1, 1, 1)
    assert data["totals"]["rules"] == 1
    assert data["default_rule_cap"] == 50


def test_admin_kill_switch_and_cap(client):
    killed = client.put("/api/admin/organize/controls", json={"paused_all": True}, headers=as_user("admin"))
    summary = client.get("/api/organize/summary").json()["data"]
    client.put("/api/admin/organize/controls", json={"paused_all": False}, headers=as_user("admin"))
    capped = client.put("/api/admin/organize/users/u1", json={"rule_cap": 0}, headers=as_user("admin"))
    refused = client.post("/api/organize/rules", json=rule_body(actions=[collection_action(name="A")]))
    unknown = client.put("/api/admin/organize/users/nobody", json={"paused": True}, headers=as_user("admin"))
    bad_cap = client.put("/api/admin/organize/controls", json={"default_rule_cap": -1}, headers=as_user("admin"))

    assert killed.json()["data"]["paused_all"] is True
    assert summary["paused_by_admin"] is True
    assert capped.json()["data"]["effective_rule_cap"] == 0
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "rule_cap_reached"
    assert unknown.status_code == 404
    assert bad_cap.status_code == 422
