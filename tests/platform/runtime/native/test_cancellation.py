from __future__ import annotations

import time
import weakref

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.cancellation import (
    CancellationScope,
    _active_scope,
    cancellation_scope,
    raise_if_cancelled,
)
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.memory.partial import LayerPrefetcher, iter_streamable_leaves
from src.platform.runtime.native.sampling.conditioned import denoise_prenoised
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.kernels import disable_weight_init

from .memory.test_partial_prefetch import _install_fake_cuda


class _Flag:
    def __init__(self) -> None:
        self.set = False
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.set


class _FakeDit:
    def __init__(self, num_blocks: int, flag: _Flag, cancel_at: tuple[int, int] | None) -> None:
        self.num_blocks = num_blocks
        self.flag = flag
        self.cancel_at = cancel_at
        self.forwards = 0
        self.ran: list[tuple[int, int]] = []
        self.activations: list[weakref.ref] = []

    def __call__(self, x, sigma, conditioning):
        forward_index = self.forwards
        self.forwards += 1
        hidden = x.clone()
        for block_index in range(self.num_blocks):
            raise_if_cancelled(block_index)
            hidden = hidden * 0.5
            scratch = torch.empty(4096)
            self.activations.append(weakref.ref(scratch))
            self.ran.append((forward_index, block_index))
            if (forward_index, block_index) == self.cancel_at:
                self.flag.set = True
        return hidden - x


def _denoise(model, flag, *, sampler="euler", steps=4, cfg=False):
    return denoise(
        model, torch.zeros(1, 4, 2, 2), {"context": torch.ones(1)},
        {"context": torch.zeros(1)} if cfg else None,
        steps=steps, sampler_name=sampler,
        sampling_settings={"shift": 1.0, "guidance": "cfg" if cfg else None},
        guidance_scale=3.0 if cfg else 1.0, seed_noise=torch.randn(1, 4, 2, 2),
        is_cancelled=flag,
    )


def test_raise_if_cancelled_without_a_scope_does_nothing():
    raise_if_cancelled(0)


def test_a_scope_without_a_probe_opens_nothing():
    with cancellation_scope(None) as scope:
        assert scope is None
        assert _active_scope.get() is None
        raise_if_cancelled(3)


def test_the_scope_raises_with_its_step_and_the_block_then_closes():
    flag = _Flag()
    with pytest.raises(SamplingCancelled) as excinfo:
        with cancellation_scope(flag, step_index=5):
            raise_if_cancelled(0)
            flag.set = True
            raise_if_cancelled(7)
    assert (excinfo.value.step_index, excinfo.value.block_index) == (5, 7)
    assert str(excinfo.value) == "sampling cancelled at step 5, block 7"
    assert _active_scope.get() is None


def test_scopes_nest_and_restore_the_outer_one():
    outer, inner = _Flag(), _Flag()
    with cancellation_scope(outer) as outer_scope:
        with cancellation_scope(inner):
            inner.set = True
            with pytest.raises(SamplingCancelled):
                raise_if_cancelled(0)
        assert _active_scope.get() is outer_scope
        raise_if_cancelled(0)


def test_the_scope_tracks_the_step_it_is_dispatched():
    scope = CancellationScope(lambda: False)
    scope.on_step(3, 10, None, 0.5, None)
    assert scope.step_index == 4


def test_cancelled_error_keeps_its_older_messages():
    assert str(SamplingCancelled()) == "sampling cancelled"
    assert str(SamplingCancelled(4)) == "sampling cancelled at step 4"
    assert SamplingCancelled(4).block_index is None


@pytest.mark.parametrize("sampler", ["euler", "unipc", "dpmpp_2m", "euler_ancestral"])
def test_denoise_stops_before_the_block_after_the_one_that_saw_the_cancel(sampler):
    flag = _Flag()
    model = _FakeDit(4, flag, cancel_at=(2, 1))
    with pytest.raises(SamplingCancelled) as excinfo:
        _denoise(model, flag, sampler=sampler)
    assert model.ran[-1] == (2, 1)
    assert model.forwards == 3
    assert (excinfo.value.step_index, excinfo.value.block_index) == (2, 2)
    assert _active_scope.get() is None


def test_denoise_with_cfg_stops_inside_the_second_forward_of_a_step():
    flag = _Flag()
    model = _FakeDit(4, flag, cancel_at=(3, 0))
    with pytest.raises(SamplingCancelled) as excinfo:
        _denoise(model, flag, cfg=True)
    assert model.ran[-1] == (3, 0)
    assert (excinfo.value.step_index, excinfo.value.block_index) == (1, 1)


def test_denoise_prenoised_stops_between_blocks():
    flag = _Flag()
    model = _FakeDit(3, flag, cancel_at=(1, 0))
    with pytest.raises(SamplingCancelled) as excinfo:
        denoise_prenoised(
            model, torch.randn(1, 4, 2, 2), {"context": torch.ones(1)}, None,
            steps=4, sampling_settings={"shift": 1.0, "guidance": None}, guidance_scale=1.0,
            is_cancelled=flag,
        )
    assert model.ran[-1] == (1, 0)
    assert (excinfo.value.step_index, excinfo.value.block_index) == (1, 1)
    assert _active_scope.get() is None


def test_an_uncancelled_run_matches_a_run_without_a_probe():
    flag = _Flag()
    torch.manual_seed(0)
    with_probe = _denoise(_FakeDit(3, flag, cancel_at=None), flag)
    torch.manual_seed(0)
    without = denoise(
        _FakeDit(3, flag, cancel_at=None), torch.zeros(1, 4, 2, 2), {"context": torch.ones(1)}, None,
        steps=4, sampling_settings={"shift": 1.0, "guidance": None}, guidance_scale=1.0,
        seed_noise=torch.randn(1, 4, 2, 2),
    )
    assert torch.equal(with_probe, without)
    assert flag.calls == 4 + 4 * 3


def test_the_interrupted_forward_frees_its_tensors_while_the_error_is_still_held():
    flag = _Flag()
    model = _FakeDit(4, flag, cancel_at=(1, 2))
    with pytest.raises(SamplingCancelled) as excinfo:
        _denoise(model, flag)
    assert excinfo.value.__traceback__ is not None
    assert all(ref() is None for ref in model.activations)


class _StreamedDit(nn.Module):
    def __init__(self, flag: _Flag, cancel_at: tuple[int, int] | None) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(disable_weight_init.Linear(8, 8) for _ in range(4))
        for p in self.parameters():
            nn.init.normal_(p)
        self.flag = flag
        self.cancel_at = cancel_at
        self.forwards = 0

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        forward_index = self.forwards
        self.forwards += 1
        for i, block in enumerate(self.blocks):
            raise_if_cancelled(i)
            x = block(x)
            if (forward_index, i) == self.cancel_at:
                self.flag.set = True
        return x


def test_a_prefetching_stream_is_left_clean_by_a_cancel_between_blocks(monkeypatch):
    _install_fake_cuda(monkeypatch)
    flag = _Flag()
    dit = _StreamedDit(flag, cancel_at=(1, 1))
    leaves = [leaf for _, leaf in iter_streamable_leaves(dit)]
    weights = [leaf.weight.data_ptr() for leaf in leaves]
    x = torch.randn(2, 8)
    with torch.no_grad():
        reference = dit(x)
        prefetcher = LayerPrefetcher(dit, leaves, "cuda:0", prefetch_depth=1)
        dit(x)
        with pytest.raises(SamplingCancelled):
            with cancellation_scope(flag):
                dit(x)
        assert prefetcher._staged == {}
        assert prefetcher._orig_weight == {}
        assert [leaf.weight.data_ptr() for leaf in leaves] == weights
        assert torch.allclose(dit(x), reference, atol=1e-6)
    prefetcher.teardown()


def test_a_check_between_blocks_costs_microseconds():
    flag = _Flag()
    calls = 200_000
    with cancellation_scope(flag):
        start = time.perf_counter()
        for i in range(calls):
            raise_if_cancelled(i)
        per_check_us = (time.perf_counter() - start) / calls * 1e6
    print(f"raise_if_cancelled: {per_check_us:.3f} us per check")
    assert per_check_us < 20.0
