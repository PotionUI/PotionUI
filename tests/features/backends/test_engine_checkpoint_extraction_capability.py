from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from src.features.backends.backend_config import (
    NATIVE_LOCAL_DRIVER,
    NATIVE_REMOTE_DRIVER,
    BaseBackendConfig,
    NativeBackendConfig,
    NativeRemoteBackendConfig,
)
from src.features.backends.backend_registry import BackendRegistry


class OtherEngineConfig(BaseBackendConfig):
    engine: str = Field(default="otherengine")


class ExtractingEngineConfig(BaseBackendConfig):
    engine: str = Field(default="extractor")
    extracts_diffusion_model_from_checkpoint: ClassVar[bool] = True


def registry_with(config_types):
    registry = object.__new__(BackendRegistry)
    registry._registered_config_types = config_types
    return registry


def test_both_native_drivers_declare_the_capability():
    assert NativeBackendConfig.extracts_diffusion_model_from_checkpoint is True
    assert NativeRemoteBackendConfig.extracts_diffusion_model_from_checkpoint is True


def test_an_engine_does_not_extract_by_default():
    assert BaseBackendConfig.extracts_diffusion_model_from_checkpoint is False
    assert OtherEngineConfig.extracts_diffusion_model_from_checkpoint is False


def test_the_registry_answers_per_engine():
    registry = registry_with(
        {
            NATIVE_LOCAL_DRIVER: NativeBackendConfig,
            NATIVE_REMOTE_DRIVER: NativeRemoteBackendConfig,
            "otherengine": OtherEngineConfig,
        }
    )

    assert registry.engine_extracts_diffusion_model_from_checkpoint("native") is True
    assert registry.engine_extracts_diffusion_model_from_checkpoint("otherengine") is False
    assert registry.engine_extracts_diffusion_model_from_checkpoint("missing") is False


def test_a_plugin_engine_can_opt_in_through_its_config_class():
    registry = registry_with({"extractor": ExtractingEngineConfig, "otherengine": OtherEngineConfig})

    assert registry.engine_extracts_diffusion_model_from_checkpoint("extractor") is True
    assert registry.engine_extracts_diffusion_model_from_checkpoint("otherengine") is False
