"""Tests for the opt-in Sol-Attn seam (`src/platform/runtime/native/sol_attn.py`).

The contract under test is the failure contract, not the kernel: Sol-Attn must
never raise, must fall back to "the caller does what it always did" for every
reason it cannot run, and must say so exactly once per process. The vendored
backends need a CUDA device and are not executed here — every test either stops
before the backend or replaces it at the loader seam.
"""

from __future__ import annotations

import logging

import pytest
import torch

from src.platform.runtime.native import sol_attn as sol_attn_module
from src.platform.runtime.native.sol_attn import (
    SolAttnContext,
    build_sol_attn_context,
    estimate_transient_gb,
    reset_sol_attn_state,
    sol_attention,
    sol_attn_disabled_reason,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_sol_attn_state()
    yield
    reset_sol_attn_state()


def _qkv(tokens: int = 512, heads: int = 2, head_dim: int = 128):
    shape = (1, tokens, heads, head_dim)
    return torch.zeros(shape), torch.zeros(shape), torch.zeros(shape)


class _Recorder:
    """Stands in for a vendored backend and records how it was called."""

    def __init__(self, result=None, raises: Exception | None = None):
        self.calls: list[dict] = []
        self._result = result
        self._raises = raises

    def __call__(self, q, k, v, *, tau, sink_tokens):
        self.calls.append({"tau": tau, "sink_tokens": sink_tokens, "shape": tuple(q.shape)})
        if self._raises is not None:
            raise self._raises
        return self._result if self._result is not None else torch.zeros_like(q)


def _install(monkeypatch, backend) -> None:
    """Replace the backend loader AND the machine check, so a CPU-only test can
    reach the call path the GPU would."""
    monkeypatch.setattr(sol_attn_module, "_load_backend", lambda: backend)
    monkeypatch.setattr(sol_attn_module, "_unsupported", lambda q: None)


# --- the paths that never reach a backend ----------------------------------

def test_no_context_returns_none_and_loads_nothing(monkeypatch):
    monkeypatch.setattr(sol_attn_module, "_load_backend", lambda: pytest.fail("backend was loaded"))
    q, k, v = _qkv()
    assert sol_attention(q, k, v, None) is None
    assert sol_attn_disabled_reason() is None


def test_dense_step_returns_none_without_calling_the_backend(monkeypatch):
    backend = _Recorder()
    _install(monkeypatch, backend)
    q, k, v = _qkv()
    assert sol_attention(q, k, v, SolAttnContext(dense=True)) is None
    assert backend.calls == []
    assert sol_attn_disabled_reason() is None


def test_short_sequence_is_skipped_without_disabling(monkeypatch):
    backend = _Recorder()
    _install(monkeypatch, backend)
    q, k, v = _qkv(tokens=128)
    assert sol_attention(q, k, v, SolAttnContext()) is None
    assert backend.calls == []
    # A short sequence is a property of the call, not the machine: a later long
    # one must still be allowed through.
    assert sol_attn_disabled_reason() is None
    long_q, long_k, long_v = _qkv(tokens=512)
    assert sol_attention(long_q, long_k, long_v, SolAttnContext()) is not None


# --- graceful degradation ---------------------------------------------------

def test_cpu_tensors_disable_with_one_warning(caplog):
    q, k, v = _qkv()
    with caplog.at_level(logging.WARNING, logger=sol_attn_module.__name__):
        assert sol_attention(q, k, v, SolAttnContext()) is None
        assert sol_attention(q, k, v, SolAttnContext()) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "CUDA" in warnings[0].getMessage()
    assert "CUDA" in (sol_attn_disabled_reason() or "")


class _FakeQ:
    """A stand-in for a query tensor claiming to be on CUDA.

    `torch.Tensor.device` is read-only, so on a CPU-only box the device check
    fires first and the dtype/head-dim/capability reasons below it are
    unreachable with a real tensor.
    """

    def __init__(self, dtype=torch.bfloat16, head_dim=128):
        self.device = type("_Device", (), {"type": "cuda"})()
        self.dtype = dtype
        self.shape = (1, 512, 2, head_dim)


@pytest.mark.parametrize(
    "kwargs, expected, capability",
    [
        ({"dtype": torch.float32}, "bfloat16", (12, 0)),
        ({"head_dim": 64}, "head_dim", (12, 0)),
        ({}, "compute capability", (7, 5)),
    ],
)
def test_unsupported_hardware_names_its_reason(monkeypatch, kwargs, expected, capability):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: capability)
    reason = sol_attn_module._unsupported(_FakeQ(**kwargs))
    assert reason is not None and expected in reason


def test_a_supported_machine_has_no_reason(monkeypatch):
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device: (12, 0))
    assert sol_attn_module._unsupported(_FakeQ()) is None


def test_backend_failure_disables_with_one_warning(monkeypatch, caplog):
    backend = _Recorder(raises=ImportError("no module named 'triton'"))
    _install(monkeypatch, backend)
    q, k, v = _qkv()
    with caplog.at_level(logging.WARNING, logger=sol_attn_module.__name__):
        assert sol_attention(q, k, v, SolAttnContext()) is None
        assert sol_attention(q, k, v, SolAttnContext()) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ImportError" in warnings[0].getMessage()
    # The failing backend is called once and never retried.
    assert len(backend.calls) == 1


def test_loader_failure_is_a_failure_not_a_crash(monkeypatch):
    def boom():
        raise RuntimeError("torch too old for flex_attention")

    monkeypatch.setattr(sol_attn_module, "_load_backend", boom)
    monkeypatch.setattr(sol_attn_module, "_unsupported", lambda q: None)
    q, k, v = _qkv()
    assert sol_attention(q, k, v, SolAttnContext()) is None
    assert "torch too old" in (sol_attn_disabled_reason() or "")


# --- the working path -------------------------------------------------------

def test_backend_output_is_returned_and_knobs_are_forwarded(monkeypatch):
    marker = torch.full((1, 512, 2, 128), 7.0)
    backend = _Recorder(result=marker)
    _install(monkeypatch, backend)
    q, k, v = _qkv()
    out = sol_attention(q, k, v, SolAttnContext(tau=1.4, sink_tokens=64))
    assert out is marker
    assert backend.calls == [{"tau": 1.4, "sink_tokens": 64, "shape": (1, 512, 2, 128)}]


@pytest.mark.parametrize("sink", [0, 512, 900])
def test_a_sink_that_is_not_a_proper_prefix_is_dropped(monkeypatch, sink):
    """A sink covering the whole sequence is dense attention plus routing
    overhead, so it is not passed through as one."""
    backend = _Recorder()
    _install(monkeypatch, backend)
    q, k, v = _qkv(tokens=512)
    sol_attention(q, k, v, SolAttnContext(sink_tokens=sink))
    assert backend.calls[0]["sink_tokens"] == 0


def test_backend_is_loaded_once_across_calls(monkeypatch):
    backend = _Recorder()
    loads = {"n": 0}

    def load():
        loads["n"] += 1
        return backend

    monkeypatch.setattr(sol_attn_module, "_load_backend", load)
    monkeypatch.setattr(sol_attn_module, "_unsupported", lambda q: None)
    q, k, v = _qkv()
    for _ in range(3):
        sol_attention(q, k, v, SolAttnContext())
    assert loads["n"] == 1
    assert len(backend.calls) == 3


def test_reset_clears_the_disable_latch():
    q, k, v = _qkv()
    sol_attention(q, k, v, SolAttnContext())
    assert sol_attn_disabled_reason() is not None
    reset_sol_attn_state()
    assert sol_attn_disabled_reason() is None


# --- backend selection ------------------------------------------------------

def test_unknown_backend_name_is_rejected(monkeypatch):
    monkeypatch.setenv("NATIVE_SOL_ATTN_BACKEND", "nope")
    with pytest.raises(ValueError, match="flex"):
        sol_attn_module._load_backend()


def test_default_backend_is_the_flex_one(monkeypatch):
    monkeypatch.delenv("NATIVE_SOL_ATTN_BACKEND", raising=False)
    backend = sol_attn_module._load_backend()
    assert backend.__module__ == "vendor.sol_attn.flex"


# --- the preset-knob resolver ----------------------------------------------

def test_context_is_none_when_disabled():
    assert build_sol_attn_context(enabled=False, tau=1.0, sink_tokens=100) is None


def test_context_carries_the_resolved_knobs():
    ctx = build_sol_attn_context(enabled=True, tau=1.3, sink_tokens=100)
    assert (ctx.tau, ctx.sink_tokens, ctx.dense) == (1.3, 100, False)


def test_non_numeric_tau_falls_back_instead_of_failing(caplog):
    with caplog.at_level(logging.WARNING, logger=sol_attn_module.__name__):
        ctx = build_sol_attn_context(enabled=True, tau="fast", sink_tokens=0)
    assert ctx.tau == 1.0
    assert any("tau" in r.getMessage() for r in caplog.records)


# --- the transient-VRAM estimate --------------------------------------------

_H3_HEADS = 56
_H3_HEAD_DIM = 128


def _one_qkv_copy_bytes(seq_len: int, heads: int = _H3_HEADS) -> int:
    padded = -(-seq_len // 128) * 128
    return padded * heads * _H3_HEAD_DIM * 2


def test_estimate_is_zero_below_the_sequence_length_that_would_route():
    """Agrees with `sol_attention`'s own short-sequence skip: nothing runs, so
    nothing is reserved."""
    assert estimate_transient_gb(255, _H3_HEADS) == 0.0
    assert estimate_transient_gb(0, _H3_HEADS) == 0.0
    assert estimate_transient_gb(256, _H3_HEADS) > 0.0


def test_estimate_is_monotonic_in_sequence_length():
    lengths = [256, 1024, 4096, 8192, 20000, 43047, 65536]
    values = [estimate_transient_gb(n, _H3_HEADS) for n in lengths]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_estimate_is_monotonic_in_heads():
    values = [estimate_transient_gb(20000, h) for h in (1, 8, 28, 56)]
    assert values == sorted(values)


def test_estimate_covers_the_qkv_copies_that_caused_the_field_oom():
    """The observed failure allocated 590 MiB with 151 MiB free at
    768x1344/141f (43047 rows, 56 heads) -- exactly ONE padded QKV copy. The
    reserve has to cover every such copy alive at peak, not one."""
    seq_len = 43047
    one_copy_mib = _one_qkv_copy_bytes(seq_len) / 1024 ** 2
    assert 589 < one_copy_mib < 591

    estimate = estimate_transient_gb(seq_len, _H3_HEADS)
    assert estimate >= 8 * one_copy_mib / 1024


@pytest.mark.parametrize("seq_len, low, high", [(20000, 2.0, 3.0), (43047, 4.8, 6.0)])
def test_estimate_magnitudes_are_sane(seq_len, low, high):
    """Pins the order of magnitude at the two sizes that matter. Too small
    reproduces the OOM; wildly too large would strand the whole card."""
    assert low < estimate_transient_gb(seq_len, _H3_HEADS) < high


def test_the_qkv_copies_dominate_not_the_routing():
    """`_build_routing` pools QUERIES into blocks too, so its tensors are
    quadratic in the BLOCK count, not the token count. Doubling the sequence
    must therefore roughly double the estimate, not quadruple it."""
    small = estimate_transient_gb(20000, _H3_HEADS)
    large = estimate_transient_gb(40000, _H3_HEADS)
    assert 1.9 < large / small < 2.3


def test_estimate_rounds_up_to_a_padded_sequence():
    """The kernel pads to a multiple of 128 and the copies are of the PADDED
    tensor, so a sequence one row over a boundary costs a whole extra block."""
    assert estimate_transient_gb(43008, _H3_HEADS) < estimate_transient_gb(43009, _H3_HEADS)
    assert estimate_transient_gb(43009, _H3_HEADS) == estimate_transient_gb(43136, _H3_HEADS)
