from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from src.platform.runtime.native.kernels import capabilities, fused_elementwise, pick_operations
from src.platform.settings.runtime_flags import runtime_flag_values

from .test_flux_model import TINY_FLUX2
from .test_krea2_model import TINY as KREA2_TINY
from .test_z_image_model import ZTINY

pytestmark = pytest.mark.requires_gpu

RELATIVE_TOLERANCE = 2.0 ** -6


@pytest.fixture(autouse=True)
def _gpu(monkeypatch):
    caps = capabilities()
    if not torch.cuda.is_available() or not caps.triton or not caps.device(0).at_least(8, 0):
        pytest.skip("needs a CUDA GPU of compute capability 8.0+ with Triton")
    fused_elementwise.reset_fused_elementwise_state()
    yield
    assert fused_elementwise.fused_elementwise_disabled_reason() is None


def _random(model, seed):
    g = torch.Generator().manual_seed(seed)
    return {
        k: (torch.randn(v.shape, generator=g) * 0.1).to(v.dtype) if v.is_floating_point() else v.clone()
        for k, v in model.state_dict().items()
    }


def _build(cls, config, seed):
    model = cls.from_config(config, pick_operations(torch.float32, torch.bfloat16))
    model.load_state_dict(_random(model, seed))
    return model.eval().to("cuda")


def _on_and_off(monkeypatch, run):
    routed = []
    real = fused_elementwise._run_fused

    def counting(name, *args):
        routed.append(name)
        return real(name, *args)

    monkeypatch.setattr(fused_elementwise, "_run_fused", counting)
    outs = {}
    for flag in (False, True):
        monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", flag)
        torch.manual_seed(11)
        with torch.inference_mode():
            outs[flag] = run().float()
    assert routed, "the flag-on forward never took the Triton path"
    return outs[True], outs[False], routed


def _assert_close_to_unfused(fused, unfused):
    assert torch.isfinite(fused).all()
    error = (fused - unfused).abs().max()
    assert error <= RELATIVE_TOLERANCE * unfused.abs().max(), (float(error), float(unfused.abs().max()))


def test_a_flux2_forward_with_fused_kernels_matches_the_unfused_one(monkeypatch):
    model = _build(Flux, TINY_FLUX2, 1)
    x = torch.randn(1, 16, 16, 16, device="cuda").to(torch.bfloat16)
    ctx = torch.randn(1, 7, 32, device="cuda").to(torch.bfloat16)
    fused, unfused, routed = _on_and_off(monkeypatch, lambda: model(x, torch.tensor([0.5], device="cuda"), ctx))
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)


def test_a_z_image_forward_with_fused_kernels_matches_the_unfused_one(monkeypatch):
    model = _build(ZImageDiT, ZTINY, 2)
    x = torch.randn(2, 16, 8, 12, device="cuda").to(torch.bfloat16)
    ctx = torch.randn(2, 7, ZTINY["cap_feat_dim"], device="cuda").to(torch.bfloat16)
    t = torch.full((2,), 0.5, device="cuda")
    fused, unfused, routed = _on_and_off(monkeypatch, lambda: model(x, t, ctx))
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)


def test_a_krea2_reference_forward_with_fused_spans_matches_the_unfused_one(monkeypatch):
    model = _build(Krea2, KREA2_TINY, 3)
    x = torch.randn(1, 4, 8, 8, device="cuda").to(torch.bfloat16)
    te = torch.randn(1, 5, 3, KREA2_TINY["txtdim"], device="cuda").to(torch.bfloat16)
    ref = torch.randn(1, 4, 8, 8, device="cuda").to(torch.bfloat16)
    t = torch.tensor([0.5], device="cuda")
    fused, unfused, routed = _on_and_off(
        monkeypatch, lambda: model(x, t, te, ref_latents=ref, ref_timestep_zero=True),
    )
    assert {"fused_norm_modulate", "fused_gated_residual", "fused_act_gate", "fused_qk_norm_rope"} <= set(routed)
    _assert_close_to_unfused(fused, unfused)
