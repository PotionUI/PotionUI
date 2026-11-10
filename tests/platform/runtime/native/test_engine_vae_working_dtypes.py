from __future__ import annotations

import pytest
import torch

import src.platform.runtime.native.engine as engine_mod
from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.ops.dtype import honour_working_dtypes, pick_dtypes
from src.platform.runtime.native.vae.ae_2d import AutoEncoder2D
from src.platform.runtime.native.vae.loader import native_vae_working_dtypes

from ._header_listings import load_header

DTYPES = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}
FLUX_VAE = {
    "vae_type": "flux_ae", "in_channels": 3, "out_channels": 3, "latent_channels": 16,
    "has_quant_conv": False, "has_batchnorm": False,
}


def _header_state(listing: str) -> dict[str, torch.Tensor]:
    return {name: torch.empty(entry["shape"], dtype=DTYPES.get(entry["dtype"], torch.float16), device="meta")
            for name, entry in load_header(listing).items()}


def _flux_fp16_state() -> dict[str, torch.Tensor]:
    with torch.device("meta"):
        module = AutoEncoder2D.from_config(FLUX_VAE, disable_weight_init)
    return {key: value.to(torch.float16) for key, value in module.state_dict().items()}


def _loaded_compute_dtype(monkeypatch, sd: dict[str, torch.Tensor]) -> torch.dtype:
    monkeypatch.setattr(engine_mod, "load_torch_file_prefixed", lambda path, prefix, device: (sd, {}))
    monkeypatch.setattr(engine_mod, "build_native_vae", lambda path, ops, sd, metadata: torch.nn.Identity())
    return NativeEngineLoader(device="cpu").load("vae.safetensors", "vae").compute_dtype


def test_the_image_vae_runs_at_its_stored_fp16_where_the_engine_would_pick_bf16(monkeypatch):
    assert pick_dtypes(torch.float16, "cpu")[1] is torch.bfloat16
    assert _loaded_compute_dtype(monkeypatch, _header_state("minimax_h3_image/vae_fp16")) is torch.float16


@pytest.mark.parametrize("state", [lambda: _header_state("minimax_h3/video_vae"), _flux_fp16_state],
                         ids=["minimax_h3_video", "flux"])
def test_vaes_that_declare_no_working_precision_keep_the_engine_choice(monkeypatch, state):
    sd = state()
    assert native_vae_working_dtypes(sd, {}) is None
    assert _loaded_compute_dtype(monkeypatch, sd) is pick_dtypes(torch.float16, "cpu")[1]


def test_the_image_vae_declares_the_reference_precisions():
    assert native_vae_working_dtypes(_header_state("minimax_h3_image/vae_fp16"), {}) == (torch.float16, torch.float32)


def test_a_declared_precision_prefers_the_engine_choice_then_storage_then_the_first_declared():
    working = (torch.float16, torch.float32)
    assert honour_working_dtypes(torch.float16, torch.bfloat16, working) is torch.float16
    assert honour_working_dtypes(torch.bfloat16, torch.float32, working) is torch.float32
    assert honour_working_dtypes(torch.bfloat16, torch.bfloat16, working) is torch.float16
    assert honour_working_dtypes(torch.bfloat16, torch.float16, None) is torch.bfloat16
