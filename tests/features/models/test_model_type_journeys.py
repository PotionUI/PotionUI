from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from src.features.models import availability as av
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.models.repository import model_repo
from src.features.models.type_manager import ModelTypeManager
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import tensor_specs
from tests.fixtures.model_index_fixtures import FLUX, LORA, SDXL_ALL_IN_ONE, Library

PREFIX = "model.diffusion_model."
FLUX_ALL_IN_ONE = tensor_specs(
    {
        **{PREFIX + key: shape for key, shape in family_shapes("flux1").items()},
        "vae.decoder.a": (4,),
        "vae.decoder.b": (4,),
        "text_encoders.clip_l.a": (4,),
        "text_encoders.clip_l.b": (4,),
    }
)


def types_for(lib):
    return ModelTypeManager(model_repo, lib.scanner.types, lib.scanner.recompute_types)


def native_picker():
    registry = Mock()
    registry.get_backends_for_engine.return_value = [Mock(backend_id="b1")]
    registry.engine_extracts_diffusion_model_from_checkpoint.return_value = True
    entries = av.models_for_engine("native", registry, model_type="diffusion_model")
    return sorted(entry["filename"] for entry in entries)


@pytest.fixture
def lib(tmp_path, mock_db):
    return Library(tmp_path)


def test_a_forge_folder_is_typed_by_content_listed_in_the_native_picker_and_keeps_an_admin_retype(lib):
    lib.put(lib.checkpoints, "flux-aio.safetensors", FLUX_ALL_IN_ONE)
    lib.put(lib.checkpoints, "flux-transformer.safetensors", FLUX)
    lib.put(lib.checkpoints, "sdxl.safetensors", SDXL_ALL_IN_ONE)
    lib.put(lib.checkpoints, "stray-lora.safetensors", LORA)

    lib.index()

    names = ("flux-aio", "flux-transformer", "sdxl", "stray-lora")
    assert {f"{n}.safetensors": lib.model(f"{n}.safetensors").model_type for n in names} == {
        "flux-aio.safetensors": "checkpoint",
        "flux-transformer.safetensors": "diffusion_model",
        "sdxl.safetensors": "checkpoint",
        "stray-lora.safetensors": "undefined",
    }
    assert native_picker() == ["flux-aio.safetensors", "flux-transformer.safetensors"]

    types_for(lib).set_admin_type(lib.model("flux-transformer.safetensors").id, "vae", None)
    lib.index()
    lib.index()

    retyped = lib.model("flux-transformer.safetensors")
    assert (retyped.model_type, retyped.type_source) == ("vae", "admin")
    assert native_picker() == ["flux-aio.safetensors"]


def test_a_download_assertion_yields_to_an_admin_override_and_a_reset_falls_back_to_the_download(lib):
    lib.set_scan("checkpoint", False)
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    types = types_for(lib)
    coordinator = ModelIndexingCoordinator(
        model_repo, MagicMock(), lib.scanner, spawn=lambda target: target(), type_manager=types,
    )

    coordinator.index_downloaded(str(path))
    downloaded = lib.model("flux-dev.safetensors")
    assert (downloaded.model_type, downloaded.type_source) == ("diffusion_model", "download")

    types.set_admin_type(downloaded.id, "vae", None)
    lib.index()
    coordinator.index_downloaded(str(path))
    overridden = lib.model("flux-dev.safetensors")
    assert (overridden.model_type, overridden.type_source) == ("vae", "admin")
    assert set(lib.scanner.types.get_assertion_rows(downloaded.sha256)) == {"admin", "download"}

    types.reset_admin_type(downloaded.id)
    restored = lib.model("flux-dev.safetensors")
    assert (restored.model_type, restored.type_source) == ("diffusion_model", "download")
    assert set(lib.scanner.types.get_assertion_rows(downloaded.sha256)) == {"download"}
    lib.index()
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"
    assert native_picker() == ["flux-dev.safetensors"]
