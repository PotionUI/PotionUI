from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.features.models.type_manager import ModelTypeManager
from src.platform.runtime.model_headers import HeaderResult, HeaderStatus
from tests.fixtures.model_header_fixtures import write_safetensors
from tests.fixtures.model_index_fixtures import FLUX, LORA, Library, add_binding, sha_of


class Plugins:
    def execute_hook(self, *args, **kwargs):
        return None, True


def coordinator_for(lib):
    types = ModelTypeManager(model_repo, lib.scanner.types, lib.scanner.recompute_types)
    coordinator = ModelIndexingCoordinator(
        model_repo, MagicMock(), lib.scanner, spawn=lambda target: target(), type_manager=types,
    )
    return coordinator, types


@pytest.fixture
def lib(tmp_path, mock_db):
    return Library(tmp_path, scan_checkpoints=False)


def test_a_download_is_classified_even_when_the_folder_does_not_scan_headers(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator, _ = coordinator_for(lib)

    result = coordinator.index_downloaded(str(path))

    assert result["indexed"] is True and result["warning"] is None
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("diffusion_model", "download")
    assert path.exists()
    assert lib.scanner.types.get_assertions([sha_of(path)])[sha_of(path)]["source"] == "download"


def test_a_plain_index_of_the_same_file_keeps_the_folder_type(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator, _ = coordinator_for(lib)

    coordinator.index_path(str(path))

    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"


def test_an_admin_type_is_never_overridden_by_a_download(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator, types = coordinator_for(lib)
    coordinator.index_path(str(path))
    types.assert_type(sha_of(path), "checkpoint", "admin")

    coordinator.index_downloaded(str(path))

    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"
    assert lib.scanner.types.get_assertions([sha_of(path)])[sha_of(path)]["source"] == "admin"


def test_a_file_no_classifier_recognises_keeps_the_folder_type_without_a_warning(lib):
    path = lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    coordinator, _ = coordinator_for(lib)

    result = coordinator.index_downloaded(str(path))

    assert result["warning"] is None
    model = lib.model("mystery.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")
    assert lib.scanner.types.get_assertions([sha_of(path)]) == {}


def test_a_file_without_a_header_format_is_not_classified(lib):
    path = lib.checkpoints / "old.ckpt"
    path.write_bytes(b"pickle bytes")
    coordinator, _ = coordinator_for(lib)

    with patch("src.features.models.indexer.read_header") as spy:
        coordinator.index_downloaded(str(path))

    spy.assert_not_called()
    assert lib.model("old.ckpt").model_type == "checkpoint"


def test_a_folder_type_outside_the_header_classified_set_is_left_alone(lib):
    lib.bindings["lora"] = add_binding("r1", "lora", lib.base / "loras", 0, scan_headers=False)
    (lib.base / "loras").mkdir()
    lib.scanner = lib.new_scanner()
    path = lib.put(lib.base / "loras", "flux-dev.safetensors", FLUX)
    coordinator, _ = coordinator_for(lib)

    with patch("src.features.models.indexer.read_header") as spy:
        result = coordinator.index_downloaded(str(path))

    spy.assert_not_called()
    assert result["warning"] is None
    assert lib.model("flux-dev.safetensors").model_type == "lora"
    assert lib.scanner.types.get_assertions([sha_of(path)]) == {}


def test_a_header_read_failure_warns_and_still_indexes_the_file(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator, _ = coordinator_for(lib)
    failing = HeaderResult(HeaderStatus.IO_ERROR, error="disk went away")

    with patch("src.features.models.indexer.read_header", return_value=failing):
        result = coordinator.index_downloaded(str(path))

    assert result["indexed"] is True
    assert "disk went away" in result["warning"]
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"


def test_a_name_collision_keeps_the_folder_type_and_says_so(lib):
    model_repo.create(
        Model(id=None, filename="flux-dev.safetensors", file_size=1, sha256="f" * 64, model_type="diffusion_model")
    )
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator, _ = coordinator_for(lib)

    result = coordinator.index_downloaded(str(path))

    assert "another model named" in result["warning"]
    assert lib.scanner.types.get_assertions([sha_of(path)]) == {}


def test_without_a_type_manager_a_download_indexes_like_any_file(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    coordinator = ModelIndexingCoordinator(
        model_repo, MagicMock(), lib.scanner, spawn=lambda target: target(),
    )

    result = coordinator.index_downloaded(str(path))

    assert result["warning"] is None
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"
