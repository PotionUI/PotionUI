from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.container import _bind_model_types
from src.features.models.indexer import ModelScanner
from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.repository import model_repo
from src.features.models.roots import ModelRootsManager
from src.features.models.routes import build_router
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import HOME_ROOT_ID, ModelRootResolver, root_path_key
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import model_type_registry, model_types, type_for_folder_name
from src.platform.templating.dict_utils import strip_model_dir
from src.platform.http.base_controller import BaseController
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.security.current_user import get_current_active_user
from tests.features.models.fakes import FakeIndexingCoordinator

PLUGIN_ID = "pose-rig-types"

MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Pose rig types",
    "version": "1.0.0",
    "description": "d",
    "author": "a",
    "type": "backend-only",
    "model_types": [
        {
            "id": "pose_rig",
            "label": "Pose rig",
            "purpose": "Skeleton for pose transfer",
            "folder": "pose_rigs",
            "aliases": ["PoseRigs"],
            "extensions": ["onnx"],
        }
    ],
}


class OnlineProbe:
    def state(self, root):
        return ("online", None)


class FakeSettings:
    def __init__(self, models_dir: Path):
        self._models_dir = str(models_dir)

    def get_setting(self, key, default=None):
        return self._models_dir if key == "models_dir" else default


def _write_plugin(base: Path, manifest: dict) -> None:
    plugin_dir = base / "marketplace" / manifest["id"]
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


@pytest.fixture
def home(tmp_path, mock_db):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    repository = ModelRootRepository()
    repository.update_root(HOME_ROOT_ID, path=str(models_dir), path_key=root_path_key(str(models_dir)), now=now_iso())
    resolver = ModelRootResolver(repository, OnlineProbe(), tmp_path)
    previous = dict(model_type_registry._listeners)
    model_type_registry._listeners.clear()
    model_type_registry.set_listener("model_roots", lambda: _bind_model_types(repository, resolver, FakeSettings(models_dir)))
    yield SimpleNamespace(models_dir=models_dir, repository=repository, resolver=resolver)
    model_type_registry.unregister_source(PLUGIN_ID)
    model_type_registry._listeners.clear()
    model_type_registry._listeners.update(previous)


@pytest.fixture
def plugins(tmp_path):
    _write_plugin(tmp_path / "plugins", MANIFEST)
    registry = PluginRegistry(
        marketplace_dir=str(tmp_path / "plugins" / "marketplace"),
        local_dir=str(tmp_path / "plugins" / "local"),
    )
    registry.discover_plugins()
    return registry


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(model_controller=BaseController(), model_type_registry=model_type_registry)))
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id="u1", is_admin=False)
    return TestClient(app)


def _served_ids() -> list:
    response = _client().get("/api/models/type-definitions")
    assert response.status_code == 200
    return [entry["id"] for entry in response.json()["data"]["types"]]


def test_enabling_the_plugin_registers_its_type_and_binds_its_folder_under_the_home_root(home, plugins):
    assert plugins.enable_plugin(PLUGIN_ID) is True

    assert "pose_rig" in model_types()
    assert type_for_folder_name("PoseRigs") == "pose_rig"
    binding = next(b for b in home.repository.list_bindings("pose_rig") if b["root_id"] == HOME_ROOT_ID)
    assert binding["subdir"] == "pose_rigs"
    assert binding["is_write"]
    assert home.resolver.write_dir("pose_rig").path == home.models_dir / "pose_rigs"


def test_a_file_in_the_plugin_folder_is_indexed_as_the_plugin_type(home, plugins):
    plugins.enable_plugin(PLUGIN_ID)
    folder = home.models_dir / "pose_rigs"
    folder.mkdir()
    (folder / "rig.onnx").write_bytes(b"rig-bytes")
    (folder / "ignored.safetensors").write_bytes(b"not an onnx file")

    ModelScanner(home.resolver).index_models(max_workers=1)

    rows = model_repo.get_by_filename("rig.onnx")
    assert [row.model_type for row in rows] == ["pose_rig"]
    assert model_repo.get_by_filename("ignored.safetensors") == []


def test_the_type_list_endpoint_serves_the_plugin_type_with_its_label(home, plugins):
    plugins.enable_plugin(PLUGIN_ID)

    response = _client().get("/api/models/type-definitions")

    entries = {entry["id"]: entry for entry in response.json()["data"]["types"]}
    assert entries["pose_rig"]["label"] == "Pose rig"
    assert entries["pose_rig"]["folder"] == "pose_rigs"
    assert entries["pose_rig"]["extensions"] == [".onnx"]
    assert entries["pose_rig"]["source"] == PLUGIN_ID
    assert entries["checkpoint"]["source"] == "core"
    assert entries["cloud"]["virtual"] is True


def test_disabling_the_plugin_hides_its_type_and_keeps_existing_rows(home, plugins):
    plugins.enable_plugin(PLUGIN_ID)
    folder = home.models_dir / "pose_rigs"
    folder.mkdir()
    (folder / "rig.onnx").write_bytes(b"rig-bytes")
    scanner = ModelScanner(home.resolver)
    scanner.index_models(max_workers=1)

    assert plugins.disable_plugin(PLUGIN_ID) is True

    assert plugins.get_plugin_state(PLUGIN_ID) == PluginState.DISABLED
    assert "pose_rig" not in model_types()
    assert "pose_rig" not in _served_ids()
    assert home.resolver.type_dirs("pose_rig") == []
    assert home.resolver.to_logical(folder / "rig.onnx") is None
    assert all(found.model_type != "pose_rig" for found in scanner.scan_roots())

    scanner.index_models(max_workers=1)

    rows = model_repo.get_by_filename("rig.onnx")
    assert [row.model_type for row in rows] == ["pose_rig"]
    assert scanner.location_path({"root_id": HOME_ROOT_ID, "model_type": "pose_rig", "rel_path": "rig.onnx"}) is None


def test_re_enabling_reuses_the_existing_home_binding(home, plugins):
    plugins.enable_plugin(PLUGIN_ID)
    plugins.disable_plugin(PLUGIN_ID)
    plugins.enable_plugin(PLUGIN_ID)

    assert len([b for b in home.repository.list_bindings("pose_rig") if b["root_id"] == HOME_ROOT_ID]) == 1
    assert home.resolver.write_dir("pose_rig").path == home.models_dir / "pose_rigs"


def test_a_plugin_type_that_collides_with_a_core_folder_fails_the_enable_with_the_plugin_named(tmp_path, home):
    _write_plugin(tmp_path / "bad", {**MANIFEST, "id": "loras-again", "model_types": [
        {"id": "my_lora", "label": "My LoRA", "folder": "loras"},
    ]})
    registry = PluginRegistry(marketplace_dir=str(tmp_path / "bad" / "marketplace"), local_dir=str(tmp_path / "bad" / "local"))
    registry.discover_plugins()

    assert registry.enable_plugin("loras-again") is False

    assert registry.get_plugin_state("loras-again") == PluginState.ERROR
    assert "loras-again" in registry.get_plugin_error("loras-again")
    assert "my_lora" not in model_types()


def test_the_roots_overview_lists_the_plugin_folder_only_while_the_plugin_is_enabled(home, plugins, tmp_path):
    manager = ModelRootsManager(
        repository=home.repository, resolver=home.resolver, probe=OnlineProbe(),
        indexing_coordinator=FakeIndexingCoordinator(), base_dir=tmp_path,
    )
    plugins.enable_plugin(PLUGIN_ID)

    enabled = manager.get_overview()

    assert {t["model_type"]: t["folder"] for t in enabled["types"]}["pose_rig"] == "pose_rigs"
    plugins.disable_plugin(PLUGIN_ID)

    disabled = manager.get_overview()

    assert "pose_rig" not in [t["model_type"] for t in disabled["types"]]
    home_root = next(root for root in disabled["roots"] if root["id"] == HOME_ROOT_ID)
    assert "pose_rig" not in [binding["model_type"] for binding in home_root["bindings"]]


def test_templates_strip_the_plugin_folder_only_while_the_plugin_is_enabled(home, plugins):
    plugins.enable_plugin(PLUGIN_ID)

    assert strip_model_dir("models/pose_rigs/body/rig.onnx") == "body/rig.onnx"
    plugins.disable_plugin(PLUGIN_ID)

    assert strip_model_dir("models/pose_rigs/body/rig.onnx") == "models/pose_rigs/body/rig.onnx"


def test_a_plugin_folder_linked_outside_the_models_root_is_indexed_only_under_the_root(home, plugins, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "rig.onnx").write_bytes(b"rig-bytes-outside")
    try:
        (home.models_dir / "pose_rigs").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    plugins.enable_plugin(PLUGIN_ID)
    scanner = ModelScanner(home.resolver)

    scanner.index_models(max_workers=1)

    (row,) = model_repo.get_by_filename("rig.onnx")
    locations = ModelLocationsRepository().list_for_model(row.id)
    assert [(loc["root_id"], loc["rel_path"]) for loc in locations] == [(HOME_ROOT_ID, "rig.onnx")]
    resolved = scanner.location_path({**locations[0], "model_type": "pose_rig"})
    assert resolved == home.models_dir / "pose_rigs" / "rig.onnx"
    assert str(outside) not in repr(locations)
    assert str(outside) not in repr(vars(row))
    assert home.resolver.to_logical(outside / "rig.onnx") is None
    logical = home.resolver.to_logical(home.models_dir / "pose_rigs" / "rig.onnx")
    assert (logical.root_id, logical.model_type, logical.rel_path) == (HOME_ROOT_ID, "pose_rig", "rig.onnx")

