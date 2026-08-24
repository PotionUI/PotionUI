"""FBCache integration on the Z-Image (NextDiT) arch.

Z-Image has no arch-level model test to borrow a builder from, so this file
builds a tiny ZImageDiT directly (norm scales -> 1, other params -> small random).
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from vendor.gpl.comfyui.ops import pick_operations
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

_T = torch.tensor([0.5])

# dim must be >= 256 (the t-embed width the adaLN Linear consumes is min(dim,256),
# and the timestep embedder always emits 256). dim 256 / n_heads 4 -> head_dim 64
# == sum(axes_dims). n_layers 2 gives a block[-1] for the "later blocks" test.
ZTINY = {
    "image_model": "lumina2", "in_channels": 16, "dim": 256, "cap_feat_dim": 8,
    "n_layers": 2, "n_refiner_layers": 1, "n_heads": 4, "n_kv_heads": 4,
    "intermediate_size": 128, "axes_dims": (32, 16, 16), "patch_size": 2,
}


def _build() -> ZImageDiT:
    m = ZImageDiT.from_config(ZTINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    for k, v in m.state_dict().items():
        if not v.is_floating_point():
            sd[k] = v.clone()
        elif "norm" in k and (k.endswith(".weight") or k.endswith(".scale")):
            sd[k] = torch.ones_like(v)
        else:
            sd[k] = torch.randn_like(v) * 0.02
    m.load_state_dict(sd)
    return m.eval()


def _ctx():
    return torch.randn(1, 4, 8)


def _fwd(m, x, ctx, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, **kw)


def test_step_cache_none_is_byte_identical():
    m = _build()
    x = torch.randn(1, 16, 16, 16)
    ctx = _ctx()
    base = _fwd(m, x, ctx)
    assert torch.equal(base, _fwd(m, x, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build()
    x = torch.randn(1, 16, 16, 16)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)  # includes the final negation


def test_different_inputs_do_not_skip():
    m = _build()
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 16, 16, 16), ctx, step_cache=cache)
    _fwd(m, torch.randn(1, 16, 16, 16) * 5.0, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build()  # n_layers=2 -> self.layers[-1] is block 1
    x = torch.randn(1, 16, 16, 16)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    last = m.layers[-1]
    orig = last.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    last.forward = counting
    _fwd(m, x, ctx, step_cache=cache)
    assert calls["n"] == 1
    _fwd(m, x, ctx, step_cache=cache)
    assert calls["n"] == 1


def test_resolution_change_forces_compute():
    m = _build()
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 16, 16, 16), ctx, step_cache=cache)
    out = _fwd(m, torch.randn(1, 16, 16, 32), ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape[-1] == 32
