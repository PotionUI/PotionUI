from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import core_bridge, nsfw, settings
from backend.api import router
from backend.nsfw import resolve_nsfw as real_resolve_nsfw
from backend.permissions_store import Row, load_rows, save_rows
from backend.schema import NsfwContext
from backend.tables import create_tables
from src.plugin_api import AccountType, User, get_current_active_user
from src.plugin_api.models import model_types

ME_URL = "/api/plugins/model-browser/me"
PERMISSIONS_URL = "/api/plugins/model-browser/permissions"

_GROUPS = [
    {"id": "all_admins", "name": "All Admins", "is_system": True, "content_policy": None},
    {"id": "all_users", "name": "All Users", "is_system": True, "content_policy": None},
    {"id": "artists", "name": "Artists", "is_system": False, "content_policy": "blur"},
]
_MEMBERSHIP = {
    "admin-1": ["all_admins", "all_users"],
    "user-1": ["all_users"],
    "artist-1": ["all_users", "artists"],
}

_NSFW = NsfwContext(ceiling="blur", mode="blur", allowed_modes=("blur", "hide"), max_level=4, hide_poi=True)
_NSFW_JSON = {
    "ceiling": "blur",
    "mode": "blur",
    "allowed_modes": ["blur", "hide"],
    "max_level": 4,
    "hide_poi": True,
    "setting_key": "media_nsfw_filter_mode",
}

_ALL_GROUPS_JSON = [
    {"id": "all_admins", "name": "All Admins"},
    {"id": "all_users", "name": "All Users"},
    {"id": "artists", "name": "Artists"},
]

_DEFAULT_PARAMS = {
    "sources": ["civitai", "huggingface", "openmodeldb"],
    "model_types": [],
    "max_file_gb": None,
    "assign_to": "user",
    "allow_remote": False,
}
_DEFAULT_GROUPS = [
    {
        "group_id": "all_admins",
        "name": "All Admins",
        "is_system": True,
        "capabilities": ["approve", "browse", "download", "request"],
        "params": _DEFAULT_PARAMS,
    },
    {
        "group_id": "all_users",
        "name": "All Users",
        "is_system": True,
        "capabilities": ["browse", "request"],
        "params": _DEFAULT_PARAMS,
    },
    {"group_id": "artists", "name": "Artists", "is_system": False, "capabilities": [], "params": _DEFAULT_PARAMS},
]


def _user(user_id, account_type=AccountType.USER):
    return User(
        id=user_id,
        username=f"{user_id}-name",
        email=f"{user_id}@test.com",
        password_hash="hash",
        account_type=account_type,
    )


ADMIN = _user("admin-1", AccountType.ADMIN)
USER = _user("user-1")
ARTIST = _user("artist-1")


@pytest.fixture
def core(monkeypatch):
    nsfw_users = []

    def user_groups(user_id):
        return [dict(group) for group in _GROUPS if group["id"] in _MEMBERSHIP.get(user_id, [])]

    def resolve(user):
        nsfw_users.append(user.id)
        return _NSFW

    monkeypatch.setattr(core_bridge, "list_groups", lambda: [dict(group) for group in _GROUPS])
    monkeypatch.setattr(core_bridge, "user_groups", user_groups)
    monkeypatch.setattr(nsfw, "resolve_nsfw", resolve)
    return SimpleNamespace(nsfw_users=nsfw_users)


@pytest.fixture
def app(plugin_db, core):
    create_tables()
    app = FastAPI()
    app.include_router(router)
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def _sign_in(app, user):
    app.dependency_overrides[get_current_active_user] = lambda: user


def test_me_for_an_admin(app, client, core):
    _sign_in(app, ADMIN)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "user": {"id": "admin-1", "username": "admin-1-name", "is_admin": True},
            "capabilities": ["approve", "browse", "download", "request"],
            "params": {
                "sources": [],
                "model_types": [],
                "max_file_gb": None,
                "assign_to": "user",
                "allow_remote": True,
                "unrestricted": True,
            },
            "request_params": {"sources": [], "model_types": [], "max_file_gb": None, "assign_to": "user"},
            "nsfw": _NSFW_JSON,
            "groups": _ALL_GROUPS_JSON,
            "requests": {"open_count": 0},
        },
    }
    assert core.nsfw_users == ["admin-1"]


def test_me_for_a_user_with_the_default_rights(app, client, core):
    _sign_in(app, USER)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "user": {"id": "user-1", "username": "user-1-name", "is_admin": False},
            "capabilities": ["browse", "request"],
            "params": {
                "sources": [],
                "model_types": [],
                "max_file_gb": None,
                "assign_to": "user",
                "allow_remote": False,
                "unrestricted": False,
            },
            "request_params": {
                "sources": ["civitai", "huggingface", "openmodeldb"],
                "model_types": [],
                "max_file_gb": None,
                "assign_to": "user",
            },
            "nsfw": _NSFW_JSON,
            "groups": [{"id": "all_users", "name": "All Users"}],
            "requests": {"open_count": 0},
        },
    }
    assert core.nsfw_users == ["user-1"]


def test_me_answers_a_user_without_any_capability(app, client):
    _sign_in(app, _user("loner"))

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["capabilities"] == []


def test_me_lists_only_the_groups_a_user_belongs_to(app, client):
    _sign_in(app, ARTIST)

    groups = client.get(ME_URL).json()["data"]["groups"]

    assert groups == [{"id": "all_users", "name": "All Users"}, {"id": "artists", "name": "Artists"}]


def test_me_does_not_list_a_group_the_user_is_not_in(app, client):
    _sign_in(app, USER)

    groups = client.get(ME_URL).json()["data"]["groups"]

    assert "artists" not in [group["id"] for group in groups]


def test_me_lists_every_group_for_an_admin_even_one_they_are_not_in(app, client):
    assert "artists" not in _MEMBERSHIP["admin-1"]
    _sign_in(app, ADMIN)

    groups = client.get(ME_URL).json()["data"]["groups"]

    assert groups == _ALL_GROUPS_JSON


def test_me_groups_carry_an_id_and_a_name_only(app, client):
    for who in (ADMIN, ARTIST):
        _sign_in(app, who)

        groups = client.get(ME_URL).json()["data"]["groups"]

        assert groups and all(set(group) == {"id", "name"} for group in groups)


def test_me_groups_are_empty_for_a_user_in_no_group(app, client):
    _sign_in(app, _user("loner"))

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["groups"] == []


def test_me_takes_the_groups_of_an_admin_from_the_list_of_every_core_group(app, client, monkeypatch):
    _sign_in(app, ADMIN)
    monkeypatch.setattr(core_bridge, "list_groups", lambda: [{"id": "testers", "name": "Testers", "is_system": False}])

    assert client.get(ME_URL).json()["data"]["groups"] == [{"id": "testers", "name": "Testers"}]


def test_me_follows_what_the_admin_saved(app, client):
    _sign_in(app, ADMIN)
    client.put(
        PERMISSIONS_URL,
        json={
            "groups": [
                {
                    "group_id": "artists",
                    "capabilities": ["browse", "download"],
                    "params": {
                        "sources": ["civitai"],
                        "model_types": ["lora"],
                        "max_file_gb": 2,
                        "assign_to": "group",
                        "allow_remote": False,
                    },
                }
            ]
        },
    ).raise_for_status()
    _sign_in(app, ARTIST)

    data = client.get(ME_URL).json()["data"]

    assert data["capabilities"] == ["browse", "download", "request"]
    assert data["params"] == {
        "sources": ["civitai"],
        "model_types": ["lora"],
        "max_file_gb": 2.0,
        "assign_to": "group",
        "allow_remote": False,
        "unrestricted": False,
    }
    assert data["request_params"] == {
        "sources": ["civitai", "huggingface", "openmodeldb"],
        "model_types": [],
        "max_file_gb": None,
        "assign_to": "user",
    }


def test_me_request_params_follow_the_groups_granting_request(app, client):
    _sign_in(app, ADMIN)
    client.put(
        PERMISSIONS_URL,
        json={
            "groups": [
                {
                    "group_id": "all_users",
                    "capabilities": ["browse", "request"],
                    "params": {
                        "sources": ["huggingface", "civitai"],
                        "model_types": ["embedding", "lora"],
                        "max_file_gb": 1.5,
                        "assign_to": "group",
                        "allow_remote": True,
                    },
                }
            ]
        },
    ).raise_for_status()
    _sign_in(app, USER)

    data = client.get(ME_URL).json()["data"]

    assert data["request_params"] == {
        "sources": ["civitai", "huggingface"],
        "model_types": ["embedding", "lora"],
        "max_file_gb": 1.5,
        "assign_to": "group",
    }
    assert data["params"]["sources"] == [] and data["params"]["allow_remote"] is False


def test_me_counts_the_open_requests_for_an_approver_only(app, client):
    from backend import requests

    for user_id, status in (("user-1", "open"), ("artist-1", "open"), ("user-1", "rejected")):
        created = requests.create(
            user_id=user_id, source="civitai", source_model_id="1", source_version_id="2", file_id="3", snapshot={}
        )
        if status != "open":
            requests.set_status(created.id, status, decided_by="admin-1")

    _sign_in(app, ADMIN)
    assert client.get(ME_URL).json()["data"]["requests"] == {"open_count": 2}
    _sign_in(app, USER)
    assert client.get(ME_URL).json()["data"]["requests"] == {"open_count": 0}
    save_rows([Row(group_id="artists", capabilities=frozenset({"browse", "approve"}))])
    _sign_in(app, ARTIST)
    assert client.get(ME_URL).json()["data"]["requests"] == {"open_count": 1}


def test_me_still_answers_when_the_requests_cannot_be_counted(app, client, monkeypatch):
    from backend import requests

    def broken():
        raise RuntimeError("no such table: model_browser_requests")

    monkeypatch.setattr(requests, "open_count", broken)
    _sign_in(app, ADMIN)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["requests"] == {"open_count": 0}


def test_me_with_the_real_nsfw_resolution(app, client, monkeypatch):
    values = {"nsfw_ceiling": "blur", "nsfw_max_level": 8, "hide_poi": False}
    monkeypatch.setattr(nsfw, "resolve_nsfw", real_resolve_nsfw)
    monkeypatch.setattr(core_bridge, "effective_policy", lambda user_id: "allowed")
    monkeypatch.setattr(settings, "get_setting", values.__getitem__)
    monkeypatch.setattr(settings, "user_nsfw_mode", lambda user_id: "show")
    _sign_in(app, USER)

    data = client.get(ME_URL).json()["data"]

    assert data["nsfw"] == {
        "ceiling": "blur",
        "mode": "blur",
        "allowed_modes": ["blur", "hide"],
        "max_level": 8,
        "hide_poi": False,
        "setting_key": "media_nsfw_filter_mode",
    }


def test_me_needs_a_signed_in_user(client):
    assert client.get(ME_URL).status_code == 401


def test_get_permissions_lists_every_core_group_with_its_default(app, client):
    _sign_in(app, ADMIN)

    response = client.get(PERMISSIONS_URL)

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "capabilities": ["browse", "request", "download", "approve"],
            "sources": ["civitai", "huggingface", "openmodeldb"],
            "model_types": list(model_types()),
            "groups": _DEFAULT_GROUPS,
        },
    }


def test_get_permissions_shows_stored_rows_and_deletes_those_of_deleted_groups(app, client):
    save_rows(
        [
            Row(group_id="artists", capabilities=frozenset({"browse"})),
            Row(group_id="deleted-group", capabilities=frozenset({"download"})),
        ]
    )
    _sign_in(app, ADMIN)

    groups = client.get(PERMISSIONS_URL).json()["data"]["groups"]

    assert [(group["group_id"], group["capabilities"]) for group in groups] == [
        ("all_admins", ["approve", "browse", "download", "request"]),
        ("all_users", ["browse", "request"]),
        ("artists", ["browse"]),
    ]
    assert set(load_rows()) == {"artists"}


_ARTISTS_UPDATE = {
    "group_id": "artists",
    "capabilities": ["download", "browse", "request"],
    "params": {
        "sources": ["huggingface", "civitai"],
        "model_types": ["lora", "embedding"],
        "max_file_gb": 4.5,
        "assign_to": "group",
        "allow_remote": True,
    },
}


def test_put_permissions_round_trip(app, client):
    _sign_in(app, ADMIN)

    put = client.put(
        PERMISSIONS_URL,
        json={"groups": [{"group_id": "all_users", "capabilities": ["browse"]}, _ARTISTS_UPDATE]},
    )

    assert put.status_code == 200
    assert put.json()["success"] is True
    assert put.json()["data"]["groups"] == [
        _DEFAULT_GROUPS[0],
        {**_DEFAULT_GROUPS[1], "capabilities": ["browse"]},
        {
            "group_id": "artists",
            "name": "Artists",
            "is_system": False,
            "capabilities": ["browse", "download", "request"],
            "params": {
                "sources": ["civitai", "huggingface"],
                "model_types": ["embedding", "lora"],
                "max_file_gb": 4.5,
                "assign_to": "group",
                "allow_remote": True,
            },
        },
    ]
    assert client.get(PERMISSIONS_URL).json() == put.json()
    assert set(load_rows()) == {"all_users", "artists"}


def test_put_records_the_saving_admin_on_every_row(app, client):
    other_admin = _user("admin-2", AccountType.ADMIN)
    _sign_in(app, ADMIN)
    client.put(
        PERMISSIONS_URL,
        json={"groups": [{"group_id": "all_users", "capabilities": ["browse"], "updated_by": "user-1"}, _ARTISTS_UPDATE]},
    ).raise_for_status()

    assert {group_id: row.updated_by for group_id, row in load_rows().items()} == {
        "all_users": "admin-1",
        "artists": "admin-1",
    }

    _sign_in(app, other_admin)
    client.put(PERMISSIONS_URL, json={"groups": [_ARTISTS_UPDATE]}).raise_for_status()

    assert {group_id: row.updated_by for group_id, row in load_rows().items()} == {"artists": "admin-2"}


def test_put_accepts_the_groups_get_answered(app, client):
    _sign_in(app, ADMIN)
    data = client.get(PERMISSIONS_URL).json()["data"]

    put = client.put(PERMISSIONS_URL, json={"groups": data["groups"]})

    assert put.status_code == 200
    assert put.json()["data"] == data
    assert set(load_rows()) == {"all_admins", "all_users", "artists"}


def test_a_group_left_out_of_a_put_goes_back_to_its_default(app, client):
    _sign_in(app, ADMIN)
    client.put(PERMISSIONS_URL, json={"groups": [_ARTISTS_UPDATE]}).raise_for_status()

    put = client.put(PERMISSIONS_URL, json={"groups": []})

    assert put.status_code == 200
    assert put.json()["data"]["groups"] == _DEFAULT_GROUPS
    assert load_rows() == {}


def test_put_refuses_an_unknown_group_and_changes_nothing(app, client):
    kept = Row(group_id="artists", capabilities=frozenset({"browse"}))
    save_rows([kept])
    _sign_in(app, ADMIN)

    response = client.put(
        PERMISSIONS_URL,
        json={
            "groups": [
                {"group_id": "all_users", "capabilities": []},
                {"group_id": "ghosts", "capabilities": ["browse"]},
            ]
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "success": False,
            "error": "unknown_group",
            "message": "There is no user group with the id 'ghosts'",
        }
    }
    assert load_rows() == {"artists": kept}


def _params(**values):
    return {"groups": [{"group_id": "artists", "capabilities": ["download"], "params": values}]}


@pytest.mark.parametrize(
    "body, hint",
    [
        ({"groups": [{"group_id": "artists", "capabilities": ["browse", "manage"]}]}, "unknown capability: manage"),
        (_params(sources=["civitai", "pixiv"]), "unknown source: pixiv"),
        (_params(model_types=["lora", "hypernetwork"]), "unknown model type: hypernetwork"),
        (_params(max_file_gb=0), "greater than 0"),
        (_params(max_file_gb=-2.5), "greater than 0"),
        (_params(max_file_gb="5"), "max_file_gb"),
        (_params(max_file_gb=True), "max_file_gb"),
        (_params(assign_to="everyone"), "assign_to"),
        (_params(allow_remote="yes"), "allow_remote"),
        (_params(max_size_gb=3), "max_size_gb"),
        ({"groups": [{"group_id": "artists"}]}, "capabilities"),
        ({"groups": [{"group_id": 7, "capabilities": []}]}, "group_id"),
        ({"groups": "artists"}, "groups"),
        ({}, "groups"),
        (
            {
                "groups": [
                    {"group_id": "artists", "capabilities": []},
                    {"group_id": "artists", "capabilities": ["browse"]},
                ]
            },
            "listed more than once",
        ),
    ],
    ids=[
        "capability",
        "source",
        "model-type",
        "zero-size",
        "negative-size",
        "size-as-text",
        "size-as-bool",
        "assign-to",
        "allow-remote-as-text",
        "unknown-param",
        "no-capabilities",
        "group-id-not-text",
        "groups-not-a-list",
        "no-groups",
        "duplicate-group",
    ],
)
def test_put_refuses_an_invalid_body_with_the_envelope(app, client, body, hint):
    _sign_in(app, ADMIN)

    response = client.put(PERMISSIONS_URL, json=body)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["success"] is False
    assert detail["error"] == "invalid_request"
    assert hint in detail["message"]
    assert load_rows() == {}


def test_put_refuses_an_infinite_size(app, client):
    _sign_in(app, ADMIN)

    body = '{"groups": [{"group_id": "artists", "capabilities": ["download"], "params": {"max_file_gb": Infinity}}]}'

    response = client.put(PERMISSIONS_URL, content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert "finite" in response.json()["detail"]["message"]


def test_permissions_refuse_a_non_admin_and_change_nothing(app, client):
    kept = Row(group_id="artists", capabilities=frozenset({"browse"}))
    save_rows([kept])
    _sign_in(app, USER)

    assert client.get(PERMISSIONS_URL).status_code == 403
    assert client.put(PERMISSIONS_URL, json={"groups": []}).status_code == 403
    assert load_rows() == {"artists": kept}


def test_permissions_need_a_signed_in_user(client):
    assert client.get(PERMISSIONS_URL).status_code == 401
    assert client.put(PERMISSIONS_URL, json={"groups": []}).status_code == 401
