"""Wan UMT5-XXL text encoder tests.

Covers: per-layer relative-attention bias, the Wan-native -> ComfyUI key remap,
detection disambiguation (umt5 wan_native / comfy_t5 vs t5xxl by vocab), spiece
tokenizer goldens, zero_out_masked encode, real-header parity, gated real-file.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.detect.te_detect import detect_te_config  # noqa: E402
from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402
from src.platform.runtime.native.text_encoders.loader import (  # noqa: E402
    _SPECS,
    _build_config,
    _convert_wan_umt5,
)
from src.platform.runtime.native.text_encoders.t5xxl import T5XXLModel  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[5]
_BF16 = _REPO_ROOT / "models/text_encoders/umt5-xxl-enc-bf16.safetensors"
_FP8 = _REPO_ROOT / "models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"

TINY = {"hidden_size": 64, "num_layers": 3, "vocab_size": 256384, "d_kv": 16,
        "num_heads": 4, "d_ff": 128, "per_layer_bias": True}


def _build(cfg):
    m = T5XXLModel.from_config(cfg, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    m.post_load()
    return m


def test_per_layer_relative_attention_bias():
    m = _build(TINY)
    biases = [b.layer[0].SelfAttention.relative_attention_bias is not None for b in m.encoder.block]
    assert all(biases) and len(biases) == 3   # UMT5: every block owns its bias


def test_t5xxl_shares_block0_bias_only():
    # Regression guard: plain T5 (per_layer_bias False) keeps the single shared bias.
    m = _build({**TINY, "vocab_size": 32128, "per_layer_bias": False})
    biases = [b.layer[0].SelfAttention.relative_attention_bias is not None for b in m.encoder.block]
    assert biases == [True, False, False]


def test_forward_with_mask_finite():
    m = _build(TINY)
    ids = torch.randint(0, 256384, (2, 12))
    mask = torch.cat([torch.ones(2, 8), torch.zeros(2, 4)], dim=1).long()
    out = m(ids, attention_mask=mask)
    assert out.shape == (2, 12, 64)
    assert torch.isfinite(out).all()


def test_wan_native_key_conversion():
    sd = {
        "token_embedding.weight": torch.zeros(256384, 64),
        "norm.weight": torch.zeros(64),
        "blocks.0.attn.q.weight": torch.zeros(64, 64),
        "blocks.0.attn.o.weight": torch.zeros(64, 64),
        "blocks.0.pos_embedding.embedding.weight": torch.zeros(32, 4),
        "blocks.0.norm1.weight": torch.zeros(64),
        "blocks.0.norm2.weight": torch.zeros(64),
        "blocks.0.ffn.gate.0.weight": torch.zeros(128, 64),
        "blocks.0.ffn.fc1.weight": torch.zeros(128, 64),
        "blocks.0.ffn.fc2.weight": torch.zeros(64, 128),
    }
    out = _convert_wan_umt5(sd)
    assert "shared.weight" in out
    assert "encoder.final_layer_norm.weight" in out
    p = "encoder.block.0.layer."
    assert f"{p}0.SelfAttention.q.weight" in out
    assert f"{p}0.SelfAttention.relative_attention_bias.weight" in out
    assert f"{p}0.layer_norm.weight" in out
    assert f"{p}1.layer_norm.weight" in out
    # gate = Linear+GELU -> activated wi_0; fc1 = plain -> wi_1.
    assert f"{p}1.DenseReluDense.wi_0.weight" in out
    assert f"{p}1.DenseReluDense.wi_1.weight" in out
    assert f"{p}1.DenseReluDense.wo.weight" in out


def test_detection_umt5_vs_t5xxl_by_vocab():
    def t5_sd(vocab):
        return {"shared.weight": torch.zeros(vocab, 64),
                "encoder.block.0.layer.0.SelfAttention.q.weight": torch.zeros(64, 64)}

    assert detect_te_config(t5_sd(256384))["te_type"] == "umt5"      # multilingual vocab
    assert detect_te_config(t5_sd(32128))["te_type"] == "t5xxl"      # T5 vocab
    # Wan-native layout.
    wan = {"token_embedding.weight": torch.zeros(256384, 64), "blocks.0.attn.q.weight": torch.zeros(64, 64)}
    cfg = detect_te_config(wan)
    assert cfg["te_type"] == "umt5" and cfg["format"] == "wan_native"


def test_tokenizer_golden():
    pytest.importorskip("sentencepiece")
    from src.platform.runtime.native.text_encoders.tokenization import UMT5Tokenizer

    tok = UMT5Tokenizer()
    ids, mask = tok(["a cat"])
    assert ids.shape[1] == 512                    # min length 512
    assert ids[0, :3].tolist() == [289, 6283, 1]  # a, cat, eos (spiece add_eos)
    assert (ids[0, 3:] == 0).all()                # pad 0
    assert int(mask.sum()) == 3


def test_synthetic_load_and_zero_out_masked(tmp_path):
    pytest.importorskip("sentencepiece")
    from safetensors.torch import save_file

    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    m = _build(TINY)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    path = tmp_path / "umt5.safetensors"
    save_file(sd, str(path))

    enc = load_text_encoder(str(path))
    assert enc.role == "umt5_xxl"
    out = enc.encode(["a cat"])
    assert set(out) == {"context", "attention_mask"}
    assert out["context"].shape[2] == 64
    # zero_out_masked: everything past the real tokens is exactly zero.
    n = int(out["attention_mask"][0].sum())
    assert out["context"][0, n:].abs().max() == 0
    assert torch.isfinite(out["context"]).all()


@pytest.mark.requires_models
@pytest.mark.parametrize("path", [_BF16, _FP8])
def test_real_header_key_parity(path):
    if not path.is_file():
        pytest.skip(f"real checkpoint absent: {path.name}")
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    sd = {k: torch.zeros(tuple(header[k]["shape"]) or [1]) for k in header if k != "__metadata__"}
    cfg = detect_te_config(sd)
    assert cfg["te_type"] == "umt5"
    sd = {k: v for k, v in sd.items() if k != "spiece_model"}
    if cfg.get("format") == "wan_native":
        sd = _convert_wan_umt5(sd)
    module = T5XXLModel.from_config(_build_config(cfg, sd), ops)
    mkeys, ckeys = set(module.state_dict()), set(sd)
    spec = _SPECS["umt5"]
    bad_unexpected = [k for k in ckeys - mkeys if not spec.key_is_expected_unexpected(k)]
    bad_missing = [k for k in mkeys - ckeys if not spec.key_is_expected_missing(k)]
    assert not bad_unexpected, bad_unexpected[:10]
    assert not bad_missing, bad_missing[:10]


@pytest.mark.skipif(
    not (_BF16.is_file() and os.environ.get("POTIONUI_UMT5_REALFILE")),
    reason="real umt5 checkpoint absent or POTIONUI_UMT5_REALFILE not set (slow, ~40s)",
)
def test_real_bf16_loads_and_encodes():
    pytest.importorskip("sentencepiece")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    enc = load_text_encoder(str(_BF16), device="cpu")
    out = enc.encode(["a cat"])
    assert out["context"].shape == (1, 512, 4096)
    assert torch.isfinite(out["context"]).all()
    n = int(out["attention_mask"][0].sum())
    assert out["context"][0, n:].abs().max() == 0   # zero_out_masked
