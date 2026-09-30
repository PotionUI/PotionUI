from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.features.models.roots import ModelRootsManager
from src.features.models.roots_routes import build_router
from src.platform.filesystem.model_roots import ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.security.current_user import get_current_active_user, get_current_admin_user


class FakeIndexingCoordinator:
    def __init__(self):
        self.calls = []

    def cancel_and_restart(self, trigger="location_change"):
        self.calls.append(trigger)
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


@pytest.fixture
def manager(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    return ModelRootsManager(
        repository=repository,
        resolver=ModelRootResolver(repository, probe, tmp_path),
        probe=probe,
        indexing_coordinator=FakeIndexingCoordinator(),
        base_dir=tmp_path,
    )


@pytest.fixture
def app(manager):
    container = SimpleNamespace(
        model_roots_manager=manager,
        model_index_manager=SimpleNamespace(indexing=manager._indexing),
        model_layout_catalog=None,
    )
    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(container))
    admin = Mock(id="admin-1")
    fastapi_app.dependency_overrides[get_current_admin_user] = lambda: admin
    fastapi_app.dependency_overrides[get_current_active_user] = lambda: admin
    return fastapi_app


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    for name in ("Stable-diffusion", "checkpoints", "loras", "unet"):
        (root / name).mkdir(parents=True)
    return root


def client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def create(client, library):
    response = await client.post(
        "/api/models/roots",
        json={
            "path": str(library),
            "bindings": [
                {"model_type": "checkpoint", "subdir": "Stable-diffusion"},
                {"model_type": "unet", "subdir": "unet"},
                {"model_type": "lora", "subdir": "loras"},
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


def flags(root):
    return {b["model_type"]: b["scan_headers"] for b in root["bindings"]}


@pytest.mark.asyncio
async def test_new_bindings_report_the_default_from_their_folder_name(app, library):
    async with client_for(app) as client:
        root = await create(client, library)
    assert flags(root) == {"checkpoint": True, "unet": True, "lora": False}


@pytest.mark.asyncio
async def test_create_accepts_an_explicit_flag(app, library):
    async with client_for(app) as client:
        response = await client.post(
            "/api/models/roots",
            json={
                "path": str(library),
                "bindings": [
                    {"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": False},
                    {"model_type": "diffusion_model", "subdir": "checkpoints", "scan_headers": True},
                ],
            },
        )
    assert flags(response.json()["data"]) == {"checkpoint": False, "diffusion_model": True}


@pytest.mark.asyncio
async def test_the_overview_lists_the_flag_per_binding(app, library):
    async with client_for(app) as client:
        root = await create(client, library)
        overview = (await client.get("/api/models/roots")).json()["data"]
    listed = next(r for r in overview["roots"] if r["id"] == root["id"])
    assert flags(listed) == {"checkpoint": True, "unet": True, "lora": False}


@pytest.mark.asyncio
async def test_detect_suggestions_carry_the_default(app, library):
    async with client_for(app) as client:
        response = await client.post("/api/models/roots/detect", json={"path": str(library)})
    by_folder = {(s["model_type"], s["subdir"]): s["scan_headers"] for s in response.json()["data"]["suggestions"]}
    assert by_folder[("lora", "loras")] is False
    assert by_folder[("unet", "unet")] is True
    assert by_folder[("checkpoint", "checkpoints")] is False


@pytest.mark.asyncio
async def test_patch_switches_the_flag_and_restarts_indexing(app, manager, library):
    async with client_for(app) as client:
        root = await create(client, library)
        manager._indexing.calls.clear()

        response = await client.patch(
            f"/api/models/roots/{root['id']}/bindings",
            json={"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": False},
        )

    assert response.status_code == 200
    assert flags(response.json()["data"])["checkpoint"] is False
    assert manager._indexing.calls == ["roots_change"]
    stored = {b["model_type"]: b["scan_headers"] for b in ModelRootRepository().bindings_for_root(root["id"])}
    assert stored["checkpoint"] == 0
    assert stored["unet"] == 1


@pytest.mark.asyncio
async def test_patch_can_switch_a_folder_on(app, library):
    async with client_for(app) as client:
        response = await client.post(
            "/api/models/roots",
            json={"path": str(library), "bindings": [{"model_type": "diffusion_model", "subdir": "checkpoints"}]},
        )
        root = response.json()["data"]
        assert flags(root) == {"diffusion_model": False}

        patched = await client.patch(
            f"/api/models/roots/{root['id']}/bindings",
            json={"model_type": "diffusion_model", "subdir": "checkpoints", "scan_headers": True},
        )

    assert flags(patched.json()["data"]) == {"diffusion_model": True}


@pytest.mark.asyncio
async def test_a_type_outside_the_classified_set_is_unprocessable(app, manager, library):
    async with client_for(app) as client:
        root = await create(client, library)
        manager._indexing.calls.clear()
        response = await client.patch(
            f"/api/models/roots/{root['id']}/bindings",
            json={"model_type": "lora", "subdir": "loras", "scan_headers": True},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "model_roots_invalid_binding"
    assert manager._indexing.calls == []


@pytest.mark.asyncio
async def test_an_unknown_binding_is_unprocessable(app, library):
    async with client_for(app) as client:
        root = await create(client, library)
        response = await client.patch(
            f"/api/models/roots/{root['id']}/bindings",
            json={"model_type": "checkpoint", "subdir": "elsewhere", "scan_headers": True},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_root_is_not_found(app):
    async with client_for(app) as client:
        response = await client.patch(
            "/api/models/roots/nope/bindings",
            json={"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": True},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_requires_admin(manager, library):
    def forbidden():
        raise HTTPException(status_code=403, detail="forbidden")

    container = SimpleNamespace(
        model_roots_manager=manager,
        model_index_manager=SimpleNamespace(indexing=manager._indexing),
        model_layout_catalog=None,
    )
    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(container))
    fastapi_app.dependency_overrides[get_current_admin_user] = forbidden
    async with client_for(fastapi_app) as client:
        response = await client.patch(
            "/api/models/roots/home/bindings",
            json={"model_type": "unet", "subdir": "unet", "scan_headers": False},
        )
    assert response.status_code == 403


def stored_flags(root_id):
    return {b["model_type"]: b["scan_headers"] for b in ModelRootRepository().bindings_for_root(root_id)}


@pytest.mark.asyncio
async def test_put_flips_the_flag_of_an_existing_binding_and_adds_a_new_one_with_it(app, library):
    (library / "diffusion_models").mkdir()
    async with client_for(app) as client:
        root = await create(client, library)
        assert stored_flags(root["id"]) == {"checkpoint": 1, "unet": 1, "lora": 0}

        response = await client.patch(
            f"/api/models/roots/{root['id']}",
            json={
                "bindings": [
                    {"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": False},
                    {"model_type": "diffusion_model", "subdir": "diffusion_models", "scan_headers": True},
                ]
            },
        )

    assert response.status_code == 200
    assert stored_flags(root["id"]) == {"checkpoint": 0, "unet": 1, "lora": 0, "diffusion_model": 1}
    assert flags(response.json()["data"])["diffusion_model"] is True


@pytest.mark.asyncio
async def test_an_update_without_a_flag_keeps_the_stored_one(app, library):
    async with client_for(app) as client:
        root = await create(client, library)
        await client.patch(
            f"/api/models/roots/{root['id']}/bindings",
            json={"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": False},
        )

        await client.patch(
            f"/api/models/roots/{root['id']}",
            json={"bindings": [{"model_type": "checkpoint", "subdir": "Stable-diffusion"}]},
        )

    assert stored_flags(root["id"])["checkpoint"] == 0


@pytest.mark.asyncio
async def test_create_refuses_scanning_on_a_type_that_is_not_classified(app, library):
    async with client_for(app) as client:
        response = await client.post(
            "/api/models/roots",
            json={"path": str(library), "bindings": [{"model_type": "lora", "subdir": "loras", "scan_headers": True}]},
        )
        overview = (await client.get("/api/models/roots")).json()["data"]

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "model_roots_invalid_binding"
    assert all(r["path"] != str(library) for r in overview["roots"])


@pytest.mark.asyncio
async def test_update_refuses_scanning_on_a_type_that_is_not_classified_and_stores_nothing(app, library):
    async with client_for(app) as client:
        root = await create(client, library)
        response = await client.patch(
            f"/api/models/roots/{root['id']}",
            json={
                "label": "Renamed",
                "bindings": [
                    {"model_type": "checkpoint", "subdir": "Stable-diffusion", "scan_headers": False},
                    {"model_type": "lora", "subdir": "loras", "scan_headers": True},
                ],
            },
        )

    assert response.status_code == 422
    assert stored_flags(root["id"]) == {"checkpoint": 1, "unet": 1, "lora": 0}


@pytest.mark.asyncio
async def test_switching_scanning_off_on_any_type_is_allowed_at_create(app, library):
    async with client_for(app) as client:
        response = await client.post(
            "/api/models/roots",
            json={"path": str(library), "bindings": [{"model_type": "lora", "subdir": "loras", "scan_headers": False}]},
        )

    assert response.status_code == 201
