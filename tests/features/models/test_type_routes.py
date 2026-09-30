from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.catalog import ModelCatalog
from src.features.models.indexer import ModelScanner
from src.features.models.locator import ModelLocator
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.features.models.routes import ModelController, build_router
from src.features.models.type_repository import ModelTypeRepository
from src.features.models.type_manager import ModelTypeManager
from src.platform.database.rows import now_iso
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from tests.fixtures.model_index_fixtures import FLUX, LORA, Library, sha_of


@pytest.fixture
def lib(tmp_path, mock_db):
    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) "
            "VALUES ('admin-1', 'admin', 'admin@example.test', 'x', 'ADMIN')"
        )
    return Library(tmp_path)


@pytest.fixture
def app(lib):
    scanner = lib.scanner
    types = ModelTypeManager(model_repo, scanner.types, scanner.recompute_types)
    catalog = ModelCatalog(
        model_repo, Mock(spec=ModelAccessPolicy), scanner,
        user_attribute_repository=Mock(get_map=Mock(return_value={})),
        locator=ModelLocator(scanner.resolver, scanner.locations),
    )
    controller = ModelController(SimpleNamespace(types=types, catalog=catalog), Mock(), Mock())
    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(SimpleNamespace(model_controller=controller)))
    admin = Mock(id="admin-1")
    fastapi_app.dependency_overrides[get_current_admin_user] = lambda: admin
    fastapi_app.dependency_overrides[get_current_active_user] = lambda: admin
    return fastapi_app


def client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def flux(lib):
    lib.set_scan("checkpoint", False)
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")
    return model


@pytest.mark.asyncio
async def test_put_sets_the_type_and_returns_the_model_with_its_type_info(app, flux):
    async with client_for(app) as client:
        response = await client.put(f"/api/models/{flux.id}/type", json={"model_type": "diffusion_model"})

    assert response.status_code == 200
    model = response.json()["data"]["model"]
    assert model["model_type"] == "diffusion_model"
    assert model["type_info"]["source"] == "admin"
    assert model["type_info"]["folder_type"] == "checkpoint"
    stored = model_repo.get_by_id(flux.id)
    assert (stored.model_type, stored.type_source) == ("diffusion_model", "admin")


@pytest.mark.asyncio
async def test_a_set_type_survives_a_rescan_even_when_the_header_disagrees(lib, app, flux):
    async with client_for(app) as client:
        await client.put(f"/api/models/{flux.id}/type", json={"model_type": "vae"})

    lib.set_scan("checkpoint", True)
    lib.index()
    lib.index()

    stored = model_repo.get_by_id(flux.id)
    assert (stored.model_type, stored.type_source) == ("vae", "admin")


@pytest.mark.asyncio
async def test_delete_returns_the_model_to_its_automatic_type(lib, app, flux):
    async with client_for(app) as client:
        await client.put(f"/api/models/{flux.id}/type", json={"model_type": "vae"})
        lib.set_scan("checkpoint", True)
        lib.index()
        assert model_repo.get_by_id(flux.id).type_source == "admin"

        response = await client.delete(f"/api/models/{flux.id}/type")

    assert response.status_code == 200
    stored = model_repo.get_by_id(flux.id)
    assert (stored.model_type, stored.type_source) == ("diffusion_model", "header")
    assert ModelTypeRepository().get_assertions([flux.sha256]) == {}


@pytest.mark.asyncio
async def test_delete_without_an_assertion_changes_nothing(app, flux):
    async with client_for(app) as client:
        response = await client.delete(f"/api/models/{flux.id}/type")

    assert response.status_code == 200
    assert model_repo.get_by_id(flux.id).type_source == "folder"


@pytest.mark.asyncio
async def test_the_type_change_needs_no_file_access(app, flux):
    with patch("src.features.models.indexer.read_header") as header_spy, patch.object(
        ModelScanner, "calculate_sha256", side_effect=AssertionError("must not hash")
    ), patch("builtins.open", side_effect=AssertionError("must not open files")):
        async with client_for(app) as client:
            put = await client.put(f"/api/models/{flux.id}/type", json={"model_type": "lora"})
            delete = await client.delete(f"/api/models/{flux.id}/type")

    assert (put.status_code, delete.status_code) == (200, 200)
    header_spy.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_model_is_not_found(app):
    async with client_for(app) as client:
        put = await client.put("/api/models/nope/type", json={"model_type": "lora"})
        delete = await client.delete("/api/models/nope/type")

    assert (put.status_code, delete.status_code) == (404, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_type", ["llm", "undefined", "nonsense", ""])
async def test_types_that_cannot_be_assigned_are_unprocessable(app, flux, model_type):
    async with client_for(app) as client:
        response = await client.put(f"/api/models/{flux.id}/type", json={"model_type": model_type})

    assert response.status_code == 422
    assert model_repo.get_by_id(flux.id).type_source == "folder"


@pytest.mark.asyncio
async def test_a_model_without_a_hash_is_unprocessable(app, mock_db):
    bare = model_repo.create(Model(filename="bare.safetensors", model_type="lora"))

    async with client_for(app) as client:
        put = await client.put(f"/api/models/{bare.id}/type", json={"model_type": "checkpoint"})
        delete = await client.delete(f"/api/models/{bare.id}/type")

    assert put.status_code == 422
    assert delete.status_code == 422


@pytest.mark.asyncio
async def test_a_directory_model_is_unprocessable(app, mock_db):
    directory = model_repo.create(
        Model(filename="chat", model_type="llm", sha256="d" * 64, is_directory=True)
    )

    async with client_for(app) as client:
        response = await client.put(f"/api/models/{directory.id}/type", json={"model_type": "checkpoint"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_name_collision_is_a_conflict_naming_the_other_model(lib, app, flux):
    other = model_repo.create(
        Model(filename="flux-dev.safetensors", model_type="diffusion_model", sha256="e" * 64)
    )

    async with client_for(app) as client:
        response = await client.put(f"/api/models/{flux.id}/type", json={"model_type": "diffusion_model"})

    assert response.status_code == 409
    assert other.id in response.json()["detail"]["message"]
    assert ModelTypeRepository().get_assertions([flux.sha256]) == {}
    assert model_repo.get_by_id(flux.id).model_type == "checkpoint"


@pytest.mark.asyncio
async def test_a_reset_that_would_collide_is_a_conflict_and_keeps_the_assertion(lib, app, flux):
    async with client_for(app) as client:
        await client.put(f"/api/models/{flux.id}/type", json={"model_type": "vae"})
        lib.set_scan("checkpoint", True)
        lib.index()
        model_repo.create(Model(filename="flux-dev.safetensors", model_type="diffusion_model", sha256="e" * 64))

        response = await client.delete(f"/api/models/{flux.id}/type")

    assert response.status_code == 409
    assert set(ModelTypeRepository().get_assertions([flux.sha256])) == {flux.sha256}
    assert model_repo.get_by_id(flux.id).model_type == "vae"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["put", "delete"])
async def test_only_admins_may_change_a_type(lib, flux, method):
    def forbidden():
        raise HTTPException(status_code=403, detail="forbidden")

    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(SimpleNamespace(model_controller=Mock())))
    fastapi_app.dependency_overrides[get_current_admin_user] = forbidden
    fastapi_app.dependency_overrides[get_current_active_user] = lambda: Mock(id="u")
    async with client_for(fastapi_app) as client:
        call = getattr(client, method)
        kwargs = {"json": {"model_type": "lora"}} if method == "put" else {}
        response = await call(f"/api/models/{flux.id}/type", **kwargs)

    assert response.status_code == 403


def test_type_info_describes_the_verdict_and_the_folder(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    model = lib.model("flux-dev.safetensors")
    catalog = ModelCatalog(
        model_repo, Mock(), lib.scanner,
        user_attribute_repository=Mock(get_map=Mock(return_value={})),
        locator=ModelLocator(lib.scanner.resolver, lib.scanner.locations),
    )

    info = catalog.get_model_by_id(model.id, admin=True)["model"]["type_info"]

    assert info["source"] == "header"
    assert info["folder_type"] == "checkpoint"
    assert (info["family"], info["variant"], info["classifier"]) == ("flux", "flux1", "core.native_dit")
    assert info["components"] == ["denoiser"]
    assert info["verdict_status"] == "decided"
    assert info["packaging"] is None


def test_type_info_marks_an_extractable_full_checkpoint(lib):
    from tests.fixtures.family_shape_fixtures import family_shapes
    from tests.fixtures.model_header_fixtures import tensor_specs

    shapes = {f"model.diffusion_model.{k}": v for k, v in family_shapes("flux1").items()}
    shapes["vae.decoder.a"] = (4,)
    shapes["vae.decoder.b"] = (4,)
    lib.put(lib.checkpoints, "flux-all.safetensors", tensor_specs(shapes))
    lib.index()
    model = lib.model("flux-all.safetensors")
    catalog = ModelCatalog(
        model_repo, Mock(), lib.scanner,
        user_attribute_repository=Mock(get_map=Mock(return_value={})),
        locator=ModelLocator(lib.scanner.resolver, lib.scanner.locations),
    )

    info = catalog.get_model_by_id(model.id, admin=True)["model"]["type_info"]

    assert info["packaging"] == "full_checkpoint"
    assert info["components"] == ["denoiser", "vae"]


def test_type_info_is_admin_only(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    model = lib.model("flux-dev.safetensors")
    catalog = ModelCatalog(
        model_repo, Mock(), lib.scanner,
        user_attribute_repository=Mock(get_map=Mock(return_value={})),
        locator=ModelLocator(lib.scanner.resolver, lib.scanner.locations),
    )

    assert "type_info" not in catalog.get_model_by_id(model.id, admin=False)["model"]


def test_type_info_for_a_file_that_was_never_classified(lib):
    lib.set_scan("checkpoint", False)
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    model = lib.model("flux-dev.safetensors")
    catalog = ModelCatalog(
        model_repo, Mock(), lib.scanner,
        user_attribute_repository=Mock(get_map=Mock(return_value={})),
        locator=ModelLocator(lib.scanner.resolver, lib.scanner.locations),
    )

    info = catalog.get_model_by_id(model.id, admin=True)["model"]["type_info"]

    assert info["source"] == "folder"
    assert info["verdict_status"] is None
    assert info["family"] is None
    assert info["components"] == []


@pytest.mark.asyncio
async def test_delete_keeps_the_download_pin_and_falls_back_to_it(app, flux):
    ModelTypeRepository().put_assertion(flux.sha256, "diffusion_model", "download", None, now_iso())
    async with client_for(app) as client:
        await client.put(f"/api/models/{flux.id}/type", json={"model_type": "vae"})
        assert model_repo.get_by_id(flux.id).type_source == "admin"

        response = await client.delete(f"/api/models/{flux.id}/type")

    assert response.status_code == 200
    stored = model_repo.get_by_id(flux.id)
    assert (stored.model_type, stored.type_source) == ("diffusion_model", "download")
    assert set(ModelTypeRepository().get_assertion_rows(flux.sha256)) == {"download"}
    assert response.json()["data"]["model"]["type_info"]["source"] == "download"
