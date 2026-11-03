from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3 import flow as music3_flow
from src.platform.runtime.native.arch.seedvr2.config import SeedVR2Config
from src.platform.runtime.native.arch.seedvr2.model import SeedVR2
from src.platform.runtime.native.arch.seedvr2_7b.config import SeedVR27BConfig
from src.platform.runtime.native.arch.seedvr2_7b.model import SeedVR27B
from src.platform.runtime.native.arch.trellis2.config import StageSampling
from src.platform.runtime.native.arch.trellis2.sampling import sample_flow_stage
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.yue2 import nar as yue2_nar
from src.platform.runtime.native.cancellation import cancellation_scope, raise_if_cancelled
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sparse3d import SparseTensor

from .._native_ops import fp32_ops
from .test_cancel_between_blocks import _cancel_after_first_block, _Flag
from .test_minimax_music3_model import TINY_CONFIG as TINY_MUSIC3
from .test_minimax_music3_model import _build_ready as _build_music3
from .test_seedvr2_model import TINY_3B, TINY_7B
from .test_seedvr2_model import _randomize as _randomize_seedvr2
from .trellis2.test_slat_flow import _TINY_CONFIG as TINY_SLAT
from .trellis2.test_ss_flow import TINY as TINY_SS
from .trellis2.test_ss_flow import _build as _build_ss
from .yue2.test_nar import _tiny_model as _yue2_model


def _seedvr2_case(model):
    _randomize_seedvr2(model)
    model.eval()
    vid, txt = torch.randn(1, 8, 2, 8, 8), torch.randn(5, 16)
    return model.blocks, lambda: model(vid, torch.tensor(0.5), txt)


def _ss_flow_case():
    model = _build_ss()
    _randomize_seedvr2(model)
    x = torch.randn(1, TINY_SS.in_channels, TINY_SS.resolution, TINY_SS.resolution, TINY_SS.resolution)
    cond = torch.randn(1, 5, TINY_SS.cond_channels)
    return model.blocks, lambda: model(x, torch.tensor([250.0]), cond)


def _slat_flow_case():
    model = SLatFlowModel(**TINY_SLAT).eval()
    coords = torch.tensor([[0, 0, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0], [0, 1, 0, 0]])
    feats = torch.randn(coords.shape[0], TINY_SLAT["in_channels"])
    cond = torch.randn(1, 5, TINY_SLAT["cond_channels"])
    return model.blocks, lambda: model(SparseTensor(feats, coords), torch.tensor([250.0]), cond)


def _music3_case():
    model = _build_music3(TINY_MUSIC3)
    latents = torch.randn(1, TINY_MUSIC3["in_channels"], 8)
    condition = torch.randn(1, 8, TINY_MUSIC3["condition_dim"])
    return model.diffusion_transformer.transformer.layers, lambda: model(latents, torch.tensor([0.5]), condition)


CASES = {
    "seedvr2-3b": lambda: _seedvr2_case(SeedVR2(SeedVR2Config(**TINY_3B), fp32_ops())),
    "seedvr2-7b": lambda: _seedvr2_case(SeedVR27B(SeedVR27BConfig(**TINY_7B), fp32_ops())),
    "trellis2-sparse-structure": _ss_flow_case,
    "trellis2-structured-latent": _slat_flow_case,
    "minimax-music3": _music3_case,
}


@pytest.mark.parametrize("family", list(CASES))
def test_a_cancel_seen_after_a_block_stops_the_dit_before_the_next_one(family):
    blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(flag, step_index=3):
            run()
    assert calls == {"first": 1, "second": 0}
    assert (excinfo.value.step_index, excinfo.value.block_index) == (3, 1)


@pytest.mark.parametrize("family", list(CASES))
def test_a_set_probe_outside_any_scope_leaves_the_forward_untouched(family):
    blocks, run = CASES[family]()
    flag = _Flag()
    calls = _cancel_after_first_block(blocks, flag)
    with torch.no_grad():
        run()
        run()
    assert calls == {"first": 2, "second": 2}


@pytest.mark.parametrize("family", list(CASES))
def test_an_open_scope_that_is_never_cancelled_gives_a_bit_identical_output(family):
    _, run = CASES[family]()
    with torch.no_grad():
        plain = run()
        with cancellation_scope(lambda: False, step_index=0):
            scoped = run()
    assert torch.equal(getattr(plain, "feats", plain), getattr(scoped, "feats", scoped))


def _yue2_case():
    model = _yue2_model()
    context = model.new_nar_context(torch.randint(0, 64, (1, 6)), num_latent_frames=5)
    return model, lambda: model.nar_velocity(context, torch.randn(5, model.cfg.latent_dim), t_value=0.3)


def test_a_yue2_flow_velocity_stops_before_the_next_layer():
    model, run = _yue2_case()
    flag = _Flag()
    ran = []
    layers = model.model.layers
    first, second = layers[0].nar_forward, layers[1].nar_forward

    def first_then_cancel(*args, **kwargs):
        ran.append(0)
        out = first(*args, **kwargs)
        flag.set = True
        return out

    def counting(*args, **kwargs):
        ran.append(1)
        return second(*args, **kwargs)

    layers[0].nar_forward = first_then_cancel
    layers[1].nar_forward = counting

    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(flag, step_index=3):
            run()
    assert ran == [0]
    assert (excinfo.value.step_index, excinfo.value.block_index) == (3, 1)


class _CancellingModel:
    def __init__(self, flag: _Flag, forward_that_cancels: int) -> None:
        self.flag = flag
        self.forward_that_cancels = forward_that_cancels
        self.forwards = 0

    def blocks(self) -> None:
        for block_index in range(3):
            raise_if_cancelled(block_index)
            if self.forwards == self.forward_that_cancels and block_index == 1:
                self.flag.set = True
        self.forwards += 1


def test_the_trellis2_sampler_runs_its_forwards_inside_a_scope():
    flag = _Flag()
    model = _CancellingModel(flag, forward_that_cancels=2)

    def forward(x, t, cond, **kwargs):
        model.blocks()
        return torch.zeros_like(x)

    settings = StageSampling(steps=4, guidance_strength=1.0, guidance_rescale=0.0, guidance_interval=(0.0, 1.0),
                             rescale_t=1.0, sigma_min=1e-5)
    with pytest.raises(SamplingCancelled) as excinfo:
        sample_flow_stage(forward, torch.zeros(1, 4, 2, 2, 2), torch.zeros(1, 2, 4), None, settings, is_cancelled=flag)
    assert (excinfo.value.step_index, excinfo.value.block_index) == (2, 2)


def test_the_music3_flow_runs_its_forwards_inside_a_scope():
    flag = _Flag()
    tracker = _CancellingModel(flag, forward_that_cancels=3)

    class _Model:
        config = SimpleNamespace(in_channels=4)

        @staticmethod
        def encode_condition(frames):
            return torch.zeros(1, 8, 3)

        def __call__(self, latents, timestep, condition):
            tracker.blocks()
            return torch.zeros_like(latents)

    with pytest.raises(SamplingCancelled) as excinfo:
        music3_flow.denoise_windowed(
            _Model(), torch.zeros(1, 5, 8), steps=3, cfg_scale=1.5, generator=torch.Generator(),
            device=torch.device("cpu"), dtype=torch.float32, is_cancelled=flag,
        )
    assert (excinfo.value.step_index, excinfo.value.block_index) == (1, 2)


def test_the_yue2_flow_runs_its_velocities_inside_a_scope():
    flag = _Flag()
    tracker = _CancellingModel(flag, forward_that_cancels=3)
    model = _yue2_model()
    real_velocity = model.nar_velocity

    def velocity(context, state, t_value):
        tracker.blocks()
        return real_velocity(context, state, t_value)

    model.nar_velocity = velocity

    with torch.no_grad(), pytest.raises(SamplingCancelled) as excinfo:
        yue2_nar.synthesize(model, [1, 2, 3], [0, 1, 2], seed=0, steps=4, is_cancelled=flag)
    assert (excinfo.value.step_index, excinfo.value.block_index) == (1, 2)
