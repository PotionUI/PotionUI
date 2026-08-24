"""Tests for the Anima text encoder (Qwen3-0.6B + T5 token ids).

Anima reuses the generic Qwen3 arch but has its own extraction contract: the
LAST decoder layer's hidden state (no final norm) plus a T5 tokenization of the
same prompt (the DiT's in-model LLMAdapter target ids). Detection classifies the
hidden==1024 checkpoint as ``qwen3_06b`` (the exact real 0.6B width) and the
loader routes that variant to ``AnimaTextEncoder``. Covers detection, the loader
routing, the four-key conditioning dict, last-layer extraction, and A1111
emphasis riding on the T5 side.
"""

from __future__ import annotations

import os
import tempfile

import torch
from safetensors.torch import save_file

from src.platform.runtime.native.detect.te_detect import detect_te_config
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.text_encoders.anima import AnimaTextEncoder
from src.platform.runtime.native.text_encoders.loader import _build_config, load_text_encoder
from src.platform.runtime.native.text_encoders.qwen3 import Qwen3Config, Qwen3Model


def _tiny_06b_sd(vocab: int = 256, layers: int = 4):
    """A loadable Qwen3-0.6B-shaped checkpoint: hidden EXACTLY 1024 (the detection
    discriminator), 16 heads / head_dim 128 (inner 2048 != hidden), small vocab so
    the embed stays light."""
    cfg = Qwen3Config(hidden_size=1024, intermediate_size=64, num_hidden_layers=layers,
                      vocab_size=vocab, num_attention_heads=16, num_key_value_heads=8, head_dim=128)
    m = Qwen3Model(cfg, disable_weight_init)
    # Seeded: unseeded draws occasionally produce activations whose variance
    # collapses through the final RMS norm into NaN (observed ~1-in-3 flake).
    gen = torch.Generator().manual_seed(0)
    return {k: torch.randn(tuple(v.shape), generator=gen).mul_(0.02).to(v.dtype)
            for k, v in m.state_dict().items()}, cfg


def test_detect_qwen3_06b_variant():
    sd, _ = _tiny_06b_sd(layers=28)
    cfg = detect_te_config(sd)
    assert cfg["te_type"] == "qwen3"
    assert cfg["variant"] == "qwen3_06b"      # hidden == 1024
    assert cfg["num_layers"] == 28


def test_small_qwen3_not_claimed_as_06b():
    # A hidden-64 tiny Qwen3 (the Klein test fixture width) must stay generic.
    cfg = Qwen3Config(hidden_size=64, intermediate_size=64, num_hidden_layers=2,
                      vocab_size=256, num_attention_heads=1, num_key_value_heads=1, head_dim=64)
    sd = {k: torch.zeros(tuple(v.shape)).to(v.dtype) for k, v in Qwen3Model(cfg, disable_weight_init).state_dict().items()}
    assert detect_te_config(sd)["variant"] != "qwen3_06b"


def test_build_config_recovers_head_geometry():
    sd, _ = _tiny_06b_sd()
    cfg = _build_config(detect_te_config(sd), sd)
    assert cfg["head_dim"] == 128
    assert cfg["num_attention_heads"] == 16   # inner 2048 / head_dim 128
    assert cfg["num_key_value_heads"] == 8


def test_loader_routes_06b_to_anima_encoder():
    sd, _ = _tiny_06b_sd(layers=28)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "anima_qwen3_06b.safetensors")
        save_file(sd, p)
        enc = load_text_encoder(p, device="cpu")
    assert isinstance(enc, AnimaTextEncoder)
    assert enc.role == "qwen3_06b"
    assert enc._last_layer == 27              # extracts the LAST (28th) layer's output


# -- encode contract (fake tokenizers keep it light + deterministic) -------

class _FakeQwenTok:
    """Emits small ids so a tiny-vocab module can encode; records the stripped text."""

    def __init__(self):
        self.seen = None

    def __call__(self, texts, device="cpu"):
        self.seen = list(texts)
        ids = torch.arange(1, 6).unsqueeze(0).repeat(len(texts), 1)  # [B,5]
        return ids, torch.ones_like(ids)


class _FakeT5Tok:
    """Returns fixed ids and A1111-style weights (>1.0 iff the prompt has emphasis)."""

    def __call__(self, text, device="cpu"):
        ids = torch.tensor([[3, 4, 5, 1]])
        w = torch.tensor([[1.0, 1.4, 1.0, 1.0]]) if "(" in text else torch.ones(1, 4)
        return ids, torch.ones_like(ids), w


def _encoder_with_fakes():
    _sd, cfg = _tiny_06b_sd(vocab=64, layers=6)
    m = Qwen3Model(cfg, disable_weight_init)
    gen = torch.Generator().manual_seed(7)
    with torch.no_grad():
        for p in m.parameters():
            p.normal_(0.0, 0.02, generator=gen)
    # inv_freq is torch.empty garbage until post_load (the loader always calls
    # it; a hand-built module must too) — without this the encode NaNs whenever
    # the uninitialized memory is hostile, which showed up as a ~1-in-3 flake.
    m.post_load()
    return AnimaTextEncoder(m, _FakeQwenTok(), _FakeT5Tok(), device="cpu"), m


def test_encode_returns_four_key_conditioning_from_last_layer():
    enc, m = _encoder_with_fakes()
    out = enc.encode(["a red cat"])
    assert set(out) == {"context", "attention_mask", "t5xxl_ids", "t5xxl_weights"}
    assert out["context"].shape == (1, 5, 1024)     # [B, S_qwen, hidden]
    assert out["t5xxl_ids"].dtype == torch.long and out["t5xxl_ids"].shape == (1, 4)
    assert out["t5xxl_weights"].shape == out["t5xxl_ids"].shape
    assert torch.isfinite(out["context"]).all()
    # Last-layer extraction THROUGH the final RMS norm (ComfyUI layer="last").
    ref = m(torch.arange(1, 6).unsqueeze(0), attention_mask=torch.ones(1, 5, dtype=torch.long),
            layers_to_extract=[enc._last_layer], capture="output")[:, 0]
    ref = m.model.norm(ref)
    assert torch.allclose(out["context"], ref)


def test_a1111_weight_rides_on_t5_side():
    enc, _ = _encoder_with_fakes()
    plain = enc.encode_weighted("a plain cat")
    assert torch.allclose(plain["t5xxl_weights"], torch.ones_like(plain["t5xxl_weights"]))
    weighted = enc.encode_weighted("a (glowing:1.4) cat")
    assert float(weighted["t5xxl_weights"].max()) > 1.0
