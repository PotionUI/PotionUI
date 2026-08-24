"""Numerical parity between the director pipe and the GPU-validated t2v path.

With ZERO conditions (no media, no extras, no audio) the ``generator/video_ltx``
stack must be numerically identical to the plain ``generator/txt2vid_ltx`` path
that was GPU-validated on real LTX-2.3 weights: same single-forward output, same
full euler trajectory, same sigma schedule. These tests pin that equivalence on
a tiny 2.3-featured model (gated blocks, prompt-adaLN, cross-timestep) so a
regression in the packing / per-token-timestep / x0-blend plumbing shows up on
CPU instead of as noise on a GPU box.

Also pins the conditioned-path invariants the blend math promises:
- masked tokens' x0 is EXACTLY the clean latent (strength-1 pinning);
- a per-token timestep whose values all equal the scalar sigma produces output
  bit-identical to the legacy scalar-timestep call.
"""

from __future__ import annotations

import pytest
import torch

from tests.platform.runtime.native.arch.test_ltx_forward import TINY_23, _build
from src.platform.runtime.native.sampling import conditioned_sigmas, denoise_prenoised
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.pipelines.pipes.generator.video_ltx.conditioning import PreparedConditioning, mix_initial_noise
from src.pipelines.pipes._shared.generation.ltx_conditioned_forward import ConditionedAVForward
from src.pipelines.pipes.generator.video_ltx.main import _VideoLtxCtx

_SETTINGS = {"prediction": "const", "shift": 2.37, "guidance": "cfg"}
_T, _H, _W = 3, 2, 2
_S = _T * _H * _W
_PPF = _H * _W
_FPS = 25.0


@pytest.fixture(scope="module")
def model():
    torch.manual_seed(7)
    return _build(TINY_23)


@pytest.fixture(scope="module")
def context():
    torch.manual_seed(3)
    dim = TINY_23["cross_attention_dim"] + TINY_23["audio_cross_attention_dim"]
    return torch.randn(1, 5, dim)


def _fwd(model, mask=None, clean=None):
    c = TINY_23["in_channels"]
    prepared = PreparedConditioning(
        tokens=(clean.clone() if clean is not None else torch.zeros(1, _S, c)),
        mask=(mask if mask is not None else torch.zeros(1, _S)),
        clean=(clean if clean is not None else torch.zeros(1, _S, c)),
        extra_coords=None, n_extra=0, base_tokens=_S,
    )
    ctx = _VideoLtxCtx(
        bundle=None, sampling_settings=dict(_SETTINGS), conditioning=[],
        prepared=prepared, steps=6, cfg=1.0, sampler="euler",
        width=_W * 32, height=_H * 32, frames=1 + 8 * (_T - 1), fps=_FPS,
        device="cpu", dtype=torch.float32, spec=None,
        audio_mode="none", audio_file=None, audio_tokens=0,
        t_lat=_T, h_lat=_H, w_lat=_W,
    )
    return ConditionedAVForward(model, ctx), prepared


def _pack(x5d):
    b, c = x5d.shape[0], x5d.shape[1]
    return x5d.permute(0, 2, 3, 4, 1).reshape(b, _S, c)


def test_single_forward_matches_legacy_call(model, context):
    torch.manual_seed(11)
    c = TINY_23["in_channels"]
    x5d = torch.randn(1, c, _T, _H, _W)
    sigma = torch.tensor([0.7])
    fwd, _ = _fwd(model)
    with torch.inference_mode():
        legacy = model([x5d], sigma, context, attention_mask=None, frame_rate=_FPS)
        director = fwd.unpack_base(fwd(_pack(x5d), sigma, {"context": context}))
    assert (legacy - director).abs().max().item() < 1e-5


def test_full_trajectory_matches_legacy_denoise(model, context):
    torch.manual_seed(13)
    c = TINY_23["in_channels"]
    seed_noise = torch.randn(1, c, _T, _H, _W)
    cond = {"context": context}

    def legacy_forward(x, s, conditioning):
        return model([x], s, conditioning["context"], attention_mask=None, frame_rate=_FPS)

    fwd, prepared = _fwd(model)
    with torch.inference_mode():
        out_legacy = denoise(
            legacy_forward, torch.zeros_like(seed_noise), cond, None,
            steps=6, sampler_name="euler", sampling_settings=dict(_SETTINGS),
            guidance_scale=1.0, seed_noise=seed_noise,
        )
        sigmas = conditioned_sigmas(6, _SETTINGS)
        x0 = mix_initial_noise(prepared, _pack(seed_noise), float(sigmas[0]))
        out_director = fwd.unpack_base(denoise_prenoised(
            fwd, x0, cond, None, steps=6, sampler_name="euler",
            sampling_settings=dict(_SETTINGS), guidance_scale=1.0, sigmas=sigmas,
        ))
    assert (out_legacy - out_director).abs().max().item() < 1e-4


def test_conditioned_sigmas_matches_legacy_schedule():
    new = conditioned_sigmas(6, _SETTINGS)
    old = build_sigmas(
        6, shift=_SETTINGS.get("shift"), base_shift=_SETTINGS.get("base_shift"),
        max_shift=_SETTINGS.get("max_shift"), dynamic_shift=_SETTINGS.get("dynamic_shift"),
    )
    assert torch.allclose(new, old)
    assert float(new[0]) == pytest.approx(1.0)


def test_masked_tokens_x0_pinned_exactly(model, context):
    torch.manual_seed(17)
    c = TINY_23["in_channels"]
    mask = torch.zeros(1, _S)
    mask[:, :_PPF] = 1.0
    clean = torch.randn(1, _S, c) * 0.5
    fwd, _ = _fwd(model, mask=mask, clean=clean)

    sigma = torch.tensor([0.7])
    x = torch.randn(1, _S, c)
    x[:, :_PPF] = clean[:, :_PPF]
    with torch.inference_mode():
        v = fwd(x, sigma, {"context": context})
    x0 = x - sigma.view(1, 1, 1) * v
    assert (x0[:, :_PPF] - clean[:, :_PPF]).abs().max().item() < 1e-5


def test_per_token_timestep_equals_scalar_when_uniform(model, context):
    torch.manual_seed(19)
    c = TINY_23["in_channels"]
    x5d = torch.randn(1, c, _T, _H, _W)
    scalar = torch.tensor([0.7])
    per_token = torch.full((1, _S), 0.7)
    with torch.inference_mode():
        a = model([x5d], scalar, context, frame_rate=_FPS)
        b = model([x5d], (per_token, scalar), context, frame_rate=_FPS, sigma=scalar)
    assert (a - b).abs().max().item() < 1e-6


def test_mix_initial_noise_reduces_to_legacy_init_when_unconditioned():
    torch.manual_seed(23)
    c = TINY_23["in_channels"]
    prepared = PreparedConditioning(
        tokens=torch.zeros(1, _S, c), mask=torch.zeros(1, _S),
        clean=torch.zeros(1, _S, c), extra_coords=None, n_extra=0, base_tokens=_S,
    )
    noise = torch.randn(1, _S, c)
    sigma0 = 1.0
    mixed = mix_initial_noise(prepared, noise, sigma0)
    # legacy denoise() init: sigma0*noise + (1-sigma0)*zeros
    assert torch.equal(mixed, noise * sigma0)
