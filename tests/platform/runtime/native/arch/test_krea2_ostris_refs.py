from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.krea2.layers import ref_isolation_bias
from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.arch.krea2.placement import place_refs
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from .._native_ops import fp32_ops

TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 2, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _build_ready() -> Krea2:
    torch.manual_seed(0)
    m = Krea2.from_config(TINY, fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.randn_like(v) * 0.5 if k.endswith(".mod.lin") else torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.3
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _inputs():
    g = torch.Generator().manual_seed(7)
    return (
        torch.randn(1, 4, 8, 8, generator=g),
        torch.randn(1, 4, 8, 8, generator=g),
        torch.randn(1, 5, 3, 16, generator=g),
    )


def test_isolation_bias_blocks_reference_rows_from_text_and_target_only():
    bias = ref_isolation_bias(txt_len=2, ref_len=3, tgt_len=4, device=torch.device("cpu"), dtype=torch.float32)
    assert bias.shape == (1, 1, 9, 9)
    assert torch.isinf(bias[0, 0, 2:5, :2]).all() and torch.isinf(bias[0, 0, 2:5, 5:]).all()
    assert (bias[0, 0, 2:5, 2:5] == 0).all()
    assert (bias[0, 0, :2] == 0).all() and (bias[0, 0, 5:] == 0).all()


def test_origin_placement_starts_each_reference_at_zero_on_both_axes():
    ids = torch.zeros(3, 3, 3)
    ids[..., 1] = torch.arange(3, dtype=torch.float32)[:, None] + 5
    ids[..., 2] = torch.arange(3, dtype=torch.float32)[None, :] + 7
    [placed] = place_refs([ids], ["origin"], grid_h=8, grid_w=8)
    assert placed[..., 1].min().item() == 0.0 and placed[..., 2].min().item() == 0.0
    assert placed[..., 1].max().item() == 2.0


def test_a_reference_larger_than_the_target_needs_origin_placement():
    m = _build_ready()
    x = torch.randn(1, 4, 4, 4)
    big = torch.randn(1, 4, 8, 8)
    te = torch.randn(1, 5, 3, 16)
    t = torch.tensor([0.5])
    with pytest.raises(ValueError, match="exceeds the target grid"):
        m(x, t, te, ref_latents=big)
    out = m(x, t, te, ref_latents=big, ref_position="origin")
    assert out.shape == x.shape and torch.isfinite(out).all()


def test_timestep_zero_references_leave_the_default_path_untouched():
    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.5])
    plain = m(x, t, te, ref_latents=ref)
    explicit = m(x, t, te, ref_latents=ref, ref_timestep_zero=False, ref_isolated=False)
    assert torch.equal(plain, explicit)


def test_timestep_zero_references_equal_the_shared_timestep_path_at_t_zero():
    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.0])
    plain = m(x, t, te, ref_latents=ref)
    zeroed = m(x, t, te, ref_latents=ref, ref_timestep_zero=True)
    assert torch.allclose(plain, zeroed, atol=1e-5)


def test_timestep_zero_references_change_the_output_at_other_timesteps():
    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.7])
    plain = m(x, t, te, ref_latents=ref)
    zeroed = m(x, t, te, ref_latents=ref, ref_timestep_zero=True)
    assert torch.isfinite(zeroed).all()
    assert not torch.allclose(plain, zeroed, atol=1e-4)


def test_isolated_references_change_the_output_and_stay_finite():
    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.5])
    plain = m(x, t, te, ref_latents=ref)
    isolated = m(x, t, te, ref_latents=ref, ref_isolated=True)
    assert torch.isfinite(isolated).all()
    assert not torch.allclose(plain, isolated, atol=1e-4)


def _reference_states_entering_block_one(m, x, ref, te, t, **flags):
    captured = []
    handle = m.blocks[1].register_forward_pre_hook(lambda _module, args: captured.append(args[0].clone()))
    try:
        m(x, t, te, ref_latents=ref, **flags)
    finally:
        handle.remove()
    txt_len, ref_len = te.shape[1], 16
    return captured[0][:, txt_len:txt_len + ref_len]


def test_isolated_zero_timestep_references_do_not_depend_on_text_target_or_timestep():
    m = _build_ready()
    _, ref, te = _inputs()
    flags = {"ref_isolated": True, "ref_timestep_zero": True}
    first = _reference_states_entering_block_one(
        m, torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(1)), ref, te, torch.tensor([0.2]), **flags)
    other_te = torch.randn(1, 5, 3, 16, generator=torch.Generator().manual_seed(9))
    second = _reference_states_entering_block_one(
        m, torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(2)), ref, other_te, torch.tensor([0.9]), **flags)
    assert torch.allclose(first, second, atol=1e-5)


def test_shared_references_do_depend_on_text_target_and_timestep():
    m = _build_ready()
    _, ref, te = _inputs()
    first = _reference_states_entering_block_one(
        m, torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(1)), ref, te, torch.tensor([0.2]))
    second = _reference_states_entering_block_one(
        m, torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(2)), ref, te, torch.tensor([0.9]))
    assert not torch.allclose(first, second, atol=1e-4)


def test_isolation_alone_keeps_references_independent_of_the_text_and_target_but_not_the_timestep():
    m = _build_ready()
    _, ref, te = _inputs()
    x = torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(1))
    other_x = torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(2))
    same_t = torch.tensor([0.4])
    a = _reference_states_entering_block_one(m, x, ref, te, same_t, ref_isolated=True)
    b = _reference_states_entering_block_one(m, other_x, ref, te, same_t, ref_isolated=True)
    c = _reference_states_entering_block_one(m, x, ref, te, torch.tensor([0.9]), ref_isolated=True)
    assert torch.allclose(a, b, atol=1e-5)
    assert not torch.allclose(a, c, atol=1e-4)


def test_isolation_composes_with_the_reference_boost_bias():
    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.5])
    out = m(x, t, te, ref_latents=ref, ref_isolated=True, ref_boost=2.0)
    assert torch.isfinite(out).all() and out.shape == x.shape


def test_the_isolation_bias_is_built_once_per_run_not_per_step(monkeypatch):
    from types import SimpleNamespace

    from src.platform.runtime.native.arch.krea2 import model as model_module
    from src.platform.runtime.native.engine import NativeGenerator, NativeModel

    m = _build_ready()
    x, ref, te = _inputs()
    t = torch.tensor([0.5])
    builds = []
    real = model_module.ref_isolation_bias
    monkeypatch.setattr(model_module, "ref_isolation_bias", lambda *a, **k: builds.append(a) or real(*a, **k))

    with NativeGenerator._run_cache(SimpleNamespace(dit=NativeModel("diffusion_model", m))):
        for _ in range(3):
            m(x, t, te, ref_latents=ref, ref_isolated=True)
    assert len(builds) == 1

    m(x, t, te, ref_latents=ref, ref_isolated=True)
    m(x, t, te, ref_latents=ref, ref_isolated=True)
    assert len(builds) == 3


def test_a_different_token_layout_is_not_served_the_cached_bias():
    from types import SimpleNamespace

    from src.platform.runtime.native.engine import NativeGenerator, NativeModel

    m = _build_ready()
    _, ref, te = _inputs()
    t = torch.tensor([0.5])
    with NativeGenerator._run_cache(SimpleNamespace(dit=NativeModel("diffusion_model", m))):
        a = m(torch.randn(1, 4, 8, 8), t, te, ref_latents=ref, ref_isolated=True)
        b = m(torch.randn(1, 4, 4, 4), t, te, ref_latents=ref, ref_position="origin", ref_isolated=True)
    assert a.shape[-1] == 8 and b.shape[-1] == 4 and torch.isfinite(b).all()
