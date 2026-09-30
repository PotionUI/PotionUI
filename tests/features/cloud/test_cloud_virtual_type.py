from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.downloads.exceptions import DownloadQueueException
from src.features.downloads.queue import DownloadQueue
from src.features.fields.model import Model as ModelField
from src.features.model_layouts.schema import validate_layout_dict
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.catalog import ModelCatalog
from src.features.models.exceptions import ModelIndexingException, ModelTypeNotAssignableException
from src.features.models.locator import ModelLocator
from src.features.models.metadata_editor import ModelMetadataEditor
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.features.models.roots import BindingSpec, InvalidBindingError, ModelRootsManager
from src.features.models.routes import ModelController, build_router
from src.features.models.type_manager import ModelTypeManager, assignable_types
from src.platform.filesystem.model_roots import ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import (
    DIRECTORY_TO_MODEL_TYPE,
    MODEL_DIRECTORY_NAMES,
    MODEL_TYPE_TO_DIRECTORY,
    MODEL_TYPES,
    PICKABLE_MODEL_TYPES,
    VIRTUAL_MODEL_TYPES,
    type_for_folder_name,
)
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.settings.repository import SettingRepository
from tests.features.model_layouts.helpers import layout_data
from tests.fixtures.model_index_fixtures import LORA, Library
from tests.fixtures.model_roots import make_roots


@pytest.fixture
def cloud_model(mock_db):
    return model_repo.create(Model(filename="fake~fake~image-1", model_type="cloud"))


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


def test_cloud_is_a_virtual_type_that_the_depot_never_knows_about():
    assert VIRTUAL_MODEL_TYPES == ("cloud",)
    assert "cloud" not in MODEL_TYPES
    assert "cloud" not in MODEL_TYPE_TO_DIRECTORY
    assert "cloud" not in DIRECTORY_TO_MODEL_TYPE.values()
    assert "cloud" not in MODEL_DIRECTORY_NAMES
    assert type_for_folder_name("cloud") is None


def test_the_pickable_types_are_the_depot_types_plus_the_virtual_ones():
    assert PICKABLE_MODEL_TYPES == (*MODEL_TYPES, "cloud")


def test_the_model_field_offers_cloud_as_a_model_type_to_pick():
    spec = {item.name: item for item in ModelField.configuration()}["model_type"]
    assert "cloud" in spec.choices
    assert set(MODEL_TYPES) <= set(spec.choices)


def test_no_type_can_be_assigned_to_a_model_as_cloud():
    assert "cloud" not in assignable_types()


def test_the_resolver_has_no_directory_for_cloud(tmp_path):
    resolver = make_roots(tmp_path)

    assert resolver.type_dirs("cloud", online_only=False) == []


@pytest.fixture
def roots_manager(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    return ModelRootsManager(
        repository=repository,
        resolver=ModelRootResolver(repository, probe, tmp_path),
        probe=probe,
        indexing_coordinator=Mock(),
        setting_repository=SettingRepository(),
        base_dir=tmp_path,
    )


def test_a_root_cannot_bind_a_folder_to_cloud(roots_manager, tmp_path):
    folder = tmp_path / "library"
    (folder / "cloud").mkdir(parents=True)

    with pytest.raises(InvalidBindingError):
        roots_manager.create_root(str(folder), bindings=[BindingSpec("cloud", "cloud")])


def test_a_root_can_still_bind_a_depot_type_next_to_the_refusal(roots_manager, tmp_path):
    folder = tmp_path / "library"
    (folder / "loras").mkdir(parents=True)

    result = roots_manager.create_root(str(folder), bindings=[BindingSpec("lora", "loras")])

    assert {b["model_type"] for b in result["bindings"]} == {"lora"}


def test_a_layout_cannot_place_a_folder_under_cloud():
    data = layout_data()
    data["folders"] = [{"path": "cloud", "model_type": "cloud"}]

    issues = validate_layout_dict(data)

    assert any("model_type" in issue and "cloud" in issue for issue in issues)


def test_a_layout_with_a_depot_type_folder_is_accepted():
    data = layout_data()
    data["folders"] = [{"path": "loras", "model_type": "lora"}]

    assert validate_layout_dict(data) == []


def test_a_download_cannot_target_the_cloud_type(tmp_path):
    queue = DownloadQueue.__new__(DownloadQueue)
    queue.resolver = make_roots(tmp_path)

    assert queue._write_root_dir("lora").name == "loras"
    with pytest.raises(DownloadQueueException):
        queue._write_root_dir("cloud")


def test_the_scanner_does_not_index_cloud(lib):
    assert "cloud" not in lib.scanner.MODEL_TYPE_MAPPING.values()
    assert "cloud" not in lib.scanner.get_indexing_status()["by_type"]


def test_a_scan_leaves_a_cloud_row_alone_and_does_not_hash_or_retype_it(lib, cloud_model):
    lib.put(lib.checkpoints, "a.safetensors", LORA)

    lib.index()

    row = model_repo.get_by_id(cloud_model.id)
    assert (row.model_type, row.is_available, row.sha256, row.unavailable_at) == ("cloud", True, None, None)
    assert model_repo.get_by_identity("cloud", "fake~fake~image-1") is not None


def test_a_cloud_row_is_not_counted_as_a_model_missing_its_hash(cloud_model):
    assert cloud_model.id not in {m.id for m in model_repo.get_models_missing_hashes()}


def test_a_cloud_row_is_not_offered_to_the_marketplace_info_fetch(cloud_model):
    assert cloud_model.id not in {m.id for m in model_repo.get_models_without_provider_info("civitai")}


def test_a_fileless_depot_row_is_still_counted_so_the_exclusion_is_only_for_cloud(mock_db):
    lora = model_repo.create(Model(filename="a.safetensors", model_type="lora"))

    assert lora.id in {m.id for m in model_repo.get_models_missing_hashes()}
    assert lora.id in {m.id for m in model_repo.get_models_without_provider_info("civitai")}


async def test_setting_the_type_of_a_cloud_model_is_refused(app, cloud_model):
    async with client_for(app) as client:
        response = await client.put(f"/api/models/{cloud_model.id}/type", json={"model_type": "lora"})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "model_type_not_assignable"
    assert "provider" in response.json()["detail"]["message"]
    assert model_repo.get_by_id(cloud_model.id).model_type == "cloud"


async def test_setting_a_model_to_the_cloud_type_is_refused(app, lib):
    lib.put(lib.checkpoints, "a.safetensors", LORA)
    lib.index()
    model = lib.model("a.safetensors")

    async with client_for(app) as client:
        response = await client.put(f"/api/models/{model.id}/type", json={"model_type": "cloud"})

    assert response.status_code == 422
    assert model_repo.get_by_id(model.id).model_type != "cloud"


async def test_resetting_the_type_of_a_cloud_model_is_refused(app, cloud_model):
    async with client_for(app) as client:
        response = await client.delete(f"/api/models/{cloud_model.id}/type")

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "model_type_not_assignable"


def test_the_type_manager_refuses_a_cloud_model_before_looking_for_a_hash(lib, cloud_model):
    manager = ModelTypeManager(model_repo, lib.scanner.types, lib.scanner.recompute_types)

    with pytest.raises(ModelTypeNotAssignableException, match="offered by a provider"):
        manager.set_admin_type(cloud_model.id, "lora", "admin-1")


def test_deleting_a_cloud_model_from_the_index_is_refused_and_points_at_the_catalog(cloud_model):
    editor = ModelMetadataEditor.__new__(ModelMetadataEditor)
    editor.model_repo = model_repo
    editor.plugins = Mock()

    with pytest.raises(ModelIndexingException, match="catalog"):
        editor.delete_model(cloud_model.id)

    assert model_repo.get_by_id(cloud_model.id) is not None
