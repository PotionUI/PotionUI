"""SeedVR2 wiring: real-checkpoint detection + fixed prompt-embedding loader.

Detection is exercised against the REAL safetensors headers (shape metadata
only, no tensor materialization) so the shape-sniffing stays honest to the
actual 3B checkpoint. The full CPU load-into-module path is covered by the
scratchpad script ``scripts_scratch/seedvr2_cpu_load.py`` (6.8GB DiT — too heavy
for the unit suite); this file keeps the fast, always-runnable checks.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.seedvr2 import load_seedvr2_prompt_embedding
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.detect.vae_detect import (
    detect_seedvr2_vae_config,
    detect_vae_config,
)

_DIT_PATH = Path("models/diffusion_models/seedvr2_ema_3b_fp16.safetensors")
_DIT_3B_FP8_PATH = Path("models/diffusion_models/seedvr2_ema_3b_fp8_e4m3fn.safetensors")
_DIT_7B_PATH = Path("models/diffusion_models/seedvr2_ema_7b_fp16.safetensors")
_VAE_PATH = Path("models/vae/ema_vae_fp16.safetensors")
# Filenames match what the preset recommendations download (URL basenames).
_POS_EMB_PATH = Path("models/text_encoders/pos_emb.pt")
_NEG_EMB_PATH = Path("models/text_encoders/neg_emb.pt")


def _safetensors_shapes(path: Path) -> dict[str, torch.Tensor]:
    """Read a safetensors header and return a name -> zero-strided meta tensor
    dict with the checkpoint's real shapes/dtypes (no data read)."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)
    dtypes = {
        "F16": torch.float16, "BF16": torch.bfloat16, "F32": torch.float32,
        "F8_E4M3": torch.float8_e4m3fn, "F8_E5M2": torch.float8_e5m2, "I64": torch.int64,
    }
    return {
        k: torch.zeros(v["shape"], dtype=dtypes.get(v["dtype"], torch.float32), device="meta")
        for k, v in header.items()
    }


@pytest.mark.requires_models
@pytest.mark.skipif(not _DIT_PATH.exists(), reason="seedvr2 3B DiT not present")
def test_detect_real_3b_dit_header():
    sd = _safetensors_shapes(_DIT_PATH)
    config = detect_unet_config(sd)
    assert config is not None and config["image_model"] == "seedvr2"
    assert config["vid_dim"] == 2560
    assert config["heads"] == 20
    assert config["head_dim"] == 128
    assert config["num_layers"] == 32
    assert config["mm_layers"] == 10
    assert config["vid_in_channels"] == 33
    assert config["vid_out_channels"] == 16
    assert config["txt_in_dim"] == 5120
    assert config["emb_dim"] == 15360
    assert config["mlp_hidden"] == 6912
    # And the detected config resolves to the SeedVR2 spec.
    spec = match_model_spec(config)
    assert spec.family == "seedvr2" and spec.variant == "seedvr2_3b"


@pytest.mark.requires_models
@pytest.mark.skipif(not _DIT_7B_PATH.exists(), reason="seedvr2 7B DiT not present")
def test_detect_real_7b_dit_header():
    sd = _safetensors_shapes(_DIT_7B_PATH)
    config = detect_unet_config(sd)
    assert config is not None and config["image_model"] == "seedvr2"
    assert config["seedvr2_variant"] == "7b"
    assert config["vid_dim"] == 3072
    assert config["heads"] == 24
    assert config["head_dim"] == 128
    assert config["num_layers"] == 36
    # 7B: every block is multimodal (no shared `.all` suffix blocks).
    assert config["mm_layers"] == 36
    assert config["vid_in_channels"] == 33
    assert config["vid_out_channels"] == 16
    assert config["txt_in_dim"] == 5120
    assert config["emb_dim"] == 18432
    assert config["mlp_hidden"] == 12288
    spec = match_model_spec(config)
    assert spec.family == "seedvr2" and spec.variant == "seedvr2_7b"
    assert spec.model_class.endswith(":SeedVR27B")


@pytest.mark.requires_models
@pytest.mark.skipif(not _DIT_3B_FP8_PATH.exists(), reason="seedvr2 3B fp8 DiT not present")
def test_detect_real_3b_fp8_dit_header():
    # The fp8 e4m3fn repack has identical keys/shapes — detection must be
    # dtype-blind and land on the same 3B spec.
    sd = _safetensors_shapes(_DIT_3B_FP8_PATH)
    config = detect_unet_config(sd)
    assert config is not None and config["seedvr2_variant"] == "3b"
    spec = match_model_spec(config)
    assert spec.variant == "seedvr2_3b"


@pytest.mark.requires_models
@pytest.mark.skipif(not _VAE_PATH.exists(), reason="seedvr2 VAE not present")
def test_detect_real_vae_header():
    sd = _safetensors_shapes(_VAE_PATH)
    config = detect_seedvr2_vae_config(sd)
    assert config is not None and config["vae_type"] == "seedvr2"
    assert config["latent_channels"] == 16
    assert config["in_channels"] == 3
    assert config["out_channels"] == 3
    # The 2D Flux-AE detector must decline the real 5D-conv checkpoint.
    assert detect_vae_config(sd) is None


@pytest.mark.requires_models
@pytest.mark.skipif(not _POS_EMB_PATH.exists(), reason="seedvr2 prompt embeddings not present")
def test_load_prompt_embeddings():
    pos = load_seedvr2_prompt_embedding(_POS_EMB_PATH)
    neg = load_seedvr2_prompt_embedding(_NEG_EMB_PATH)
    assert pos.ndim == 2 and neg.ndim == 2
    # width == txt_in_dim so it feeds the DiT's txt_in projection directly.
    assert pos.shape[1] == 5120
    assert neg.shape[1] == 5120


def test_prompt_embedding_rejects_non_tensor(tmp_path):
    bad = tmp_path / "bad.pt"
    torch.save({"not": "a tensor"}, bad)
    with pytest.raises(TypeError):
        load_seedvr2_prompt_embedding(bad)
