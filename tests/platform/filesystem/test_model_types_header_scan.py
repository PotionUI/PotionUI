from __future__ import annotations

import pytest

from src.platform.filesystem import model_types
from src.platform.filesystem.model_types import (
    HEADER_CLASSIFIED_TYPES,
    HEADER_EXTENSIONS,
    MODEL_TYPES,
    UNDEFINED_MODEL_TYPE,
    scan_headers_by_default,
)


def test_undefined_is_not_a_model_type():
    assert UNDEFINED_MODEL_TYPE == "undefined"
    assert UNDEFINED_MODEL_TYPE not in MODEL_TYPES
    assert UNDEFINED_MODEL_TYPE not in model_types.MODEL_TYPE_TO_DIRECTORY
    assert UNDEFINED_MODEL_TYPE not in model_types.DIRECTORY_TO_MODEL_TYPE.values()


def test_header_classified_types_are_real_types():
    assert HEADER_CLASSIFIED_TYPES == {"checkpoint", "diffusion_model", "unet"}
    assert HEADER_CLASSIFIED_TYPES <= set(MODEL_TYPES)


def test_header_extensions_exclude_pickles():
    assert HEADER_EXTENSIONS == {".safetensors", ".sft", ".gguf"}
    assert not HEADER_EXTENSIONS & {".ckpt", ".pt", ".pth", ".bin", ".task", ".tflite"}
    assert HEADER_EXTENSIONS <= model_types.SUPPORTED_MODEL_EXTENSIONS


@pytest.mark.parametrize(
    "name",
    ["Stable-diffusion", "stable-diffusion", "STABLE-DIFFUSION", "unet", "UNet", "models/Stable-diffusion", "a\\b\\unet", "unet/", "x/unet//"],
)
def test_default_on_folders(name):
    assert scan_headers_by_default(name) is True


@pytest.mark.parametrize(
    "name",
    ["checkpoints", "diffusion_models", "loras", "vae", "", "/", "unet/sub", "my-unet", "Stable-diffusion-xl", "unet\\sub"],
)
def test_default_off_folders(name):
    assert scan_headers_by_default(name) is False

