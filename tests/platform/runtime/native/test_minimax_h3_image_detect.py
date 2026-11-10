from __future__ import annotations

import json

import pytest
import torch

from src.platform.runtime.model_headers.signatures import family_from_keys
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

from ._header_listings import load_header

GRID = [1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0]
METADATA = {"config": json.dumps({"transformer": {"pdd_steps": 6, "pdd_sigmas": GRID}})}
EXPECTED = {
    "image_model": "minimax_h3_image", "hidden_size": 5376, "num_layers": 50, "num_refiner_layers": 2,
    "num_attention_heads": 56, "attention_head_dim": 128, "ffn_dim": 14336, "in_channels": 24,
    "audio_in_channels": None, "patch_size": (1, 2, 2), "text_dim": 5120, "rope_freq_dim": 16,
    "pdd_steps": 6, "pdd_sigmas": tuple(GRID), "pruned": True, "adaln_curve_grid": 1025, "time_embed_dim": 8,
}
DTYPES = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16, "I8": torch.int8,
          "U8": torch.uint8, "F8_E4M3": torch.float8_e4m3fn}


def _state(listing: str) -> dict[str, torch.Tensor]:
    return {name: torch.empty(entry["shape"], dtype=DTYPES[entry["dtype"]], device="meta")
            for name, entry in load_header(listing).items()}


def _shape_of(state):
    return lambda key: tuple(state[key].shape) if key in state else None


@pytest.mark.parametrize("listing", ["pruned_bf16", "pruned_int8_convrot", "pruned_w4a8"])
def test_every_released_file_detects_as_minimax_h3_image(listing):
    state = _state(f"minimax_h3_image/{listing}")
    config = detect_unet_config(state, METADATA)
    assert config == EXPECTED
    spec = match_model_spec(config)
    assert (spec.family, spec.variant) == ("minimax_h3_image", "h3_image")
    assert spec.sampling_settings["guidance"] == "flow_map"
    assert spec.sampling_settings["schedule"] == "h3_image_pdd"
    assert family_from_keys(state.keys(), _shape_of(state)).family == "minimax_h3_image"


def test_an_audio_stream_makes_it_the_video_model():
    state = _state("minimax_h3_image/pruned_bf16")
    state["audio_patch_proj.weight"] = torch.empty(5376, 32, device="meta")
    state["final_layer.audio_out.weight"] = torch.empty(32, 5376, device="meta")
    state["final_layer.video_out.weight"] = torch.empty(96, 5376, device="meta")
    assert detect_unet_config(state, METADATA)["image_model"] == "minimax_h3"
    assert family_from_keys(state.keys(), _shape_of(state)).family == "minimax_h3"


def test_the_video_checkpoint_still_detects_as_video():
    state = _state("minimax_h3/pruned_fp8")
    assert detect_unet_config(state)["image_model"] == "minimax_h3"
    assert family_from_keys(state.keys(), _shape_of(state)).family == "minimax_h3"


@pytest.mark.parametrize("metadata", [
    None,
    {"config": json.dumps({"transformer": {"pdd_steps": 5, "pdd_sigmas": GRID[1:]}})},
    {"config": json.dumps({"transformer": {"pdd_steps": 5, "pdd_sigmas": GRID}})},
    {"config": json.dumps({"transformer": {"pdd_steps": 6, "pdd_sigmas": GRID[::-1]}})},
    {"config": "not json"},
])
def test_a_head_bank_without_a_matching_grid_is_refused(metadata):
    with pytest.raises(NativeEngineUnsupportedError, match="pdd_sigmas"):
        detect_unet_config(_state("minimax_h3_image/pruned_bf16"), metadata)
