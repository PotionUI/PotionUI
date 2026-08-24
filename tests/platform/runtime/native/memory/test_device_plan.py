"""Tests for device assignment (no GPU required; queries injected)."""

from __future__ import annotations

from src.platform.runtime.native.memory.device_plan import DevicePlan, make_device_plan

_GB = 1024 ** 3


def test_cpu_fallback_when_no_cuda():
    plan = make_device_plan(cuda_available=lambda: False)
    assert plan == DevicePlan("cpu", "cpu", "cpu")


def test_single_gpu_all_same_device():
    plan = make_device_plan(
        cuda_available=lambda: True,
        device_count=lambda: 1,
        mem_get_info=lambda i: (24 * _GB, 24 * _GB),
    )
    assert plan.dit_device == plan.te_device == plan.vae_device == "cuda:0"


def test_preferred_device_respected():
    plan = make_device_plan(
        "cuda:1",
        cuda_available=lambda: True,
        device_count=lambda: 1,
    )
    assert plan.dit_device == "cuda:1"


def test_multi_gpu_no_spill_when_dit_small():
    # DiT 9GB on a 24GB card is < 70% -> no spill, everything stays on cuda:0.
    plan = make_device_plan(
        dit_gb=9.0,
        cuda_available=lambda: True,
        device_count=lambda: 2,
        mem_get_info=lambda i: (24 * _GB, 24 * _GB),
    )
    assert plan.te_device == "cuda:0"


def test_multi_gpu_spills_te_when_dit_dominates():
    # DiT 20GB on a 24GB card is > 70% -> TE spills to the GPU with most free mem.
    free = {0: 4 * _GB, 1: 22 * _GB, 2: 10 * _GB}
    plan = make_device_plan(
        dit_gb=20.0,
        cuda_available=lambda: True,
        device_count=lambda: 3,
        mem_get_info=lambda i: (free[i], 24 * _GB),
    )
    assert plan.dit_device == "cuda:0"
    assert plan.te_device == "cuda:1"     # most free memory, not the DiT device
    assert plan.vae_device == "cuda:0"    # VAE always stays with the DiT


def test_multi_gpu_no_spill_without_dit_size():
    # Without a DiT size we cannot evaluate the 70% rule -> stay single-device.
    plan = make_device_plan(
        cuda_available=lambda: True,
        device_count=lambda: 2,
        mem_get_info=lambda i: (24 * _GB, 24 * _GB),
    )
    assert plan.te_device == "cuda:0"
