"""Krea-2's ``_sdpa_gqa`` hands grouped K/V to the shared seam.

It used to expand the kv heads to the query head count itself before calling
the attention dispatch. The oracle here is ``_expanded_sdpa_gqa`` — that
pre-change body — compared against the live one both directly and through
``Attention.forward``, masked and unmasked.
"""

from __future__ import annotations

import pytest
import torch
from einops import rearrange

import src.platform.runtime.native.attention as att
from src.platform.runtime.native.arch.krea2.layers import (
    Attention,
    PositionalEncoding,
    _sdpa_gqa,
)
from vendor.gpl.comfyui.ops import pick_operations

AXDIMS = [4, 6, 6]
HEADDIM = sum(AXDIMS)


def _expanded_sdpa_gqa(q, k, v, heads, kvheads, mask):
    """Verbatim pre-change body: expand, then dispatch."""
    if kvheads != heads:
        repeat = heads // kvheads
        k = k.repeat_interleave(repeat, dim=1)
        v = v.repeat_interleave(repeat, dim=1)
    out = att.attention(q, k, v, mask=mask)
    return rearrange(out, "B H L D -> B L (H D)")


def _build_attention(heads=4, kvheads=2, seed=0):
    torch.manual_seed(seed)
    ops = pick_operations(torch.float64, torch.float64)
    attn = Attention(heads * HEADDIM, heads, kvheads, operations=ops, dtype=torch.float64)
    for p in attn.parameters():
        with torch.no_grad():
            p.copy_(torch.randn_like(p) * 0.2)
    return attn.eval()


@pytest.fixture(autouse=True)
def _clean_backend_state(monkeypatch):
    monkeypatch.delenv(att.ENV_VAR, raising=False)
    att.set_backend_override(None)
    att.reset_backend_cache()
    yield
    att.set_backend_override(None)
    att.reset_backend_cache()


@pytest.mark.parametrize("heads,kvheads", [(4, 2), (8, 1), (4, 4)])
@pytest.mark.parametrize("with_mask", [False, True])
def test_sdpa_gqa_matches_the_expanded_oracle(heads, kvheads, with_mask):
    g = torch.Generator().manual_seed(3)
    q = torch.randn(2, heads, 7, HEADDIM, generator=g, dtype=torch.float64)
    k = torch.randn(2, kvheads, 7, HEADDIM, generator=g, dtype=torch.float64)
    v = torch.randn(2, kvheads, 7, HEADDIM, generator=g, dtype=torch.float64)
    mask = None
    if with_mask:
        keep = torch.ones(2, 7, dtype=torch.bool)
        keep[:, 3] = False
        mask = keep[:, None, None, :]

    actual = _sdpa_gqa(q, k, v, heads, kvheads, mask)
    expected = _expanded_sdpa_gqa(q, k, v, heads, kvheads, mask)
    assert torch.equal(actual, expected)


def test_attention_forward_matches_the_expanded_oracle(monkeypatch):
    """Through the real module, with rope applied — the entry point the DiT
    blocks actually call."""
    attn = _build_attention()
    g = torch.Generator().manual_seed(11)
    x = torch.randn(1, 12, 4 * HEADDIM, generator=g, dtype=torch.float64)
    pos = torch.randn(1, 12, 3, generator=g, dtype=torch.float64)
    freqs = PositionalEncoding(AXDIMS, 1000.0, 1.0)(pos)

    actual = attn(x.clone(), freqs=freqs)

    import src.platform.runtime.native.arch.krea2.layers as layers
    monkeypatch.setattr(layers, "_sdpa_gqa", _expanded_sdpa_gqa)
    expected = attn(x.clone(), freqs=freqs)

    assert torch.equal(actual, expected)


def test_forward_does_not_expand_the_kv_heads():
    if not att.supports_grouped_kv(att.SDPA):
        pytest.skip("installed torch SDPA has no enable_gqa; only the repeat layout exists")
    attn = _build_attention()
    g = torch.Generator().manual_seed(11)
    x = torch.randn(1, 12, 4 * HEADDIM, generator=g, dtype=torch.float64)
    seen = []
    real_repeat = torch.Tensor.repeat_interleave

    def tracking_repeat(self, *args, **kwargs):
        seen.append(tuple(self.shape))
        return real_repeat(self, *args, **kwargs)

    torch.Tensor.repeat_interleave = tracking_repeat
    try:
        attn(x)
    finally:
        torch.Tensor.repeat_interleave = real_repeat
    assert seen == []
