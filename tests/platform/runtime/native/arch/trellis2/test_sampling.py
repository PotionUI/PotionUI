"""Tests for the TRELLIS.2 flow-Euler sampler (``sampling.py``).

The load-bearing test is :func:`test_matches_upstream_sampler_on_a_dense_stage`
and its sparse twin: upstream's ``FlowEulerGuidanceIntervalSampler`` is small
enough to reproduce verbatim here, so parity is asserted against a transcription
of the algorithm rather than against numbers this port produced itself. The
transcription is the two mixins plus the Euler loop, unchanged apart from
dropping the tqdm/edict wrappers.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from src.platform.runtime.native.arch.trellis2.config import StageSampling
from src.platform.runtime.native.arch.trellis2.sampling import (
    sample_flow_stage,
    stage_timesteps,
)
from src.platform.runtime.native.sparse3d import SparseTensor


# -- upstream transcription -------------------------------------------------


class _UpstreamSampler:
    """``FlowEulerGuidanceIntervalSampler``, transcribed from
    ``trellis2/pipelines/samplers/{flow_euler,classifier_free_guidance_mixin,
    guidance_interval_mixin}.py``."""

    def __init__(self, sigma_min: float) -> None:
        self.sigma_min = sigma_min

    def _pred_to_xstart(self, x_t, t, pred):
        return (1 - self.sigma_min) * x_t - (self.sigma_min + (1 - self.sigma_min) * t) * pred

    def _xstart_to_pred(self, x_t, t, x_0):
        return ((1 - self.sigma_min) * x_t - x_0) / (self.sigma_min + (1 - self.sigma_min) * t)

    def _base_inference(self, model, x_t, t, cond, **kwargs):
        t_batch = torch.tensor([1000 * t] * x_t.shape[0], device=x_t.device, dtype=torch.float32)
        return model(x_t, t_batch, cond, **kwargs)

    def _cfg_inference(self, model, x_t, t, cond, neg_cond, guidance_strength,
                       guidance_rescale=0.0, **kwargs):
        if guidance_strength == 1:
            return self._base_inference(model, x_t, t, cond, **kwargs)
        if guidance_strength == 0:
            return self._base_inference(model, x_t, t, neg_cond, **kwargs)

        pred_pos = self._base_inference(model, x_t, t, cond, **kwargs)
        pred_neg = self._base_inference(model, x_t, t, neg_cond, **kwargs)
        pred = guidance_strength * pred_pos + (1 - guidance_strength) * pred_neg

        if guidance_rescale > 0:
            x_0_pos = self._pred_to_xstart(x_t, t, pred_pos)
            x_0_cfg = self._pred_to_xstart(x_t, t, pred)
            std_pos = _upstream_std(x_0_pos)
            std_cfg = _upstream_std(x_0_cfg)
            x_0_rescaled = _upstream_scale(x_0_cfg, std_pos / std_cfg)
            x_0 = guidance_rescale * x_0_rescaled + (1 - guidance_rescale) * x_0_cfg
            pred = self._xstart_to_pred(x_t, t, x_0)
        return pred

    def _inference_model(self, model, x_t, t, cond, neg_cond, guidance_strength,
                         guidance_interval, **kwargs):
        if guidance_interval[0] <= t <= guidance_interval[1]:
            return self._cfg_inference(model, x_t, t, cond, neg_cond, guidance_strength, **kwargs)
        return self._cfg_inference(model, x_t, t, cond, neg_cond, 1, **kwargs)

    def sample(self, model, noise, cond, neg_cond, steps, rescale_t, guidance_strength,
               guidance_interval, guidance_rescale=0.0, **kwargs):
        t_seq = np.linspace(1, 0, steps + 1)
        t_seq = rescale_t * t_seq / (1 + (rescale_t - 1) * t_seq)
        sample = noise
        for t, t_prev in zip(t_seq[:-1].tolist(), t_seq[1:].tolist()):
            pred_v = self._inference_model(
                model, sample, t, cond, neg_cond,
                guidance_strength=guidance_strength,
                guidance_interval=guidance_interval,
                guidance_rescale=guidance_rescale,
                **kwargs,
            )
            sample = sample - (t - t_prev) * pred_v
        return sample


def _upstream_std(value):
    """Upstream's ``std(dim=list(range(1, ndim)), keepdim=True)``: for a dense
    tensor torch's own reduction; for a sparse one ``VarLenTensor.reduce``'s
    channel reduction followed by a per-batch segment reduction."""
    if isinstance(value, SparseTensor):
        per_row = value.feats.mean(dim=(1,), keepdim=True)
        per_row_sq = (value.feats**2).mean(dim=(1,), keepdim=True)
        mean = torch.stack([per_row[rows].mean(dim=0) for rows in value.layout])
        mean2 = torch.stack([per_row_sq[rows].mean(dim=0) for rows in value.layout])
        return (mean2 - mean**2).sqrt()
    dims = list(range(1, value.ndim))
    return value.std(dim=dims, keepdim=True)


def _upstream_scale(value, ratio):
    if isinstance(value, SparseTensor):
        rows = torch.cat(
            [ratio[i].expand(slc.stop - slc.start, 1) for i, slc in enumerate(value.layout)]
        )
        return value.replace(value.feats * rows)
    return value * ratio


# -- fakes ------------------------------------------------------------------


class _DenseModel:
    """A deterministic stand-in with the DiT's call shape. Non-linear in ``x``
    and asymmetric in ``cond`` so cond/uncond branches cannot coincide."""

    def __init__(self, seed: int = 0) -> None:
        self.weight = torch.linspace(0.1, 0.9, 8).reshape(1, 8, 1, 1, 1)
        self.calls: list[float] = []

    def __call__(self, x, t, cond, **kwargs):
        self.calls.append(float(t[0]))
        bias = cond.mean() * 0.5
        return torch.tanh(x * self.weight + bias) + 0.01 * t.reshape(-1, 1, 1, 1, 1) / 1000.0

    def to(self, device):
        return self


class _SparseModel:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.in_channels = 4

    def __call__(self, x, t, cond, concat_cond=None, **kwargs):
        self.calls.append(float(t[0]))
        feats = x.feats
        if concat_cond is not None:
            feats = torch.cat([feats, concat_cond.feats], dim=-1)
        bias = cond.mean() * 0.5
        projected = torch.tanh(feats[:, :4] * 0.7 + bias)
        return x.replace(projected + 0.01 * float(t[0]) / 1000.0)

    def to(self, device):
        return self


def _sparse_state(rows_per_batch=(5, 3), channels=4, seed=0):
    torch.manual_seed(seed)
    coords = torch.cat([
        torch.stack([
            torch.full((rows,), batch, dtype=torch.int32),
            *(torch.arange(rows, dtype=torch.int32) + i for i in range(3)),
        ], dim=1)
        for batch, rows in enumerate(rows_per_batch)
    ])
    feats = torch.randn(sum(rows_per_batch), channels)
    return SparseTensor(feats=feats, coords=coords)


SETTINGS = StageSampling(
    steps=6, guidance_strength=7.5, guidance_rescale=0.7,
    guidance_interval=(0.6, 1.0), rescale_t=5.0,
)


# -- schedule ---------------------------------------------------------------


def test_timesteps_match_upstreams_rescaled_grid():
    t = np.linspace(1, 0, 13)
    expected = (5.0 * t / (1 + (5.0 - 1) * t)).tolist()
    assert stage_timesteps(12, 5.0) == pytest.approx(expected, abs=1e-12)


def test_timesteps_run_from_one_to_zero():
    grid = stage_timesteps(12, 3.0)
    assert len(grid) == 13
    assert grid[0] == pytest.approx(1.0)
    assert grid[-1] == pytest.approx(0.0)
    assert all(later < earlier for earlier, later in zip(grid, grid[1:]))


def test_rescale_t_of_one_is_the_unshifted_grid():
    assert stage_timesteps(4, 1.0) == pytest.approx([1.0, 0.75, 0.5, 0.25, 0.0])


def test_zero_steps_is_refused():
    with pytest.raises(ValueError, match="steps must be >= 1"):
        stage_timesteps(0, 3.0)


# -- parity -----------------------------------------------------------------


def test_matches_upstream_sampler_on_a_dense_stage():
    torch.manual_seed(0)
    noise = torch.randn(2, 8, 4, 4, 4)
    cond = torch.randn(2, 6, 16)
    neg = torch.zeros_like(cond)

    ours = sample_flow_stage(_DenseModel(), noise.clone(), cond, neg, SETTINGS)
    theirs = _UpstreamSampler(SETTINGS.sigma_min).sample(
        _DenseModel(), noise.clone(), cond, neg,
        steps=SETTINGS.steps, rescale_t=SETTINGS.rescale_t,
        guidance_strength=SETTINGS.guidance_strength,
        guidance_interval=list(SETTINGS.guidance_interval),
        guidance_rescale=SETTINGS.guidance_rescale,
    )
    assert torch.allclose(ours, theirs, atol=1e-6)


def test_matches_upstream_sampler_on_a_sparse_stage():
    noise = _sparse_state()
    cond = torch.randn(2, 6, 16)
    neg = torch.zeros_like(cond)

    ours = sample_flow_stage(_SparseModel(), noise, cond, neg, SETTINGS)
    theirs = _UpstreamSampler(SETTINGS.sigma_min).sample(
        _SparseModel(), noise, cond, neg,
        steps=SETTINGS.steps, rescale_t=SETTINGS.rescale_t,
        guidance_strength=SETTINGS.guidance_strength,
        guidance_interval=list(SETTINGS.guidance_interval),
        guidance_rescale=SETTINGS.guidance_rescale,
    )
    assert torch.allclose(ours.feats, theirs.feats, atol=1e-6)


def test_sparse_rescale_is_per_batch_element_not_per_voxel():
    """The std ratio reduces over a whole batch element, so two batch elements
    get two scalars — a per-voxel reduction would give one per row and change
    every voxel's magnitude independently."""
    state = _sparse_state(rows_per_batch=(5, 3))
    ratios = _upstream_std(state)
    assert ratios.shape == (2, 1)


# -- guidance behaviour -----------------------------------------------------


def test_guidance_interval_skips_the_uncond_pass_outside_the_window():
    """Outside the interval a step is a single conditional forward — the same
    saving upstream makes by delegating with ``guidance_strength=1``."""
    settings = StageSampling(
        steps=4, guidance_strength=7.5, guidance_rescale=0.0,
        guidance_interval=(0.9, 1.0), rescale_t=1.0,
    )
    model = _DenseModel()
    sample_flow_stage(model, torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16),
                      torch.zeros(1, 4, 16), settings)

    # t = 1.0, 0.75, 0.5, 0.25: only the first is inside [0.9, 1.0].
    assert len(model.calls) == 5


def test_strength_of_one_never_runs_the_uncond_pass():
    settings = StageSampling(
        steps=3, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    model = _DenseModel()
    sample_flow_stage(model, torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16),
                      torch.zeros(1, 4, 16), settings)
    assert len(model.calls) == 3


def test_absent_negative_conditioning_runs_conditional_only():
    settings = StageSampling(
        steps=3, guidance_strength=7.5, guidance_rescale=0.5,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    model = _DenseModel()
    sample_flow_stage(model, torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16), None, settings)
    assert len(model.calls) == 3


def test_timesteps_reach_the_model_scaled_by_a_thousand():
    settings = StageSampling(
        steps=2, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    model = _DenseModel()
    sample_flow_stage(model, torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16),
                      torch.zeros(1, 4, 16), settings)
    assert model.calls == pytest.approx([1000.0, 500.0])


def test_rescale_of_zero_leaves_the_plain_cfg_combination():
    settings = StageSampling(
        steps=2, guidance_strength=3.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    noise = torch.randn(1, 8, 2, 2, 2)
    cond, neg = torch.randn(1, 4, 16), torch.zeros(1, 4, 16)

    out = sample_flow_stage(_DenseModel(), noise.clone(), cond, neg, settings)
    theirs = _UpstreamSampler(settings.sigma_min).sample(
        _DenseModel(), noise.clone(), cond, neg, steps=2, rescale_t=1.0,
        guidance_strength=3.0, guidance_interval=[0.0, 1.0], guidance_rescale=0.0,
    )
    assert torch.allclose(out, theirs, atol=1e-6)


def test_cancellation_stops_the_loop():
    settings = StageSampling(
        steps=8, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    model = _DenseModel()
    seen: list[int] = []
    sample_flow_stage(
        model, torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16), torch.zeros(1, 4, 16),
        settings, on_step=lambda step, total: seen.append(step),
        is_cancelled=lambda: len(seen) >= 3,
    )
    assert seen == [0, 1, 2]


def test_progress_reports_every_step():
    settings = StageSampling(
        steps=5, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    seen: list[tuple[int, int]] = []
    sample_flow_stage(
        _DenseModel(), torch.zeros(1, 8, 2, 2, 2), torch.randn(1, 4, 16),
        torch.zeros(1, 4, 16), settings, on_step=lambda step, total: seen.append((step, total)),
    )
    assert seen == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5)]


def test_texture_stage_forwards_its_concat_cond():
    settings = StageSampling(
        steps=2, guidance_strength=1.0, guidance_rescale=0.0,
        guidance_interval=(0.0, 1.0), rescale_t=1.0,
    )
    state = _sparse_state(rows_per_batch=(4,), channels=4)
    shape_latent = state.replace(torch.randn(state.feats.shape[0], 4))
    model = _SparseModel()

    out = sample_flow_stage(
        model, state, torch.randn(1, 4, 16), None, settings,
        forward_kwargs={"concat_cond": shape_latent},
    )
    assert out.feats.shape == state.feats.shape
    assert len(model.calls) == 2
