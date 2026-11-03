from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.features.backends.backend_config import (
    NATIVE_LOCAL_DRIVER,
    TEXT_ENCODER_PLACEMENTS,
    BackendConfigStore,
    NativeBackendConfig,
    NativeRemoteBackendConfig,
)
from src.features.backends.backend_registry import BackendRegistry
from src.features.backends.native_backend import NativeBackend
from src.features.backends.records import Backend
from src.features.backends.routes import BackendController
from src.platform.runtime.native.memory import text_encoder as te_settings
from src.platform.settings.settings import Settings


@pytest.fixture(autouse=True)
def _reset_engine_settings():
    te_settings.reset_text_encoder_placement()
    yield
    te_settings.reset_text_encoder_placement()


def _config(**overrides) -> NativeBackendConfig:
    return NativeBackendConfig(id="native", name="Local Generation", device="cuda:0", gpu_max_vram=10, **overrides)


def test_default_is_auto():
    assert _config().text_encoder_placement == "auto"


def test_choices_mirror_the_engine_modes():
    assert TEXT_ENCODER_PLACEMENTS == te_settings.TEXT_ENCODER_PLACEMENT_MODES


@pytest.mark.parametrize("raw,expected", [("GPU", "gpu"), (" stream ", "stream"), ("", "auto"), (None, "auto")])
def test_values_are_normalised(raw, expected):
    assert _config(text_encoder_placement=raw).text_encoder_placement == expected


def test_unknown_value_is_rejected():
    with pytest.raises(ValidationError, match="text_encoder_placement"):
        _config(text_encoder_placement="cpu")


def test_admin_form_describes_it_with_its_choices():
    spec = next(f for f in NativeBackendConfig.engine_fields() if f["name"] == "text_encoder_placement")
    assert spec["type"] == "string"
    assert spec["default"] == "auto"
    assert spec["options"] == ["auto", "gpu", "stream"]
    assert spec["label"] == "Text Encoder Placement"


def test_remote_driver_does_not_carry_it():
    assert "text_encoder_placement" not in NativeRemoteBackendConfig.model_fields


def _backend(cfg: NativeBackendConfig) -> NativeBackend:
    engine = Mock()
    engine.gpu_monitor = Mock()
    return NativeBackend(backend_config=cfg, generation_engine=engine)


def test_prepare_pipes_hands_mode_and_budget_to_the_engine():
    _backend(_config(text_encoder_placement="stream")).prepare_pipes([{"name": "g", "config": {}}])

    settings = te_settings.text_encoder_placement_settings()
    assert settings.mode == "stream"
    assert settings.vram_budget_gb == 10.0


def test_each_run_rebinds_the_owning_backends_mode():
    _backend(_config(text_encoder_placement="gpu")).prepare_pipes([{"name": "g", "config": {}}])
    assert te_settings.text_encoder_placement_mode() == "gpu"
    _backend(_config()).prepare_pipes([{"name": "g", "config": {}}])
    assert te_settings.text_encoder_placement_mode() == "auto"


def test_prepare_pipes_does_not_add_a_pipe_config_key():
    pipes = _backend(_config(text_encoder_placement="stream")).prepare_pipes([{"name": "g", "config": {}}])
    assert pipes[0]["config"] == {"device": "cuda:0", "dtype": _config().dtype, "vram_limit_gb": 10}


def _store(rows: list) -> BackendConfigStore:
    repo = MagicMock()
    repo.get_all.return_value = rows
    return BackendConfigStore(backend_repository=repo, registered_config_types={NATIVE_LOCAL_DRIVER: NativeBackendConfig})


def test_a_stored_config_from_before_the_option_loads_as_auto():
    row = Backend(id="native", name="Local", engine="native", driver=NATIVE_LOCAL_DRIVER, enabled=True,
                  is_default=True, config={"device": "cuda:0", "dtype": "bfloat16", "gpu_max_vram": 10})
    assert _store([row]).get_backend("native").text_encoder_placement == "auto"


def test_the_saved_value_round_trips():
    row = Backend(id="native", name="Local", engine="native", driver=NATIVE_LOCAL_DRIVER, enabled=True,
                  is_default=True, config={"text_encoder_placement": "stream"})
    assert _store([row]).get_backend("native").text_encoder_placement == "stream"
    saved = {k: v for k, v in _config(text_encoder_placement="gpu").model_dump().items()
             if k not in {"id", "name", "engine", "driver", "enabled"}}
    assert saved["text_encoder_placement"] == "gpu"


@pytest.fixture
def controller():
    store = Mock(spec=BackendConfigStore)
    store.get_default_backend_ids.return_value = {}
    real = _store([])
    store.validate_backend_config.side_effect = real.validate_backend_config
    registry = Mock(spec=BackendRegistry)
    registry.refresh_backends = AsyncMock()
    registry.backend_config_store = store
    return BackendController(Mock(spec=Settings), registry)


@pytest.mark.asyncio
async def test_get_backend_exposes_the_option(controller):
    controller.backend_config_store.get_backend.return_value = _config()

    response = await controller.get_backend("native")

    assert response.data["text_encoder_placement"] == "auto"


@pytest.mark.asyncio
async def test_update_backend_changes_the_option(controller):
    controller.backend_config_store.get_backend.return_value = _config()

    response = await controller.update_backend("native", {"text_encoder_placement": "stream"})

    assert response.success is True
    assert response.data["text_encoder_placement"] == "stream"
    saved = controller.backend_config_store.update_backend.call_args[0][1]
    assert saved.text_encoder_placement == "stream"
    assert saved.gpu_max_vram == 10


@pytest.mark.asyncio
async def test_update_backend_rejects_an_unknown_mode(controller):
    controller.backend_config_store.get_backend.return_value = _config()

    with pytest.raises(HTTPException) as exc_info:
        await controller.update_backend("native", {"text_encoder_placement": "cpu"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "backend_validation_failed"
    controller.backend_config_store.update_backend.assert_not_called()
