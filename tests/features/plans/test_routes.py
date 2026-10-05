from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.features.plans.routes import build_admin_router, build_router
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.security.current_user import get_current_user
from tests.features.plans.conftest import GB, make_plan


@pytest.fixture
def users(seed):
    return {name: seed.user(name, admin=name == "admin") for name in ("u1", "u2", "admin")}


def build_app(plans):
    app = FastAPI()
    container = SimpleNamespace(plans_controller=plans.controller)
    app.include_router(build_router(container))
    app.include_router(build_admin_router(container))
    return app


@pytest.fixture
def client(plans, users):
    app = build_app(plans)

    def current_user(request: Request):
        name = request.headers.get("x-user")
        if name not in users:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return users[name]

    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app)


ADMIN = {"x-user": "admin"}
USER = {"x-user": "u1"}

ADMIN_ROUTES = [
    ("get", "/api/admin/plans", None),
    ("post", "/api/admin/plans", {"name": "X", "limits": []}),
    ("get", "/api/admin/plans/kinds", None),
    ("get", "/api/admin/plans/settings", None),
    ("put", "/api/admin/plans/settings", {"exempt_admins": False}),
    ("get", "/api/admin/plans/groups", None),
    ("put", f"/api/admin/plans/groups/{ALL_USERS_GROUP_ID}", {"plan_id": None}),
    ("get", f"/api/admin/plans/groups/{ALL_USERS_GROUP_ID}/impact", None),
    ("get", "/api/admin/plans/users", None),
    ("get", "/api/admin/plans/users/u1", None),
    ("put", "/api/admin/plans/users/u1", {"plan_id": "unlimited"}),
    ("get", "/api/admin/plans/unlimited", None),
    ("put", "/api/admin/plans/unlimited", {"name": "Hacked"}),
    ("delete", "/api/admin/plans/unlimited", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_refuse_users_and_anonymous_callers(client, seed, method, path, body):
    for headers, status in (({}, 401), (USER, 403)):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == status, (path, headers, response.text)
    assert seed.rows("SELECT plan_id FROM users WHERE id = 'u1'")[0]["plan_id"] is None
    assert seed.rows("SELECT name FROM plans") == [{"name": "Unlimited"}]


def test_without_a_token_the_user_endpoint_answers_401(plans, users):
    response = TestClient(build_app(plans)).get("/api/me/limits")

    assert response.status_code == 401


def test_my_limits_is_empty_with_nothing_assigned(client):
    data = client.get("/api/me/limits", headers=USER).json()["data"]

    assert data == {
        "plan": None, "source": "none", "group": None, "exempt": False, "timezone": "UTC",
        "contact_line": "Ask your admin for more.", "payments": False, "limits": [],
        "usage": {"storage_bytes": 0},
    }


def test_my_limits_always_reports_storage_used(client, plans, seed):
    seed.generation("u1", sizes=(10, 20))
    seed.upload("u1", 7)
    seed.upload("admin", 5)

    assert client.get("/api/me/limits", headers=USER).json()["data"]["usage"] == {"storage_bytes": 37}

    daily = make_plan(plans, "Daily", generations_per_day=5)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, daily.id)
    data = client.get("/api/me/limits", headers=USER).json()["data"]
    assert [row["kind"] for row in data["limits"]] == ["generations_per_day"]
    assert data["usage"] == {"storage_bytes": 37}

    admin = client.get("/api/me/limits", headers=ADMIN).json()["data"]
    assert admin["exempt"] is True
    assert admin["usage"] == {"storage_bytes": 5}

    sized = make_plan(plans, "Sized", storage_bytes=100)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, sized.id)
    data = client.get("/api/me/limits", headers=USER).json()["data"]
    assert data["limits"][0]["used"] == data["usage"]["storage_bytes"] == 37


def test_the_file_limit_is_an_info_row_without_usage(client, plans, seed):
    files = make_plan(plans, "Files", upload_file_size=50 * 1024 ** 2, storage_bytes=100)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, files.id)
    seed.upload("u1", 90)

    rows = {row["kind"]: row for row in client.get("/api/me/limits", headers=USER).json()["data"]["limits"]}
    row = rows["upload_file_size"]
    assert (row["limit"], row["used"], row["remaining"], row["percent"], row["state"]) == (
        50 * 1024 ** 2, None, None, None, "ok",
    )
    assert row["kind_info"]["per_item"] is True
    assert rows["storage_bytes"]["kind_info"]["per_item"] is False

    users = {u["username"]: u for u in client.get("/api/admin/plans/users", headers=ADMIN).json()["data"]["users"]}
    assert users["u1"]["max_percent"] == 90.0
    detail = client.get("/api/admin/plans/users/u1", headers=ADMIN).json()["data"]
    assert {r["kind"]: r["detail"] for r in detail["limits"]}["upload_file_size"] == "Files (default plan)"
    bigger = make_plan(plans, "Bigger", upload_file_size=120 * 1024 ** 2)
    client.put("/api/admin/plans/users/u1", json={"plan_id": bigger.id}, headers=ADMIN)
    detail = client.get("/api/admin/plans/users/u1", headers=ADMIN).json()["data"]
    assert {r["kind"]: r["detail"] for r in detail["limits"]}["upload_file_size"] == (
        "Bigger (personal override): 120 MB; All users says 50 MB, smaller"
    )
    client.put("/api/admin/plans/users/u1", json={"plan_id": None}, headers=ADMIN)

    listing = client.get("/api/admin/plans", headers=ADMIN).json()["data"]
    usage = {p["name"]: p["usage"] for p in listing["plans"]}["Files"]
    assert [k["kind"] for k in usage["kinds"]] == ["storage_bytes"]


def test_my_limits_lists_limited_kinds_and_hides_cloud_dollars(client, plans, seed):
    free = make_plan(plans, "Free", storage_bytes=10 * GB, generations_per_day=20, cloud_spend_usd_month=50)
    client.put(f"/api/admin/plans/groups/{ALL_USERS_GROUP_ID}", json={"plan_id": free.id}, headers=ADMIN)
    seed.upload("u1", 9 * GB)
    seed.cost("u1", 25, plans.guard.clock())

    data = client.get("/api/me/limits", headers=USER).json()["data"]

    assert data["plan"] == {"id": free.id, "name": "Free"}
    assert data["source"] == "default"
    rows = {row["kind"]: row for row in data["limits"]}
    assert rows["storage_bytes"]["used"] == 9 * GB
    assert rows["storage_bytes"]["state"] == "warn"
    assert rows["storage_bytes"]["percent"] == 90.0
    assert rows["generations_per_day"]["resets_at"] == "2026-10-06T00:00:00+00:00"
    assert rows["cloud_spend_usd_month"]["format"] == "percent"
    assert rows["cloud_spend_usd_month"]["used"] is None
    assert rows["cloud_spend_usd_month"]["limit"] is None
    assert rows["cloud_spend_usd_month"]["percent"] == 50.0
    assert rows["storage_bytes"]["kind_info"]["enforce_at"] == ["submit", "upload"]
    assert rows["generations_per_day"]["kind_info"]["short_label"] == rows["generations_per_day"]["label"]


def test_storage_breakdown(client, seed):
    seed.generation("u1", sizes=(10, 20))
    seed.generation("u1", sizes=(5,), file_type="VIDEO")
    seed.upload("u1", 7)

    data = client.get("/api/me/limits/storage", headers=USER).json()["data"]

    assert data["total_bytes"] == 42
    assert [(g["key"], g["files"], g["bytes"]) for g in data["groups"]] == [
        ("image", 2, 30), ("video", 1, 5), ("upload", 1, 7),
    ]


def test_plan_crud_and_validation(client):
    created = client.post("/api/admin/plans", json={
        "name": "Tier 1", "description": "First", "limits": [{"kind": "storage_bytes", "value": 20 * GB}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
    plan = created.json()["data"]
    assert plan["limits"] == [{"kind": "storage_bytes", "value": 20 * GB, "active": True}]

    duplicate = client.post("/api/admin/plans", json={"name": "tier 1"}, headers=ADMIN)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error"] == "plan_name_taken"

    invalid = client.post("/api/admin/plans", json={"name": "Bad", "limits": [
        {"kind": "nope", "value": 1},
        {"kind": "generations_per_day", "value": 1.5},
        {"kind": "storage_bytes", "value": -1},
    ]}, headers=ADMIN)
    assert invalid.status_code == 422
    assert [p["code"] for p in invalid.json()["detail"]["problems"]] == ["unknown_kind", "invalid_value", "invalid_value"]

    updated = client.put(f"/api/admin/plans/{plan['id']}", json={
        "name": "Tier 1", "limits": [{"kind": "generations_per_day", "value": 100}],
    }, headers=ADMIN).json()["data"]
    assert [limit["kind"] for limit in updated["limits"]] == ["generations_per_day"]

    system = client.put("/api/admin/plans/unlimited", json={"name": "U"}, headers=ADMIN)
    assert system.status_code == 409
    assert system.json()["detail"]["error"] == "plan_is_system"


def test_delete_is_refused_while_assigned_and_reassigns_on_request(client, plans, seed):
    tier = make_plan(plans, "Tier", storage_bytes=1)
    other = make_plan(plans, "Other", storage_bytes=2)
    group_id = seed.group("premium")
    client.put(f"/api/admin/plans/groups/{group_id}", json={"plan_id": tier.id}, headers=ADMIN)
    client.put("/api/admin/plans/users/u2", json={"plan_id": tier.id}, headers=ADMIN)

    refused = client.delete(f"/api/admin/plans/{tier.id}", headers=ADMIN)
    assert refused.status_code == 409
    assert refused.json()["detail"]["assigned_to"] == {
        "groups": [{"id": group_id, "name": "premium"}], "users": [{"id": "u2", "username": "u2"}],
    }

    deleted = client.delete(f"/api/admin/plans/{tier.id}?reassign_to={other.id}", headers=ADMIN).json()["data"]
    assert deleted == {"deleted": True, "reassigned": {"groups": 1, "users": 1}}
    assert seed.rows("SELECT plan_id FROM users WHERE id = 'u2'")[0]["plan_id"] == other.id
    assert client.get(f"/api/admin/plans/{tier.id}", headers=ADMIN).status_code == 404


def test_settings_round_trip_and_validation(client, plans):
    free = make_plan(plans, "Free", storage_bytes=1)

    data = client.put("/api/admin/plans/settings", json={
        "default_plan_id": free.id, "exempt_admins": False, "day_timezone": "Europe/Warsaw",
        "contact_line": "Mail ops@example.test",
    }, headers=ADMIN).json()["data"]
    assert data == {
        "default_plan_id": free.id, "exempt_admins": False, "day_timezone": "Europe/Warsaw",
        "contact_line": "Mail ops@example.test",
    }

    bad = client.put("/api/admin/plans/settings", json={"day_timezone": "Mars/Base"}, headers=ADMIN)
    assert bad.status_code == 422
    assert bad.json()["detail"]["problems"][0]["field"] == "day_timezone"

    missing = client.put("/api/admin/plans/settings", json={"default_plan_id": "nope"}, headers=ADMIN)
    assert missing.status_code == 404


def test_impact_preview_explains_each_member_per_kind(client, plans, seed):
    free = make_plan(plans, "Free", storage_bytes=5 * GB, generations_per_day=20)
    tier1 = make_plan(plans, "Tier 1", storage_bytes=20 * GB, generations_per_day=100)
    tier2 = make_plan(plans, "Tier 2", storage_bytes=100 * GB, generations_per_day=500)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, free.id)
    first = seed.group("premium-tier-1")
    second = seed.group("premium-tier-2")
    for user_id in ("u1", "u2"):
        seed.join(first, user_id)
    seed.join(second, "u2")
    plans.guard.plans.set_group_plan(second, tier2.id)
    seed.upload("u1", 6 * GB)

    data = client.get(f"/api/admin/plans/groups/{first}/impact?plan_id={tier1.id}", headers=ADMIN).json()["data"]

    members = {m["username"]: {row["kind"]: row for row in m["limits"]} for m in data["members"]}
    assert members["u1"]["storage_bytes"]["before"]["value"] == 5 * GB
    assert members["u1"]["storage_bytes"]["after"]["value"] == 20 * GB
    assert members["u1"]["storage_bytes"]["change"] == "raised"
    assert members["u1"]["storage_bytes"]["note"] == "from_this_group"
    assert members["u2"]["generations_per_day"]["change"] == "unchanged"
    assert members["u2"]["generations_per_day"]["note"] == "kept_from_other_group"
    assert members["u2"]["generations_per_day"]["after"]["group"] == {"id": second, "name": "premium-tier-2"}
    assert data["summary"] == {"members": 2, "changed": 1, "over_after": 0}
    assert seed.rows("SELECT plan_id FROM user_groups WHERE id = ?", (first,))[0]["plan_id"] is None

    inherit = client.get(f"/api/admin/plans/groups/{second}/impact", headers=ADMIN).json()["data"]
    u2 = {row["kind"]: row for row in inherit["members"][0]["limits"]}
    assert u2["storage_bytes"]["after"]["value"] == 5 * GB
    assert u2["storage_bytes"]["note"] == "default"
    assert u2["storage_bytes"]["change"] == "lowered"


def test_users_list_and_user_detail(client, plans, seed):
    free = make_plan(plans, "Free", storage_bytes=10, generations_per_day=20)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, free.id)
    seed.upload("u1", 9)
    seed.upload("u2", 1)
    client.put("/api/admin/plans/users/u2", json={"plan_id": "unlimited"}, headers=ADMIN)

    data = client.get("/api/admin/plans/users", headers=ADMIN).json()["data"]
    assert [k["key"] for k in data["kinds"]] == ["storage_bytes", "generations_per_day"]
    assert [u["username"] for u in data["users"]][:1] == ["u1"]
    by_name = {u["username"]: u for u in data["users"]}
    assert by_name["u1"]["max_percent"] == 90.0
    assert by_name["u2"]["source"] == "override"
    assert by_name["u2"]["limits"][0]["limit"] is None
    assert by_name["admin"]["exempt"] is True
    assert by_name["admin"]["limits"][0]["enforced"] is False

    filtered = client.get(f"/api/admin/plans/users?plan_id={free.id}&sort=username", headers=ADMIN).json()["data"]
    assert [u["username"] for u in filtered["users"]] == ["admin", "u1"]

    detail = client.get("/api/admin/plans/users/u1", headers=ADMIN).json()["data"]
    assert detail["decided_by"] == "default"
    assert [step["step"] for step in detail["resolution"]] == ["override", "groups", "default"]
    assert detail["limits"][0]["detail"] == "Free (default plan)"
    assert client.get("/api/admin/plans/users/ghost", headers=ADMIN).status_code == 404


def test_plan_list_and_detail_count_assignments_and_usage(client, plans, seed):
    free = make_plan(plans, "Free", storage_bytes=10)
    tier = make_plan(plans, "Tier", storage_bytes=100)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, free.id)
    client.put("/api/admin/plans/users/u2", json={"plan_id": tier.id}, headers=ADMIN)
    seed.upload("u1", 9)
    seed.upload("u2", 50)

    listing = client.get("/api/admin/plans", headers=ADMIN).json()["data"]
    assert [p["name"] for p in listing["plans"]] == ["Free", "Tier", "Unlimited"]
    by_name = {p["name"]: p for p in listing["plans"]}
    assert by_name["Free"]["is_default"] is True
    assert by_name["Free"]["usage"] == {"members": 2, "kinds": [{"kind": "storage_bytes", "used_total": 9}]}
    assert by_name["Tier"]["assigned"] == {"groups": 0, "users": 1}

    detail = client.get(f"/api/admin/plans/{free.id}", headers=ADMIN).json()["data"]
    assert detail["assigned_to"]["groups"][0]["is_default"] is True
    assert detail["in_use"] == {
        "people": 2, "above_warn": 1, "at_limit": 0,
        "kinds": [{"kind": "storage_bytes", "used_total": 9, "limit_total": 20}],
    }
