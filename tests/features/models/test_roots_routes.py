from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.features.model_layouts.catalog import ModelLayoutCatalog
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
def layout_catalog(tmp_path):
    from tests.features.model_layouts.helpers import write_layout

    write_layout(
        tmp_path / "layouts" / "marketplace",
        "tool",
        markers=[{"path": "tool.py", "kind": "file", "weight": 4}],
        config_readers=[],
        models_root=["models"],
        folders=[{"path": "loras", "model_type": "lora", "write": True}],
    )
    return ModelLayoutCatalog(str(tmp_path / "layouts"))


@pytest.fixture
def container(manager, layout_catalog):
    indexing = manager._indexing
    return SimpleNamespace(
        model_roots_manager=manager,
        model_index_manager=SimpleNamespace(indexing=indexing),
        model_layout_catalog=layout_catalog,
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
    return root_dir


class TestListRoots:
    @pytest.mark.asyncio
    async def test_returns_the_expected_shape(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/models/roots")

        assert response.status_code == 200
        data = response.json()["data"]
        assert {"roots", "types", "unplaced", "indexing", "server_os", "path_style"} <= set(data.keys())
        assert any(r["id"] == HOME_ROOT_ID for r in data["roots"])
        assert data["path_style"] in ("windows", "posix")


class TestDetect:
    @pytest.mark.asyncio
    async def test_detects_a_typed_layout(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/models/roots/detect", json={"path": str(library_dir)})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["layout"] == "typed"
        assert any(s["model_type"] == "lora" for s in data["suggestions"])


class TestDetectWithProfiles:
    @pytest.mark.asyncio
    async def test_response_keeps_the_existing_fields_and_adds_the_new_ones(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/models/roots/detect", json={"path": str(library_dir)})

        data = response.json()["data"]
        assert {"path", "effective_path", "state", "layout", "suggestions", "single_type_guess", "conflicts", "warnings"} <= set(data)
        assert {"root_path", "profile", "alternatives", "outside_folders", "extra_roots", "delegated"} <= set(data)
        assert data["profile"] is None and data["alternatives"][-1]["id"] == "generic"

    @pytest.mark.asyncio
    async def test_a_matching_profile_is_detected(self, app, tmp_path):
        install = tmp_path / "install"
        (install / "models" / "loras").mkdir(parents=True)
        (install / "tool.py").write_text("", encoding="utf-8")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/models/roots/detect", json={"path": str(install)})

        data = response.json()["data"]
        assert data["profile"]["id"] == "tool"
        assert [s["subdir"] for s in data["suggestions"]] == ["models/loras"]

    @pytest.mark.asyncio
    async def test_generic_override_skips_the_profile(self, app, tmp_path):
        install = tmp_path / "install"
        (install / "models" / "loras").mkdir(parents=True)
        (install / "tool.py").write_text("", encoding="utf-8")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/models/roots/detect", json={"path": str(install), "profile": "generic"})

        assert response.json()["data"]["profile"] is None

    @pytest.mark.asyncio
    async def test_unknown_profile_is_a_bad_request(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/models/roots/detect", json={"path": str(library_dir), "profile": "nope"})

        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "model_layout_unknown"


class TestCreateRootWithProfile:
    @pytest.mark.asyncio
    async def test_the_profile_is_stored_on_the_root(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "profile": "tool", "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            listing = await client.get("/api/models/roots")

        assert response.status_code == 201
        stored = next(r for r in listing.json()["data"]["roots"] if r["id"] == response.json()["data"]["id"])
        assert stored["layout_profile"] == "tool"

    @pytest.mark.asyncio
    async def test_generic_is_accepted_and_unknown_is_rejected(self, app, library_dir, tmp_path):
        other = tmp_path / "other"
        (other / "loras").mkdir(parents=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ok = await client.post(
                "/api/models/roots",
                json={"path": str(other), "profile": "generic", "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            bad = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "profile": "nope", "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )

        assert ok.status_code == 201
        assert bad.status_code == 400 and bad.json()["detail"]["error"] == "model_layout_unknown"


class TestCreateRoot:
    @pytest.mark.asyncio
    async def test_creates_a_root(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "label": "Library", "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )

        assert response.status_code == 201
        assert response.json()["data"]["label"] == "Library"

    @pytest.mark.asyncio
    async def test_duplicate_path_is_a_conflict(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = {"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]}
            first = await client.post("/api/models/roots", json=body)
            second = await client.post("/api/models/roots", json=body)

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["detail"]["error"] == "model_roots_duplicate"

    @pytest.mark.asyncio
    async def test_missing_subdir_is_unprocessable(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "vae", "subdir": "vae"}]},
            )

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "model_roots_invalid_binding"

    @pytest.mark.asyncio
    async def test_requires_admin(self, container, regular_user, library_dir):
        def _forbidden():
            raise HTTPException(status_code=403, detail="forbidden")

        fastapi_app = FastAPI()
        fastapi_app.include_router(build_router(container))
        fastapi_app.dependency_overrides[get_current_admin_user] = _forbidden
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
            response = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )

        assert response.status_code == 403


class TestUpdateDeleteRoot:
    @pytest.mark.asyncio
    async def test_update_then_delete(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            root_id = created.json()["data"]["id"]

            updated = await client.patch(f"/api/models/roots/{root_id}", json={"label": "Renamed"})
            assert updated.status_code == 200
            assert updated.json()["data"]["label"] == "Renamed"

            deleted = await client.delete(f"/api/models/roots/{root_id}")
            assert deleted.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_home_is_refused(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/models/roots/{HOME_ROOT_ID}")

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "model_roots_home_protected"

    @pytest.mark.asyncio
    async def test_update_unknown_root_is_not_found(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch("/api/models/roots/does-not-exist", json={"label": "x"})

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_types_removes_a_binding(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            root_id = created.json()["data"]["id"]

            updated = await client.patch(
                f"/api/models/roots/{root_id}", json={"remove_types": ["lora"]}
            )

        assert updated.status_code == 200
        assert updated.json()["data"]["bindings"] == []

    @pytest.mark.asyncio
    async def test_remove_types_on_home_is_refused(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/api/models/roots/{HOME_ROOT_ID}", json={"remove_types": ["lora"]}
            )

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "model_roots_home_protected"


class TestReorderAndWrite:
    @pytest.mark.asyncio
    async def test_reorder_and_set_write(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            root_id = created.json()["data"]["id"]

            reordered = await client.put(
                "/api/models/roots/order", json={"model_type": "lora", "root_ids": [root_id, HOME_ROOT_ID]}
            )
            assert reordered.status_code == 200

            written = await client.put(
                "/api/models/roots/write", json={"model_type": "lora", "root_id": root_id}
            )
            assert written.status_code == 200
            binding = next(b for b in written.json()["data"]["bindings"] if b["model_type"] == "lora")
            assert binding["is_write"] is True

    @pytest.mark.asyncio
    async def test_set_write_refuses_read_only_root(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/models/roots",
                json={
                    "path": str(library_dir),
                    "bindings": [{"model_type": "lora", "subdir": "loras"}],
                    "read_only": True,
                },
            )
            root_id = created.json()["data"]["id"]

            written = await client.put(
                "/api/models/roots/write", json={"model_type": "lora", "root_id": root_id}
            )

        assert written.status_code == 409
        assert written.json()["detail"]["error"] == "model_roots_read_only"


class TestProbeRoot:
    @pytest.mark.asyncio
    async def test_probe_returns_state(self, app, library_dir):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/models/roots",
                json={"path": str(library_dir), "bindings": [{"model_type": "lora", "subdir": "loras"}]},
            )
            root_id = created.json()["data"]["id"]

            probed = await client.post(f"/api/models/roots/{root_id}/probe")

        assert probed.status_code == 200
        assert probed.json()["data"]["state"] == "online"


class TestRouteOrder:
    def test_static_roots_paths_register_before_the_dynamic_model_id_catch_all(self, container):
        from src.features.models.routes import build_router as build_model_router

        fastapi_app = FastAPI()
        model_container = SimpleNamespace(model_controller=Mock())
        fastapi_app.include_router(build_router(container))
        fastapi_app.include_router(build_model_router(model_container))

        paths_with_methods = [
            (r.path, tuple(sorted(r.methods))) for r in fastapi_app.router.routes if isinstance(r, APIRoute)
        ]
        catch_all_index = paths_with_methods.index(("/api/models/{model_id}", ("GET",)))
        roots_list_index = paths_with_methods.index(("/api/models/roots", ("GET",)))

        assert roots_list_index < catch_all_index
