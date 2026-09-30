from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.model_layouts.catalog import ModelLayoutCatalog
from src.features.models.indexer import ModelScanner
from src.features.models.locator import ModelFileUnavailable, ModelLocator
from src.features.models.repository import model_repo
from src.features.models.roots import ModelRootsManager
from src.features.models.roots_routes import build_router
from src.platform.filesystem.model_roots import ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import AccountType, User
from tests.fixtures.model_header_fixtures import tensor_specs, write_safetensors
from tests.fixtures.model_index_fixtures import FLUX, LORA, SDXL_ALL_IN_ONE

OTHER_LORA = tensor_specs({"lora_unet_a.lora_down.weight": (8, 4), "lora_unet_a.lora_up.weight": (4, 8)})

CATALOG_DIR = "content/model-layouts"


class _Coordinator:
    def cancel_and_restart(self, trigger="location_change"):
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


@pytest.fixture
def journey(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    resolver = ModelRootResolver(repository, probe, tmp_path)
    manager = ModelRootsManager(
        repository=repository,
        resolver=resolver,
        probe=probe,
        indexing_coordinator=_Coordinator(),
        base_dir=tmp_path,
    )
    container = SimpleNamespace(
        model_roots_manager=manager,
        model_index_manager=SimpleNamespace(indexing=_Coordinator()),
        model_layout_catalog=ModelLayoutCatalog(CATALOG_DIR),
    )
    admin = User(id="a1", username="a", email="a@example.com", password_hash="h", account_type=AccountType.ADMIN)
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return SimpleNamespace(
        client=TestClient(app),
        repository=repository,
        resolver=resolver,
        scanner=ModelScanner(resolver),
        locator=ModelLocator(resolver),
        base=tmp_path,
    )


def _stabilitymatrix(base):
    for folder in ("StableDiffusion", "Lora", "LyCORIS", "Embeddings"):
        (base / "Data" / "Models" / folder).mkdir(parents=True)
    (base / "Data" / ".sm-portable").write_text("", encoding="utf-8")
    (base / "Data" / "Packages").mkdir()
    return base / "Data" / "Models"


def _create_from_detection(journey, path, write_subdir=None):
    detected = journey.client.post("/api/models/roots/detect", json={"path": str(path)}).json()["data"]
    bindings = [
        {
            "model_type": s["model_type"],
            "subdir": s["subdir"],
            "scan_headers": s["scan_headers"],
            "write": (s["subdir"] == write_subdir) if write_subdir else s["write"],
        }
        for s in detected["suggestions"]
    ]
    created = journey.client.post(
        "/api/models/roots",
        json={"path": detected["root_path"], "bindings": bindings, "profile": detected["profile"]["id"]},
    )
    return detected, created


def _type_of(filename):
    model = model_repo.get_by_filename(filename)[0]
    return model.model_type, model.type_source


def test_a_stabilitymatrix_tree_detected_then_created_then_indexed_by_header(journey):
    models = _stabilitymatrix(journey.base)
    write_safetensors(models / "StableDiffusion" / "flux.safetensors", FLUX)
    write_safetensors(models / "StableDiffusion" / "sdxl.safetensors", SDXL_ALL_IN_ONE)
    write_safetensors(models / "Lora" / "a.safetensors", LORA)
    write_safetensors(models / "LyCORIS" / "b.safetensors", OTHER_LORA)

    detected, created = _create_from_detection(journey, journey.base, write_subdir="Data/Models/LyCORIS")

    assert detected["profile"]["id"] == "stabilitymatrix"
    assert created.status_code == 201
    root = created.json()["data"]
    assert root["layout_profile"] == "stabilitymatrix"
    lora = [b for b in root["bindings"] if b["model_type"] == "lora"]
    assert [(b["subdir"], b["is_write"]) for b in lora] == [
        ("Data/Models/Lora", False),
        ("Data/Models/LyCORIS", True),
    ]

    journey.scanner.index_models(max_workers=1)

    assert _type_of("flux.safetensors") == ("diffusion_model", "header")
    assert _type_of("sdxl.safetensors") == ("checkpoint", "header")
    assert _type_of("a.safetensors")[0] == _type_of("b.safetensors")[0] == "lora"
    assert journey.resolver.write_dir("lora").path == models / "LyCORIS"


def test_removing_a_folder_through_the_route_keeps_the_sibling_files_and_moves_writes(journey):
    models = _stabilitymatrix(journey.base)
    write_safetensors(models / "Lora" / "a.safetensors", LORA)
    kept = write_safetensors(models / "LyCORIS" / "b.safetensors", OTHER_LORA)
    _, created = _create_from_detection(journey, journey.base)
    root_id = created.json()["data"]["id"]
    journey.scanner.index_models(max_workers=1)
    assert journey.resolver.write_dir("lora").path == models / "Lora"

    removed = journey.client.patch(
        f"/api/models/roots/{root_id}",
        json={"remove_bindings": [{"model_type": "lora", "subdir": "Data/Models/Lora"}]},
    )
    journey.scanner.index_models(max_workers=1)

    assert removed.status_code == 200
    assert journey.resolver.write_dir("lora").path == models / "LyCORIS"
    assert journey.locator.path_for_model(model_repo.get_by_filename("b.safetensors")[0].id) == kept
    with pytest.raises(ModelFileUnavailable):
        journey.locator.path_for_model(model_repo.get_by_filename("a.safetensors")[0].id)


def test_a_comfyui_extra_root_is_offered_not_created_and_can_be_created_separately(journey):
    comfy = journey.base / "comfy"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "folder_paths.py").write_text("", encoding="utf-8")
    (comfy / "models" / "loras").mkdir(parents=True)
    outside = journey.base / "shared" / "models"
    (outside / "checkpoints").mkdir(parents=True)
    write_safetensors(outside / "checkpoints" / "s.safetensors", SDXL_ALL_IN_ONE)
    (comfy / "extra_model_paths.yaml").write_text(
        f"shared:\n    base_path: {outside.as_posix()}\n    checkpoints: checkpoints\n", encoding="utf-8"
    )

    detected, created = _create_from_detection(journey, comfy)

    assert detected["profile"]["id"] == "comfyui"
    assert created.status_code == 201
    extra = detected["extra_roots"][0]
    assert extra["path"] == str(outside)
    paths = [r["path"] for r in journey.client.get("/api/models/roots").json()["data"]["roots"]]
    assert str(outside) not in paths

    offered = [
        {"model_type": s["model_type"], "subdir": s["subdir"], "scan_headers": s["scan_headers"]}
        for s in extra["suggestions"]
    ]
    second = journey.client.post("/api/models/roots", json={"path": extra["path"], "bindings": offered})
    journey.scanner.index_models(max_workers=1)

    assert second.status_code == 201
    assert _type_of("s.safetensors") == ("checkpoint", "folder")
