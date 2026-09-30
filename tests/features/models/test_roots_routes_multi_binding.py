from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.features.models.roots import ModelRootsManager
from src.features.models.roots_routes import build_router
from src.platform.filesystem.model_roots import HOME_ROOT_ID, ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import AccountType, User


class FakeIndexingCoordinator:
    def __init__(self):
        self.calls = []

    def cancel_and_restart(self, trigger="location_change"):
        self.calls.append(trigger)
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


@pytest.fixture
def admin_user():
    user = Mock(spec=User)
    user.id = "admin-1"
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def regular_user():
    user = Mock(spec=User)
    user.id = "user-1"
    user.account_type = AccountType.USER
    return user


@pytest.fixture
def manager(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    resolver = ModelRootResolver(repository, probe, tmp_path)
    coordinator = FakeIndexingCoordinator()
    return ModelRootsManager(
        repository=repository,
        resolver=resolver,
        probe=probe,
        indexing_coordinator=coordinator,
        base_dir=tmp_path,
    )


@pytest.fixture
def container(manager):
    indexing = manager._indexing
    return SimpleNamespace(
        model_roots_manager=manager,
        model_index_manager=SimpleNamespace(indexing=indexing),
    )


@pytest.fixture
def app(container, admin_user):
    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(container))
    fastapi_app.dependency_overrides[get_current_admin_user] = lambda: admin_user
    fastapi_app.dependency_overrides[get_current_active_user] = lambda: admin_user
    return fastapi_app


@pytest.fixture
def library_dir(tmp_path):
    root_dir = tmp_path / "library"
    (root_dir / "loras").mkdir(parents=True)
    (root_dir / "LyCORIS").mkdir(parents=True)
    return root_dir


_TWO = [{"model_type": "lora", "subdir": "loras"}, {"model_type": "lora", "subdir": "LyCORIS"}]


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _lora(data):
    return [b for b in data["bindings"] if b["model_type"] == "lora"]


class TestSeveralFolders:
    @pytest.mark.asyncio
    async def test_creates_a_root_with_two_folders_of_one_type(self, app, library_dir):
        async with _client(app) as client:
            response = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})

        assert response.status_code == 201
        assert [b["subdir"] for b in _lora(response.json()["data"])] == ["loras", "LyCORIS"]

    @pytest.mark.asyncio
    async def test_the_write_flag_selects_the_folder(self, app, library_dir):
        bindings = [_TWO[0], {**_TWO[1], "write": True}]
        async with _client(app) as client:
            response = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": bindings})

        assert [b["is_write"] for b in _lora(response.json()["data"])] == [False, True]

    @pytest.mark.asyncio
    async def test_a_nested_folder_is_unprocessable_with_a_clear_message(self, app, library_dir):
        (library_dir / "loras" / "sub").mkdir()
        bindings = [_TWO[0], {"model_type": "lora", "subdir": "loras/sub"}]
        async with _client(app) as client:
            response = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": bindings})

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "model_roots_binding_nested"
        assert "loras/sub" in detail["message"] and "cannot contain each other" in detail["message"]

    @pytest.mark.asyncio
    async def test_a_repeated_folder_is_unprocessable(self, app, library_dir):
        bindings = [_TWO[0], {"model_type": "lora", "subdir": "loras/"}]
        async with _client(app) as client:
            response = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": bindings})

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "model_roots_duplicate_binding"

    @pytest.mark.asyncio
    async def test_patch_adds_a_folder_instead_of_replacing(self, app, library_dir):
        (library_dir / "Locon").mkdir()
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            updated = await client.patch(
                f"/api/models/roots/{root_id}", json={"bindings": [{"model_type": "lora", "subdir": "Locon"}]}
            )

        assert [b["subdir"] for b in _lora(updated.json()["data"])] == ["loras", "LyCORIS", "Locon"]

    @pytest.mark.asyncio
    async def test_patch_moves_the_write_folder_with_the_flag(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post(
                "/api/models/roots", json={"path": str(library_dir), "bindings": _TWO, "write_types": ["lora"]}
            )
            root_id = created.json()["data"]["id"]

            updated = await client.patch(
                f"/api/models/roots/{root_id}",
                json={"bindings": [{"model_type": "lora", "subdir": "LyCORIS", "write": True}]},
            )

        assert [b["is_write"] for b in _lora(updated.json()["data"])] == [False, True]

    @pytest.mark.asyncio
    async def test_patch_refuses_a_nested_folder(self, app, library_dir):
        (library_dir / "loras" / "sub").mkdir()
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            response = await client.patch(
                f"/api/models/roots/{root_id}", json={"bindings": [{"model_type": "lora", "subdir": "loras/sub"}]}
            )

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "model_roots_binding_nested"

    @pytest.mark.asyncio
    async def test_patch_removes_one_folder(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            updated = await client.patch(
                f"/api/models/roots/{root_id}",
                json={"remove_bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )

        assert [b["subdir"] for b in _lora(updated.json()["data"])] == ["LyCORIS"]

    @pytest.mark.asyncio
    async def test_patch_refuses_to_remove_an_unknown_folder(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            response = await client.patch(
                f"/api/models/roots/{root_id}",
                json={"remove_bindings": [{"model_type": "lora", "subdir": "Nope"}]},
            )

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "model_roots_invalid_binding"

    @pytest.mark.asyncio
    async def test_remove_types_still_removes_every_folder_of_the_type(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            updated = await client.patch(f"/api/models/roots/{root_id}", json={"remove_types": ["lora"]})

        assert updated.json()["data"]["bindings"] == []

    @pytest.mark.asyncio
    async def test_write_takes_a_subdir(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post(
                "/api/models/roots", json={"path": str(library_dir), "bindings": _TWO, "write_types": ["lora"]}
            )
            root_id = created.json()["data"]["id"]
            assert [b["is_write"] for b in _lora(created.json()["data"])] == [True, False]

            written = await client.put(
                "/api/models/roots/write", json={"model_type": "lora", "root_id": root_id, "subdir": "LyCORIS"}
            )

        assert written.status_code == 200
        assert [b["is_write"] for b in _lora(written.json()["data"])] == [False, True]

    @pytest.mark.asyncio
    async def test_write_without_a_subdir_keeps_working(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            written = await client.put("/api/models/roots/write", json={"model_type": "lora", "root_id": root_id})

        assert written.status_code == 200
        assert [b["is_write"] for b in _lora(written.json()["data"])] == [True, False]

    @pytest.mark.asyncio
    async def test_write_on_an_unbound_subdir_is_unprocessable(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            response = await client.put(
                "/api/models/roots/write", json={"model_type": "lora", "root_id": root_id, "subdir": "Nope"}
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_the_overview_lists_the_bindings_of_each_type(self, app, library_dir):
        async with _client(app) as client:
            created = await client.post("/api/models/roots", json={"path": str(library_dir), "bindings": _TWO})
            root_id = created.json()["data"]["id"]

            overview = await client.get("/api/models/roots")

        lora = next(t for t in overview.json()["data"]["types"] if t["model_type"] == "lora")
        mine = [b for b in lora["bindings"] if b["root_id"] == root_id]
        assert [b["subdir"] for b in mine] == ["loras", "LyCORIS"]
        assert all(b["binding_id"] for b in mine)
