from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.presets.routes import PresetController, build_router
from src.platform.security.current_user import get_current_active_user
from tests.features.presets.mirror_world import build_world, png_bytes

MIRROR_BODY = {"name": "Anime", "description": None, "cover": None, "offered_modes": ["txt2img"], "first_mode": None}
DISPLAY_BODY = {"name": "Realistic", "description": None, "cover": None}


@pytest.fixture
def world(mock_db, tmp_path):
    return build_world(mock_db, tmp_path)


def _client(world, user):
    backends = Mock()
    backends.engine_supports_prompt_batch.return_value = False
    backends.get_backends_for_engine.return_value = []
    controller = PresetController(world.collaborators, backends)
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(preset_controller=controller)))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def admin(world):
    return _client(world, world.admin)


def _error(response):
    return response.json()["detail"]


def _create(admin, source="krea2", **body):
    return admin.post(f"/api/presets/{source}/mirrors", json={**MIRROR_BODY, **body})


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("get", "/api/presets/krea2/admin", {}),
        ("put", "/api/presets/krea2/display", {"json": DISPLAY_BODY}),
        ("post", "/api/presets/krea2/display/cover", {"files": {"file": ("c.png", b"x", "image/png")}}),
        ("post", "/api/presets/krea2/mirrors", {"json": MIRROR_BODY}),
        ("put", "/api/presets/krea2/modes", {"json": {"offered": None, "first": None}}),
    ],
)
def test_regular_users_are_refused_every_admin_route(world, method, path, kwargs):
    world.install("krea2")

    response = getattr(_client(world, world.regular), method)(path, **kwargs)

    assert response.status_code == 403
    assert world.repo.mirrors_of("krea2") == []
    assert world.repo.get_installed_preset_by_preset_id("krea2").display_name is None


def test_creating_a_mirror_returns_201_with_its_admin_detail(world, admin):
    world.install("krea2")

    response = _create(admin)

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    detail = body["data"]
    assert detail["is_mirror"] is True and detail["source_preset_id"] == "krea2"
    assert set(detail) == {
        "preset_id", "preset_db_id", "is_mirror", "source_preset_id", "source_name", "mirror_state", "file",
        "display", "effective", "modes", "mirrors", "assignment_count", "group_count",
    }
    source = admin.get("/api/presets/krea2/admin").json()["data"]
    assert source["mirrors"] == [{"preset_id": detail["preset_id"], "name": "Anime", "mirror_state": "ok"}]
    assert source["is_mirror"] is False and source["mirror_state"] is None and source["source_name"] is None


@pytest.mark.parametrize(
    "source, body, installed, status, code",
    [
        ("missing", {}, False, 404, "preset_not_found"),
        ("krea2", {}, False, 409, "source_not_installed"),
        ("krea2", {"name": None}, True, 400, "invalid_display"),
        ("krea2", {"offered_modes": ["nope"]}, True, 400, "invalid_modes"),
    ],
)
def test_mirror_creation_refusals_carry_their_status_and_code(world, admin, source, body, installed, status, code):
    if installed:
        world.install("krea2")

    response = _create(admin, source, **body)

    assert response.status_code == status
    assert _error(response)["error"] == code
    assert _error(response)["success"] is False
    if code.startswith("invalid_"):
        assert _error(response)["errors"]


def test_a_mirror_cannot_be_the_source_of_another(world, admin):
    world.install("krea2")
    mirror_id = _create(admin).json()["data"]["preset_id"]

    response = _create(admin, mirror_id)

    assert response.status_code == 400
    assert _error(response)["error"] == "mirror_source_invalid"


def test_the_display_route_saves_and_validates(world, admin):
    assert admin.put("/api/presets/missing/display", json=DISPLAY_BODY).status_code == 404
    not_installed = admin.put("/api/presets/krea2/display", json=DISPLAY_BODY)
    assert not_installed.status_code == 409
    assert _error(not_installed)["error"] == "preset_not_installed"
    world.install("krea2")

    saved = admin.put("/api/presets/krea2/display", json=DISPLAY_BODY)
    refused = admin.put("/api/presets/krea2/display", json={"name": "x" * 81, "description": None, "cover": None})

    assert saved.status_code == 200
    assert saved.json()["data"]["effective"]["name"] == "Realistic"
    assert refused.status_code == 400
    assert _error(refused)["error"] == "invalid_display"


def test_the_cover_upload_route_sets_a_display_cover(world, admin):
    world.install("krea2")

    uploaded = admin.post("/api/presets/krea2/display/cover", files={"file": ("c.png", png_bytes(), "image/png")})
    refused = admin.post("/api/presets/krea2/display/cover", files={"file": ("c.gif", b"GIF89a", "image/gif")})

    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["display"]["cover"].startswith("display/")
    assert refused.status_code == 400
    assert _error(refused)["error"] == "invalid_cover"


def test_the_modes_route_refuses_preset_files_and_bad_modes(world, admin):
    world.install("krea2")
    mirror_id = _create(admin).json()["data"]["preset_id"]

    file_preset = admin.put("/api/presets/krea2/modes", json={"offered": ["txt2img"], "first": None})
    bad = admin.put(f"/api/presets/{mirror_id}/modes", json={"offered": ["txt2img"], "first": "edit"})
    good = admin.put(f"/api/presets/{mirror_id}/modes", json={"offered": ["edit", "txt2img"], "first": "edit"})

    assert (file_preset.status_code, _error(file_preset)["error"]) == (400, "not_a_mirror")
    assert (bad.status_code, _error(bad)["error"]) == (400, "invalid_modes")
    assert good.status_code == 200
    assert good.json()["data"]["modes"]["first"] == "edit"
    assert admin.put("/api/presets/nothing/modes", json={"offered": None, "first": None}).status_code == 404


def test_the_admin_route_404s_without_a_row(world, admin):
    response = admin.get("/api/presets/krea2/admin")

    assert response.status_code == 404
    assert _error(response)["error"] == "preset_not_found"


def test_uninstall_refuses_a_source_with_mirrors_with_409_and_removes_a_mirror_with_a_tombstone(world, admin):
    world.install("krea2")
    mirror_id = _create(admin).json()["data"]["preset_id"]

    blocked = admin.post("/api/presets/krea2/uninstall")
    removed = admin.post(f"/api/presets/{mirror_id}/uninstall")
    legacy = admin.post("/api/presets/flux/uninstall")

    assert blocked.status_code == 409
    assert _error(blocked)["error"] == "preset_has_mirrors"
    assert _error(blocked)["mirrors"] == [{"preset_id": mirror_id, "name": "Anime"}]
    assert removed.status_code == 200
    assert removed.json()["data"] == {"tombstone": {"preset_id": mirror_id, "name": "Anime"}}
    assert legacy.status_code == 200
    assert legacy.json()["success"] is False and legacy.json()["error"] == "preset_not_installed"
    file_preset = admin.post("/api/presets/krea2/uninstall")
    assert file_preset.status_code == 200 and file_preset.json()["data"] is None


def test_a_regular_user_assigned_a_mirror_sees_an_ordinary_preset(world, admin):
    world.install("krea2")
    mirror_id = _create(admin, offered_modes=["edit", "txt2img"], first_mode="edit").json()["data"]["preset_id"]
    world.repo.assign_preset_to_user(mirror_id, "ann")
    user = _client(world, world.regular)

    listed = user.get("/api/presets").json()["data"]
    detail = user.get(f"/api/presets/{mirror_id}").json()["data"]
    modes = user.get(f"/api/presets/{mirror_id}/modes").json()["data"]

    assert [p["id"] for p in listed] == [mirror_id]
    assert not {"is_mirror", "source_preset_id", "mirror_state", "file_name"} & set(listed[0])
    assert detail["name"] == "Anime"
    assert not {"is_mirror", "source_preset_id"} & set(detail)
    assert [m["name"] for m in modes["modes"]] == ["edit", "txt2img"]
    assert modes["default_mode"] == "edit"
    assert user.get("/api/presets/krea2").status_code == 404
