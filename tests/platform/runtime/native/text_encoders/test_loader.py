"""Full load path: detect -> build -> integrity gate -> encode, plus guards.

Uses tiny synthetic checkpoints written to disk so the whole loader runs on CPU
in milliseconds. The tokenizers are real (bundled, offline).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
from safetensors.torch import save_file  # noqa: E402

from src.platform.runtime.native.errors import (  # noqa: E402
    NativeEngineLoadIntegrityError,
    NativeEngineUnsupportedError,
)
from src.platform.runtime.native.text_encoders.loader import FluxTextEncoder, load_text_encoder  # noqa: E402

from ._fixtures import tiny_clip_state_dict, tiny_qwen3_state_dict, tiny_t5_state_dict  # noqa: E402


def _save(sd, tmp_path, name):
    p = tmp_path / name
    save_file(sd, str(p))
    return str(p)


def test_load_qwen3_and_encode(tmp_path):
    path = _save(tiny_qwen3_state_dict(num_layers=28, hidden=64), tmp_path, "qwen3.safetensors")
    enc = load_text_encoder(path)
    out = enc.encode(["a cat", "a dog on a beach"])
    assert set(out) == {"context", "attention_mask"}
    assert out["context"].shape[0] == 2
    assert out["context"].shape[1] >= 512            # min-length padding
    assert out["context"].shape[2] == 3 * 64         # stacked 3 hidden states
    assert torch.isfinite(out["context"]).all()
    # No pooled for Klein/Flux2.
    assert "pooled" not in out


def test_load_t5_and_encode(tmp_path):
    path = _save(tiny_t5_state_dict(num_layers=3), tmp_path, "t5.safetensors")
    enc = load_text_encoder(path)
    out = enc.encode(["a cat"])
    assert set(out) == {"context"}
    assert out["context"].shape == (1, 256, 64)      # min length 256, d_model 64
    assert torch.isfinite(out["context"]).all()


def test_load_clip_and_encode(tmp_path):
    path = _save(tiny_clip_state_dict(num_layers=3), tmp_path, "clip.safetensors")
    enc = load_text_encoder(path)
    out = enc.encode(["a cat"])
    assert set(out) == {"pooled"}
    assert out["pooled"].shape == (1, 24)
    assert torch.isfinite(out["pooled"]).all()


def test_flux_composite_two_paths(tmp_path):
    t5 = _save(tiny_t5_state_dict(), tmp_path, "t5.safetensors")
    clip = _save(tiny_clip_state_dict(), tmp_path, "clip.safetensors")
    enc = load_text_encoder([clip, t5])  # order-independent
    assert isinstance(enc, FluxTextEncoder)
    out = enc.encode(["a cat"])
    assert set(out) == {"context", "pooled"}
    assert out["context"].shape == (1, 256, 64)
    assert out["pooled"].shape == (1, 24)


def test_load_mixed_fp8_and_nvfp4_qwen(tmp_path):
    """A qwen checkpoint with one nvfp4 linear + one fp8-scaled linear loads + encodes."""
    from tests.platform.runtime.native._nvfp4_ref import default_tensor_scale, quantize_nvfp4

    sd = tiny_qwen3_state_dict(num_layers=28, hidden=64, dtype=torch.bfloat16)

    # Convert layer 0 q_proj (out=4096, in=64 -> clean to_blocked dims) to nvfp4.
    k = "model.layers.0.self_attn.q_proj.weight"
    w = sd[k].to(torch.float32)
    pts = default_tensor_scale(w)
    packed, block_sw, _, _ = quantize_nvfp4(w, pts)
    sd[k] = packed
    sd["model.layers.0.self_attn.q_proj.weight_scale"] = block_sw
    sd["model.layers.0.self_attn.q_proj.weight_scale_2"] = pts.clone()
    sd["model.layers.0.self_attn.q_proj.comfy_quant"] = torch.zeros(3, dtype=torch.uint8)

    # Convert layer 1 k_proj to per-tensor fp8-scaled.
    k2 = "model.layers.1.self_attn.k_proj.weight"
    sd[k2] = sd[k2].to(torch.float8_e4m3fn)
    sd["model.layers.1.self_attn.k_proj.weight_scale"] = torch.tensor(0.5)

    path = _save(sd, tmp_path, "mixed.safetensors")
    enc = load_text_encoder(path)
    out = enc.encode(["a cat"])
    assert out["context"].shape[1] >= 512
    assert out["context"].shape[2] == 3 * 64
    assert torch.isfinite(out["context"]).all()


@pytest.mark.skipif(
    not (Path("models/clip/qwen_3_8b_fp8mixed.safetensors").is_file() and os.environ.get("POTIONUI_NVFP4_REALFILE")),
    reason="real 8B checkpoint absent or POTIONUI_NVFP4_REALFILE not set (slow, ~50s / 9GB)",
)
def test_real_8b_mixed_loads_and_encodes():
    enc = load_text_encoder("models/clip/qwen_3_8b_fp8mixed.safetensors", device="cpu")
    out = enc.encode(["a cat"])
    assert out["context"].shape == (1, 512, 12288)
    assert torch.isfinite(out["context"]).all()


def test_integrity_error_on_unlisted_key(tmp_path):
    sd = tiny_qwen3_state_dict(num_layers=28, hidden=64)
    sd["model.garbage.weight"] = torch.zeros(4)  # not a module key, not allowlisted
    path = _save(sd, tmp_path, "bad.safetensors")
    with pytest.raises(NativeEngineLoadIntegrityError):
        load_text_encoder(path)


def test_unrecognised_checkpoint_rejected(tmp_path):
    path = _save({"not.a.text.encoder": torch.zeros(4)}, tmp_path, "unknown.safetensors")
    with pytest.raises(NativeEngineUnsupportedError):
        load_text_encoder(path)


def test_two_paths_must_be_t5_and_clip(tmp_path):
    a = _save(tiny_clip_state_dict(), tmp_path, "clip1.safetensors")
    b = _save(tiny_clip_state_dict(), tmp_path, "clip2.safetensors")
    with pytest.raises(NativeEngineUnsupportedError):
        load_text_encoder([a, b])
