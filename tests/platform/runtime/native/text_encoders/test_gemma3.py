"""Gemma3-12B text encoder (LTX-2) tests.

Covers: the gemma3 4-norm block + rms_norm(weight+1), dual global/local RoPE,
normalize_in, sliding-window mask, layer="all" 49-state stack + LTX normalization,
detection disambiguation vs qwen3, spiece golden, real-header key parity.
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
from src.platform.runtime.native.text_encoders.gemma3 import Gemma3Model, _GemmaRMSNorm  # noqa: E402
from src.platform.runtime.native.text_encoders.loader import _SPECS, _build_config  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[5]
_REAL = _REPO_ROOT / "models/clip/gemma_3_12B_it.safetensors"

# head_dim 256 so the loader's GQA derivation (shape // 256) works.
TINY = {"hidden_size": 64, "num_layers": 8, "vocab_size": 262208, "intermediate_size": 128,
        "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 256}


def _build(cfg, seed=42):
    g = torch.Generator().manual_seed(seed)
    m = Gemma3Model.from_config(cfg, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02, generator=g)
    m.post_load()
    return m


def test_block_has_four_norms():
    m = _build(TINY)
    b = m.model.layers[0]
    for n in ("input_layernorm", "post_attention_layernorm", "pre_feedforward_layernorm", "post_feedforward_layernorm"):
        assert hasattr(b, n)


def test_rms_norm_adds_one():
    # gemma3 rms_norm scales by (weight + 1): zero weight -> plain RMS (unit scale).
    norm = _GemmaRMSNorm(8, 1e-6)
    torch.nn.init.zeros_(norm.weight)
    x = torch.randn(2, 4, 8)
    out = norm(x)
    expected = torch.nn.functional.rms_norm(x, (8,), weight=torch.ones(8), eps=1e-6)
    assert torch.allclose(out, expected, atol=1e-5)


def test_dual_rope_recomputed():
    m = _build(TINY)
    g, l = m.model.inv_freq_global, m.model.inv_freq_local
    assert g.shape == l.shape == (TINY["head_dim"] // 2,)
    assert torch.isfinite(g).all() and torch.isfinite(l).all()
    # global uses larger theta + /8 scale -> strictly smaller than local at index 0.
    assert float(g[0]) < float(l[0])


def test_forward_stacks_all_states():
    m = _build(TINY)
    ids = torch.randint(0, 262208, (2, 10))
    mask = torch.ones(2, 10, dtype=torch.long)
    mask[:, 7:] = 0
    out = m(ids, attention_mask=mask)
    assert out.shape == (2, TINY["num_layers"] + 1, 10, 64)   # 48+1 states
    assert torch.isfinite(out).all()


def test_detection_gemma3_vs_qwen3():
    def sd(pre_ff: bool):
        d = {"model.embed_tokens.weight": torch.zeros(262208, 64),
             "model.layers.0.self_attn.q_norm.weight": torch.zeros(256)}
        if pre_ff:
            d["model.layers.0.pre_feedforward_layernorm.weight"] = torch.zeros(64)
        return d

    assert detect_te_config(sd(pre_ff=True))["te_type"] == "gemma3"
    # Without the 4th norm + with qwen vocab it is qwen3, not gemma3.
    qwen = {"model.embed_tokens.weight": torch.zeros(151936, 64),
            "model.layers.0.self_attn.q_norm.weight": torch.zeros(128)}
    assert detect_te_config(qwen)["te_type"] == "qwen3"


def test_tokenizer_golden():
    pytest.importorskip("sentencepiece")
    from src.platform.runtime.native.text_encoders.tokenization import Gemma3Tokenizer

    tok = Gemma3Tokenizer()
    ids, mask = tok(["a cat"])
    assert ids[0].tolist() == [2, 236746, 5866]   # bos(2) + a + cat, no eos
    assert int(mask.sum()) == 3


def test_synthetic_load_and_encode(tmp_path):
    pytest.importorskip("sentencepiece")
    from safetensors.torch import save_file

    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    m = _build({**TINY, "num_layers": 4}, seed=12345)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    # vision tower + mm projector + spiece must be stripped, not choke the load.
    sd["vision_model.embeddings.patch_embedding.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["multi_modal_projector.mm_soft_emb_norm.weight"] = torch.zeros(4, dtype=torch.bfloat16)
    path = tmp_path / "gemma3.safetensors"
    save_file(sd, str(path))

    enc = load_text_encoder(str(path))
    assert enc.role == "gemma3_12b"
    out = enc.encode(["a cat"])
    assert set(out) == {"context", "attention_mask"}
    # RAW channel-major stack: hidden(64) * (num_layers+1) = 64 * 5 = 320.
    # Normalization moved to LTXAVModel.apply_text_conditioning (see gemma3.py:292);
    # encode() now returns the un-normalised stack.
    context = out["context"]
    assert context.shape == (1, 3, 320)   # [B=1, S=3 tokens ("a cat" + BOS), H*(L+1)]
    assert context.ndim == 3
    assert context.shape[-1] == 64 * 5
    assert torch.isfinite(context).all()
    # Mask must be present and match the token count.
    assert "attention_mask" in out
    mask = out["attention_mask"]
    assert mask.shape == (1, 3)
    assert int(mask.sum()) == 3


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL.is_file(), reason="real gemma3 checkpoint absent")
def test_real_header_key_parity():
    with open(_REAL, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    real_keys = {k for k in header if k != "__metadata__"}
    cfg = {"hidden_size": 64, "num_layers": 48, "vocab_size": 262208, "intermediate_size": 128,
           "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 256}
    module = Gemma3Model.from_config(cfg, ops)
    mkeys = set(module.state_dict())
    spec = _SPECS["gemma3"]
    bad_unexpected = [k for k in real_keys - mkeys if not spec.key_is_expected_unexpected(k)]
    bad_missing = [k for k in mkeys - real_keys if not spec.key_is_expected_missing(k)]
    assert not bad_unexpected, bad_unexpected[:10]
    assert not bad_missing, bad_missing[:10]


@pytest.mark.skipif(
    not (_REAL.is_file() and os.environ.get("POTIONUI_GEMMA3_REALFILE")),
    reason="real gemma3 checkpoint absent or POTIONUI_GEMMA3_REALFILE not set (24GB, slow)",
)
def test_real_file_loads_and_encodes():
    pytest.importorskip("sentencepiece")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    enc = load_text_encoder(str(_REAL), device="cpu")
    out = enc.encode(["a cat"])
    assert out["context"].shape[-1] == 3840 * 49   # 188160
    assert torch.isfinite(out["context"]).all()
