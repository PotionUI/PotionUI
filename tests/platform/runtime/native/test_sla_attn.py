"""Tests for the opt-in SLA-Attn seam (`src/platform/runtime/native/sla_attn.py`).

The contract under test is the failure contract, not the kernel: SLA-Attn must
never raise, must fall back to "the caller does what it always did" for every
reason it cannot run, and must say so exactly once per process. The vendored
kernels need a CUDA device and are not exercised on CPU here — CPU tests
either stop before the vendored functions or replace them at the module seam
(`vendor.sla_attn.get_block_map` / `.block_sparse_attention`). The block-map
pinning arithmetic and end-to-end parity against dense attention need a real
GPU and are gated behind `requires_gpu`.
"""

from __future__ import annotations

import logging

import pytest
import torch

import vendor.sla_attn as sla_attn_vendor
from src.platform.runtime.native import sla_attn as sla_attn_module
from src.platform.runtime.native.sla_attn import (
    SlaAttnContext,
    build_sla_attn_context,
    estimate_transient_gb,
    reset_sla_attn_state,
    sla_attention,
    sla_attn_disabled_reason,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_sla_attn_state()
    yield
    reset_sla_attn_state()


def _qkv(tokens: int = 8192, heads: int = 2, head_dim: int = 128):
    shape = (1, tokens, heads, head_dim)
    return torch.zeros(shape), torch.zeros(shape), torch.zeros(shape)


class _Recorder:
    """Stands in for the vendored `get_block_map`/`block_sparse_attention`
    pair and records how each was called."""

    def __init__(self, result=None, raises: Exception | None = None):
        self.calls: list[dict] = []
        self._result = result
        self._raises = raises

    def get_block_map(self, q, k, topk_ratio, BLKQ, BLKK, protect_upto=0):
        self.calls.append({
            "stage": "get_block_map", "topk_ratio": topk_ratio,
            "BLKQ": BLKQ, "BLKK": BLKK, "protect_upto": protect_upto,
            "shape": tuple(q.shape),
        })
        if self._raises is not None:
            raise self._raises
        return torch.zeros(q.shape[0], q.shape[2], 1, 1, dtype=torch.int32), 1

    def block_sparse_attention(self, q, k, v, lut, topk, BLOCK_M, BLOCK_N, qk_scale=None):
        self.calls.append({
            "stage": "block_sparse_attention", "topk": topk,
            "BLOCK_M": BLOCK_M, "BLOCK_N": BLOCK_N,
        })
        if self._raises is not None:
            raise self._raises
        return self._result if self._result is not None else torch.zeros_like(q)


def _install(monkeypatch, recorder: _Recorder) -> None:
    """Replace the vendored functions AND the machine check, so a CPU-only
    test can reach the call path the GPU would."""
    monkeypatch.setattr(sla_attn_module, "_unsupported", lambda q: None)
    monkeypatch.setattr(sla_attn_vendor, "get_block_map", recorder.get_block_map)
    monkeypatch.setattr(sla_attn_vendor, "block_sparse_attention", recorder.block_sparse_attention)


# --- the paths that never reach a backend ----------------------------------

def test_no_context_returns_none_and_loads_nothing(monkeypatch):
    monkeypatch.setattr(sla_attn_vendor, "get_block_map",
                         lambda *a, **k: pytest.fail("backend was loaded"))
    q, k, v = _qkv()
    assert sla_attention(q, k, v, None) is None
    assert sla_attn_disabled_reason() is None


def test_dense_step_returns_none_without_calling_the_backend(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    assert sla_attention(q, k, v, SlaAttnContext(dense=True)) is None
    assert recorder.calls == []
    assert sla_attn_disabled_reason() is None


def test_short_sequence_is_skipped_without_disabling(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv(tokens=4096)
    assert sla_attention(q, k, v, SlaAttnContext()) is None
    assert recorder.calls == []
    # A short sequence is a property of the call, not the machine: a later
    # long one must still be allowed through.
    assert sla_attn_disabled_reason() is None
    long_q, long_k, long_v = _qkv(tokens=8192)
    assert sla_attention(long_q, long_k, long_v, SlaAttnContext()) is not None


def test_zero_sparsity_returns_none_without_calling_the_backend(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    assert sla_attention(q, k, v, SlaAttnContext(sparsity=0.0)) is None
    assert recorder.calls == []
    assert sla_attn_disabled_reason() is None


# --- graceful degradation ---------------------------------------------------

def test_cpu_tensors_disable_with_one_warning(caplog):
    q, k, v = _qkv()
    with caplog.at_level(logging.WARNING, logger=sla_attn_module.__name__):
        assert sla_attention(q, k, v, SlaAttnContext()) is None
        assert sla_attention(q, k, v, SlaAttnContext()) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "CUDA" in warnings[0].getMessage()
    assert "CUDA" in (sla_attn_disabled_reason() or "")


class _FakeQ:
    """A stand-in for a query tensor claiming to be on CUDA.

    `torch.Tensor.device` is read-only, so on a CPU-only box the device check
    fires first and the dtype/head-dim/capability reasons below it are
    unreachable with a real tensor.
    """

    def __init__(self, dtype=torch.bfloat16, head_dim=128):
        self.device = type("_Device", (), {"type": "cuda"})()
        self.dtype = dtype
        self.shape = (1, 8192, 2, head_dim)


@pytest.mark.parametrize(
    "kwargs, expected, capability",
    [
        ({"dtype": torch.float32}, "bfloat16 or float16", (12, 0)),
        ({"head_dim": 64}, "head_dim", (12, 0)),
        ({}, "compute capability", (7, 5)),
    ],
)
def test_unsupported_hardware_names_its_reason(monkeypatch, kwargs, expected, capability):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: capability)
    reason = sla_attn_module._unsupported(_FakeQ(**kwargs))
    assert reason is not None and expected in reason


def test_a_supported_machine_has_no_reason(monkeypatch):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (12, 0))
    assert sla_attn_module._unsupported(_FakeQ()) is None


def test_backend_failure_disables_with_one_warning(monkeypatch, caplog):
    recorder = _Recorder(raises=ImportError("no module named 'triton'"))
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    with caplog.at_level(logging.WARNING, logger=sla_attn_module.__name__):
        assert sla_attention(q, k, v, SlaAttnContext()) is None
        assert sla_attention(q, k, v, SlaAttnContext()) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ImportError" in warnings[0].getMessage()
    # The failing call happens once and is never retried.
    assert len(recorder.calls) == 1


# --- the working path -------------------------------------------------------

def test_backend_output_is_returned_and_knobs_are_forwarded(monkeypatch):
    marker = torch.full((1, 8192, 2, 128), 7.0)
    recorder = _Recorder(result=marker)
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    out = sla_attention(q, k, v, SlaAttnContext(sparsity=0.90, block_size=64, prefix_tokens=256))
    assert out is marker
    assert len(recorder.calls) == 2
    routing_call = recorder.calls[0]
    assert routing_call["stage"] == "get_block_map"
    assert routing_call["topk_ratio"] == pytest.approx(0.10)
    assert (routing_call["BLKQ"], routing_call["BLKK"]) == (64, 64)
    assert routing_call["protect_upto"] == 256
    attn_call = recorder.calls[1]
    assert attn_call["stage"] == "block_sparse_attention"
    assert (attn_call["BLOCK_M"], attn_call["BLOCK_N"]) == (64, 64)


def test_block_size_128_forces_key_block_64(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    sla_attention(q, k, v, SlaAttnContext(block_size=128))
    routing_call = recorder.calls[0]
    assert (routing_call["BLKQ"], routing_call["BLKK"]) == (128, 64)


def test_unsupported_block_size_falls_back_to_64(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    sla_attention(q, k, v, SlaAttnContext(block_size=999))
    routing_call = recorder.calls[0]
    assert (routing_call["BLKQ"], routing_call["BLKK"]) == (64, 64)


def test_sparsity_above_the_cap_is_clamped(monkeypatch):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    sla_attention(q, k, v, SlaAttnContext(sparsity=0.999))
    # 1.0 - 0.95 (the cap), not 1.0 - 0.999.
    assert recorder.calls[0]["topk_ratio"] == pytest.approx(0.05)


@pytest.mark.parametrize("prefix", [0, 8192, 9000])
def test_a_prefix_that_is_not_a_proper_prefix_is_dropped(monkeypatch, prefix):
    """A pin covering the whole sequence is dense attention plus routing
    overhead, so it is not passed through as one."""
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv(tokens=8192)
    sla_attention(q, k, v, SlaAttnContext(prefix_tokens=prefix))
    assert recorder.calls[0]["protect_upto"] == 0


def test_active_is_logged_once_across_calls(monkeypatch, caplog):
    recorder = _Recorder()
    _install(monkeypatch, recorder)
    q, k, v = _qkv()
    with caplog.at_level(logging.INFO, logger=sla_attn_module.__name__):
        for _ in range(3):
            sla_attention(q, k, v, SlaAttnContext())
    active_lines = [r for r in caplog.records if "[SLA-ATTN] active" in r.getMessage()]
    assert len(active_lines) == 1
    assert len(recorder.calls) == 6  # 2 vendored calls per invocation, 3 invocations


def test_reset_clears_the_disable_latch():
    q, k, v = _qkv()
    sla_attention(q, k, v, SlaAttnContext())
    assert sla_attn_disabled_reason() is not None
    reset_sla_attn_state()
    assert sla_attn_disabled_reason() is None


# --- the preset-knob resolver ----------------------------------------------

def test_context_is_none_when_disabled():
    assert build_sla_attn_context(enabled=False, sparsity=0.9, block_size=64, prefix_tokens=0) is None


def test_context_carries_the_resolved_knobs():
    ctx = build_sla_attn_context(enabled=True, sparsity=0.85, block_size=128, prefix_tokens=512)
    assert (ctx.sparsity, ctx.block_size, ctx.prefix_tokens, ctx.dense) == (0.85, 128, 512, False)


def test_non_numeric_sparsity_falls_back_instead_of_failing(caplog):
    with caplog.at_level(logging.WARNING, logger=sla_attn_module.__name__):
        ctx = build_sla_attn_context(enabled=True, sparsity="fast", block_size=64, prefix_tokens=0)
    assert ctx.sparsity == 0.90
    assert any("sparsity" in r.getMessage() for r in caplog.records)


def test_bad_block_size_falls_back_to_64(caplog):
    with caplog.at_level(logging.WARNING, logger=sla_attn_module.__name__):
        ctx = build_sla_attn_context(enabled=True, sparsity=0.9, block_size=32, prefix_tokens=0)
    assert ctx.block_size == 64
    assert any("block_size" in r.getMessage() for r in caplog.records)


def test_non_numeric_block_size_falls_back_to_64(caplog):
    with caplog.at_level(logging.WARNING, logger=sla_attn_module.__name__):
        ctx = build_sla_attn_context(enabled=True, sparsity=0.9, block_size="huge", prefix_tokens=0)
    assert ctx.block_size == 64


# --- the transient-VRAM estimate --------------------------------------------

_H3_HEADS = 56
_H3_HEAD_DIM = 128


def test_estimate_is_zero_below_the_sequence_length_that_would_route():
    """Agrees with `sla_attention`'s own short-sequence skip: nothing runs, so
    nothing is reserved."""
    assert estimate_transient_gb(8191, _H3_HEADS) == 0.0
    assert estimate_transient_gb(0, _H3_HEADS) == 0.0
    assert estimate_transient_gb(8192, _H3_HEADS) > 0.0


def test_estimate_is_monotonic_in_sequence_length():
    lengths = [8192, 12000, 20000, 43047, 65536]
    values = [estimate_transient_gb(n, _H3_HEADS) for n in lengths]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_estimate_is_monotonic_in_heads():
    values = [estimate_transient_gb(20000, h) for h in (1, 8, 28, 56)]
    assert values == sorted(values)


def test_estimate_is_well_below_sol_attns_at_the_same_sequence_length():
    """The whole point of this backend vs Sol-Attn: SLA's routing tensors are
    top-k-sized instead of the full block x block score/selection family
    Sol-Attn keeps live, so its reserve is a small fraction of Sol-Attn's."""
    from src.platform.runtime.native.sol_attn import estimate_transient_gb as sol_estimate
    seq_len = 43047
    sla_value = estimate_transient_gb(seq_len, _H3_HEADS)
    sol_value = sol_estimate(seq_len, _H3_HEADS)
    assert 0.0 < sla_value < sol_value / 2


def test_estimate_magnitude_at_the_observed_field_oom_sequence():
    """Pins the order of magnitude at the sequence length that caused Sol-Attn's
    field OOM (see its own test module). Too small under-reserves; wildly too
    large would strand the whole card for no reason."""
    assert 0.5 < estimate_transient_gb(43047, _H3_HEADS) < 1.5


def test_estimate_rounds_up_to_a_padded_sequence():
    """The kernel pads to a multiple of 64 (worst-case block size) and the
    copies are of the PADDED tensor, so a sequence one row over a boundary
    costs a whole extra block."""
    assert estimate_transient_gb(43008, _H3_HEADS) < estimate_transient_gb(43009, _H3_HEADS)
    assert estimate_transient_gb(43009, _H3_HEADS) == estimate_transient_gb(43072, _H3_HEADS)


# --- GPU-gated: block-map pinning arithmetic, parity, smoke -----------------

@pytest.mark.requires_gpu
def test_get_block_map_pins_prefix_blocks_and_widens_topk():
    """`protect_upto` must both rank the pinned blocks above everything else
    (so they always survive top-k) AND widen `topk` by the pinned count (so
    they do not evict blocks top-k would otherwise have kept)."""
    from vendor.sla_attn import get_block_map

    torch.manual_seed(0)
    S, BLK = 2048, 64  # 32 blocks
    q = torch.randn(1, S, 2, 128, device="cuda").contiguous()
    k = torch.randn(1, S, 2, 128, device="cuda").contiguous()
    prefix = 256  # 4 blocks
    topk_ratio = 0.10

    lut, topk = get_block_map(q, k, topk_ratio, BLK, BLK, protect_upto=prefix)

    n_blocks = S // BLK
    n_pinned = prefix // BLK
    base_topk = max(1, int(topk_ratio * n_blocks))
    assert topk == min(n_blocks, base_topk + n_pinned)

    pinned_ids = set(range(n_pinned))
    rows = lut.reshape(-1, topk).tolist()
    assert all(pinned_ids.issubset(set(row)) for row in rows)


@pytest.mark.requires_gpu
def test_sla_attention_matches_dense_when_every_block_is_kept():
    """A prefix pin wide enough to cover every block makes SLA's block-sparse
    kernel attend everywhere top-k does not already -- mathematically dense
    attention, just computed through the sparse kernel path."""
    torch.manual_seed(0)
    S, H, D, BLK = 8192, 4, 128, 64
    q = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)
    k = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)
    v = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)

    n_blocks = S // BLK
    # sparsity=0.90 -> base_topk = max(1, int(0.10 * n_blocks)); pin enough of
    # the sequence that topk widens all the way to n_blocks (kept-all).
    prefix_blocks_needed = n_blocks - max(1, int(0.10 * n_blocks))
    ctx = SlaAttnContext(sparsity=0.90, block_size=BLK, prefix_tokens=prefix_blocks_needed * BLK)

    out = sla_attention(q, k, v, ctx)
    assert out is not None
    assert sla_attn_disabled_reason() is None

    expected = torch.nn.functional.scaled_dot_product_attention(
        q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2),
    ).transpose(1, 2)
    torch.testing.assert_close(out, expected, atol=2e-2, rtol=2e-2)


@pytest.mark.requires_gpu
def test_sla_attention_smoke_at_default_sparsity():
    torch.manual_seed(0)
    S, H, D = 8192, 4, 128
    q = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)
    k = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)
    v = torch.randn(1, S, H, D, device="cuda", dtype=torch.bfloat16)

    out = sla_attention(q, k, v, SlaAttnContext(sparsity=0.90))
    assert out is not None
    assert out.shape == q.shape
    assert torch.isfinite(out).all()
    assert sla_attn_disabled_reason() is None
