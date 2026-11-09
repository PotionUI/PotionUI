from __future__ import annotations

import traceback
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.features.generation.error_classification import classify_generation_error
from src.pipelines.pipes._shared.generation import dit_placement
from src.pipelines.pipes._shared.vae import ltx_tiled_encode, minimax_h3_encode, wan_tiled_encode
from src.pipelines.pipes._shared.video import wan_experts
from src.pipelines.pipes.generator.seedvr2 import color_fix
from src.platform.runtime import cuda_allocator
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.errors import HostMemoryExhaustedError, SamplingCancelled

DEVICE_NOT_READY = "CUDA driver error: device not ready"


class _Failing:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise self.exc


def _move_resident(op):
    dit = SimpleNamespace(move_to=op, stream_to=op, offload=lambda: None)
    dit_placement._move_resident(dit, "cuda", [dit])


def _move_partial(op):
    dit = SimpleNamespace(move_to=op, stream_to=op, offload=lambda: None)
    dit_placement._move_partial(dit, "cuda", 4.0, [dit])


def _first_forward(op):
    dit_placement._forward_with_degrade(op, (), {}, dit=None, device="cuda", decision=None, fit_hint=None)


def _native_full_move(op):
    gen = NativeGenerator.__new__(NativeGenerator)
    gen.dit = SimpleNamespace(estimated_vram_gb=1.0, move_to=op, stream_to=op, offload=lambda: None)
    gen._ensure_room_for = lambda need, device: None
    gen._move_dit_to_gpu("cuda:0")


def _ltx_encode(op):
    module = SimpleNamespace(encode=op, tiled_encode=op)
    vae = SimpleNamespace(module=module, compute_dtype=torch.float32)
    with patch.object(ltx_tiled_encode, "free_vram_gb", return_value=64.0):
        ltx_tiled_encode.encode_with_oom_retry(vae, torch.zeros(1, 3, 9, 16, 16), "cpu", profiler_mark="test.encode")


def _wan_encode(op):
    module = SimpleNamespace(encode=op)
    wan_tiled_encode.make_wan_vae_encode(module, 64, 64, log_prefix="test")(torch.zeros(1, 3, 1, 64, 64))


def _h3_encode(op):
    module = SimpleNamespace(use_tiling=False, tile_sample_min_height=0, tile_sample_min_width=0, encode_tile_batch_size=1)
    plan = {"tile_px": 256, "batch_size": 1}
    with patch.object(minimax_h3_encode, "plan_encode", return_value=plan), \
            patch.object(minimax_h3_encode, "_encode_with_plan", op):
        minimax_h3_encode.encode_video(module, torch.zeros(1, 3, 5, 16, 16), "cpu")


def _colour_fix(op):
    frames = [np.zeros((4, 4, 3), dtype=np.uint8)]
    with patch.object(color_fix, "_correct_chunk", op):
        color_fix.color_correct_batch(frames, frames, "wavelet", device="cpu")


def _wan_expert(op):
    dit = SimpleNamespace(estimated_vram_gb=100.0, move_to=op, stream_to=op, offload=lambda: None)
    router = wan_experts.ExpertRouter(dit, None, 0.9, "cuda", latents_shape=(1, 16, 3, 8, 8))
    with patch.object(wan_experts.ExpertRouter, "_weights_budget_gb", return_value=4.0):
        router._place_expert(dit)


def _wan_decode_ctx(op):
    return SimpleNamespace(vae=SimpleNamespace(module=SimpleNamespace(decode=op)), device="cpu", spatial_downscale=8,
                           spec=SimpleNamespace(latent_format={}))


def _wan_decode(op):
    wan_experts._decode_pixels(_wan_decode_ctx(op), torch.zeros(1, 16, 1, 8, 64))


def _wan_decode_one_latent_frame_at_a_time(op):
    whole = _Failing(torch.cuda.OutOfMemoryError("CUDA out of memory"))
    with patch.object(wan_experts, "chunked_decode_causal3d", op):
        wan_experts._decode_pixels(_wan_decode_ctx(whole), torch.zeros(1, 16, 1, 8, 64))


def _wan_tiled_decode(op):
    with patch.object(wan_experts, "tiled_decode_causal3d", op):
        wan_experts._decode_tiled(_wan_decode_ctx(op), torch.zeros(1, 16, 1, 8, 64), 100.0)


def _wan_tiled_decode_at_the_smallest_tile(op):
    with patch.object(wan_experts, "tiled_decode_causal3d", op):
        wan_experts._decode_tiled(_wan_decode_ctx(op), torch.zeros(1, 16, 1, 8, 64), 0.001)


LADDERS = [
    _move_resident, _move_partial, _first_forward, _native_full_move, _wan_expert,
    _ltx_encode, _wan_encode, _h3_encode, _colour_fix,
    _wan_decode, _wan_decode_one_latent_frame_at_a_time, _wan_tiled_decode, _wan_tiled_decode_at_the_smallest_tile,
]
ladders = pytest.mark.parametrize("ladder", LADDERS, ids=[ladder.__name__ for ladder in LADDERS])


@pytest.fixture
def fatal_policy(monkeypatch):
    stopped = []
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: stopped.append(True))
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    return stopped


@ladders
def test_a_fatal_driver_oom_is_not_retried_and_marks_the_process_once(ladder, fatal_policy):
    op = _Failing(RuntimeError(DEVICE_NOT_READY))

    with pytest.raises(RuntimeError) as caught:
        ladder(op)

    assert caught.value is op.exc
    assert op.calls == 1
    assert cuda_allocator.allocator_damage() == DEVICE_NOT_READY
    assert fatal_policy == [True]


@ladders
@pytest.mark.parametrize(
    "exc",
    [
        SamplingCancelled(3),
        HostMemoryExhaustedError("partial-residency streaming needs more host RAM"),
        ValueError("CUDA driver error: device not ready"),
        RuntimeError("mat1 and mat2 shapes cannot be multiplied"),
        RuntimeError("CUDA driver error: invalid argument"),
    ],
    ids=["cancelled", "host_ram", "not_a_runtime_error", "ordinary_runtime_error", "other_driver_error"],
)
def test_what_is_not_an_oom_passes_through_untouched_with_its_trace(ladder, fatal_policy, exc):
    op = _Failing(exc)

    with pytest.raises(type(exc)) as caught:
        ladder(op)

    assert caught.value is exc
    assert op.calls == 1
    assert cuda_allocator.allocator_damage() is None
    assert fatal_policy == []
    assert "__call__" in [frame.name for frame in traceback.extract_tb(caught.value.__traceback__)]


def test_a_later_oom_of_torch_stays_retryable_in_a_damaged_process(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", DEVICE_NOT_READY)
    monkeypatch.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    outcomes = iter([torch.cuda.OutOfMemoryError("CUDA out of memory"), "ok"])

    def forward():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert dit_placement._forward_with_degrade(forward, (), {}, dit=None, device="cpu", decision=SimpleNamespace(
        video_tokens=1, audio_tokens=0, activation_reserve_gb=1.0, mode="resident"), fit_hint=None) == "ok"


def _stream_failing_once(exc):
    streamed = []

    def stream_to(device, budget):
        streamed.append(budget)
        if len(streamed) == 1:
            raise exc

    return streamed, SimpleNamespace(stream_to=stream_to, offload=lambda: None)


def test_native_linux_keeps_retrying_a_driver_oom_and_marks_nothing(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: False)
    monkeypatch.delenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, raising=False)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    streamed, dit = _stream_failing_once(RuntimeError(DEVICE_NOT_READY))

    with patch.object(dit_placement, "get_residency_registry"):
        assert dit_placement._move_partial(dit, "cpu", 4.0, [dit]) == "partial"

    assert streamed == [4.0, 0.0]
    assert cuda_allocator.allocator_damage() is None
    assert classify_generation_error(RuntimeError(DEVICE_NOT_READY)).category == "cuda_oom"
    assert cuda_allocator.allocator_damage() is None


def test_a_torch_oom_is_retried_under_wsl_and_marks_nothing(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: True)
    monkeypatch.delenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, raising=False)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    streamed, dit = _stream_failing_once(torch.cuda.OutOfMemoryError("CUDA out of memory"))

    with patch.object(dit_placement, "get_residency_registry"):
        assert dit_placement._move_partial(dit, "cpu", 4.0, [dit]) == "partial"

    assert streamed == [4.0, 0.0]
    assert cuda_allocator.allocator_damage() is None
    assert classify_generation_error(torch.cuda.OutOfMemoryError("CUDA out of memory")).category == "cuda_oom"
