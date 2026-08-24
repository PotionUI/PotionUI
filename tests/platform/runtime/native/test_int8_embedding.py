"""int8_tensorwise ``Embedding`` dequantisation tests.

MiniMax-H3's nvfp4_awq text-encoder repack quantises ``model.embed_tokens``
tensorwise-int8 (I8 weight ``[151936, 5120]`` + per-row F32 ``weight_scale``
``[151936, 1]`` + a ``comfy_quant`` descriptor — verified against
``ai/minimax_h3/te_nvfp4_awq_header.json``). Before this change
``disable_weight_init.Embedding``/``manual_cast.Embedding`` had no dequant
support at all: an int8 weight would be cast straight to bf16 and used as the
embedding table, i.e. the raw int8 codes read out as if they were already the
float values.
"""

from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import (  # noqa: E402
    _build_convrot_hadamard,
    disable_weight_init,
    manual_cast,
)


def _blob(conf: dict) -> torch.Tensor:
    return torch.tensor(list(json.dumps(conf).encode("utf-8")), dtype=torch.uint8)


def _quantize_int8_tensorwise(w: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-row symmetric int8 quantiser (the plain int8_tensorwise scheme)."""
    row_absmax = w.abs().amax(dim=-1, keepdim=True).clamp(min=1e-12)
    scale = row_absmax / 127.0
    q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
    return q, scale


def test_embedding_consumes_int8_sidecars_no_missing_or_unexpected():
    torch.manual_seed(0)
    num_emb, dim = 20, 16
    w = torch.randn(num_emb, dim) * 0.05
    q, scale = _quantize_int8_tensorwise(w)
    sd = {
        "weight": q,
        "weight_scale": scale,
        "comfy_quant": _blob({"format": "int8_tensorwise"}),
    }
    emb = manual_cast.Embedding(num_emb, dim)
    missing, unexpected = [], []
    emb._load_from_state_dict(dict(sd), "", {}, True, missing, unexpected, [])
    assert missing == [] and unexpected == []
    assert emb.weight is None
    assert emb._int8_weight is not None


def test_embedding_lookup_matches_manual_row_dequant():
    torch.manual_seed(1)
    num_emb, dim = 20, 16
    w = torch.randn(num_emb, dim) * 0.05
    q, scale = _quantize_int8_tensorwise(w)
    sd = {"weight": q, "weight_scale": scale, "comfy_quant": _blob({"format": "int8_tensorwise"})}

    emb = manual_cast.Embedding(num_emb, dim)
    emb._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    idx = torch.tensor([[1, 5, 19], [0, 2, 7]])
    out = emb(idx)
    expected = (q.to(torch.float32) * scale)[idx].to(out.dtype)
    assert torch.allclose(out, expected)


def test_embedding_lookup_dequant_is_load_bearing():
    """Bite-check: reading the raw int8 codes as if they were already the
    float embedding values (the pre-fix behavior) must give a DIFFERENT
    result than the real per-row dequant."""
    torch.manual_seed(2)
    num_emb, dim = 20, 16
    w = torch.randn(num_emb, dim) * 0.05
    q, scale = _quantize_int8_tensorwise(w)
    sd = {"weight": q, "weight_scale": scale, "comfy_quant": _blob({"format": "int8_tensorwise"})}

    emb = manual_cast.Embedding(num_emb, dim)
    emb._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    idx = torch.tensor([1, 5, 19])
    out = emb(idx)
    undequantized = q.to(out.dtype)[idx]
    assert not torch.allclose(out, undequantized)


def test_embedding_per_tensor_scalar_scale_broadcasts():
    """A 0-dim (true tensorwise) scale — the other on-the-wire shape the
    format allows — must also dequant correctly, not just the per-row [N,1]
    shape MiniMax-H3's checkpoint happens to use."""
    torch.manual_seed(3)
    num_emb, dim = 10, 8
    w = torch.randn(num_emb, dim) * 0.05
    absmax = w.abs().amax().clamp(min=1e-12)
    scale = (absmax / 127.0).reshape(())
    q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
    sd = {"weight": q, "weight_scale": scale, "comfy_quant": _blob({"format": "int8_tensorwise"})}

    emb = manual_cast.Embedding(num_emb, dim)
    emb._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    idx = torch.tensor([0, 3, 9])
    out = emb(idx)
    expected = (q.to(torch.float32) * scale)[idx].to(out.dtype)
    assert torch.allclose(out, expected)


def test_embedding_convrot_rotated_undoes_the_rotation():
    """ConvRot-rotated int8 embeddings (the same rotation Linear already
    supports) must un-rotate per selected row — gather-then-unrotate is
    mathematically identical to unrotate-then-gather since the rotation acts
    only within a row, never across rows."""
    torch.manual_seed(4)
    num_emb, dim, group = 12, 16, 4
    w = torch.randn(num_emb, dim) * 0.05
    hadamard = _build_convrot_hadamard(group, device="cpu", dtype=torch.float32)
    # Offline rotation matches _convrot_unrotate_weight's own grouping/matmul
    # (self-inverse, so rotating once here + un-rotating in the module round-trips).
    n_groups = dim // group
    rotated = torch.matmul(w.reshape(num_emb, n_groups, group), hadamard).reshape(num_emb, dim)
    q, scale = _quantize_int8_tensorwise(rotated)
    sd = {
        "weight": q,
        "weight_scale": scale,
        "comfy_quant": _blob({"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": group}),
    }

    emb = manual_cast.Embedding(num_emb, dim)
    emb._load_from_state_dict(dict(sd), "", {}, True, [], [], [])
    assert emb._int8_convrot_hadamard is not None

    idx = torch.tensor([0, 4, 11])
    out = emb(idx, out_dtype=torch.float32)  # avoid bf16 rounding noise in the comparison
    # Un-rotating the manually dequantised rotated rows should recover the
    # ORIGINAL (unrotated) embedding rows within int8-grid error.
    manual_deq = (q.to(torch.float32) * scale)[idx]
    manual_unrot = torch.matmul(manual_deq.reshape(-1, n_groups, group), hadamard).reshape(-1, dim)
    assert torch.allclose(out, manual_unrot, atol=1e-5)
    rel = (out - w[idx]).abs().mean() / w[idx].abs().mean()
    assert rel < 0.2  # int8 grid error, not corruption


def test_unsupported_quant_format_raises():
    num_emb, dim = 10, 8
    sd = {
        "weight": torch.zeros(num_emb, dim, dtype=torch.int8),
        "weight_scale": torch.ones(num_emb, 1),
        "comfy_quant": _blob({"format": "nvfp4"}),
    }
    emb = manual_cast.Embedding(num_emb, dim)
    with pytest.raises(ValueError, match="nvfp4"):
        emb._load_from_state_dict(dict(sd), "", {}, True, [], [], [])


def test_plain_unquantized_embedding_unaffected():
    """No comfy_quant blob at all -> ordinary float embedding load, unchanged."""
    num_emb, dim = 10, 8
    w = torch.randn(num_emb, dim)
    emb = disable_weight_init.Embedding(num_emb, dim)
    emb._load_from_state_dict({"weight": w}, "", {}, True, [], [], [])
    assert emb._int8_weight is None
    idx = torch.tensor([1, 2, 3])
    assert torch.equal(emb(idx), w[idx])
