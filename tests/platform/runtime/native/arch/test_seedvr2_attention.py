"""Tests for SeedVR2's varlen attention: flash-varlen fast path + sdpa fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import torch

import vendor.seedvr2.attention as sv_att
from src.platform.runtime.native.attention import attention as _dispatch_attention


@pytest.fixture(autouse=True)
def _reset_cache():
    # The fallback path needs a wired backend (normally done by
    # arch/seedvr2/model.py at import time) — wire the real dispatcher
    # directly so this file can test the vendor module in isolation.
    sv_att.set_attention_backend(_dispatch_attention)
    sv_att.reset_flash_varlen_cache()
    yield
    sv_att.reset_flash_varlen_cache()


def _cu(lens):
    return torch.tensor([0] + list(torch.tensor(lens).cumsum(0).tolist()), dtype=torch.int32)


# --------------------------------------------------------------------------- #
# probe: degrades gracefully when flash_attn is absent
# --------------------------------------------------------------------------- #

def test_probe_returns_none_when_flash_attn_not_installed(monkeypatch):
    # flash_attn is not a project dependency, so the real import should fail
    # in this dev venv; the probe must not raise and must report unavailable.
    assert sv_att._probe_flash_varlen() is None
    q = torch.randn(6, 2, 4, dtype=torch.float16)
    assert sv_att._flash_varlen_available(q) is False


def test_fallback_path_used_when_flash_unavailable():
    torch.manual_seed(0)
    q = torch.randn(9, 2, 4, dtype=torch.float64)
    k = torch.randn(9, 2, 4, dtype=torch.float64)
    v = torch.randn(9, 2, 4, dtype=torch.float64)
    cu = _cu([3, 4, 2]).long()

    out = sv_att.varlen_attention(q, k, v, cu, cu)
    assert out.shape == (9, 2, 4)

    # Manually replicate the per-block sdpa split to check correctness.
    expected = []
    for lo, hi in zip(cu[:-1].tolist(), cu[1:].tolist()):
        qi, ki, vi = q[lo:hi], k[lo:hi], v[lo:hi]
        qi_ = qi.transpose(0, 1).unsqueeze(0)
        ki_ = ki.transpose(0, 1).unsqueeze(0)
        vi_ = vi.transpose(0, 1).unsqueeze(0)
        oi = torch.nn.functional.scaled_dot_product_attention(qi_, ki_, vi_)
        expected.append(oi.squeeze(0).transpose(0, 1))
    expected = torch.cat(expected, dim=0)
    assert torch.allclose(out, expected, atol=1e-10)


# --------------------------------------------------------------------------- #
# fast path: mocked flash_attn_varlen_func, precise shape/dtype assertions
# --------------------------------------------------------------------------- #

def test_flash_varlen_fast_path_called_with_correct_layout(monkeypatch):
    total_q, total_k, heads, head_dim = 9, 9, 2, 4
    lens = [3, 4, 2]
    cu = _cu(lens)  # already int32, cpu

    q = torch.randn(total_q, heads, head_dim, dtype=torch.float16)
    k = torch.randn(total_k, heads, head_dim, dtype=torch.float16)
    v = torch.randn(total_k, heads, head_dim, dtype=torch.float16)

    fake_out = torch.zeros_like(q)
    mock_flash = MagicMock(return_value=fake_out)

    # Pretend we're on CUDA without needing a real GPU: patch is_cuda via a
    # tensor subclass shim is overkill — instead patch the availability probe
    # directly to isolate call-shape assertions from device detection.
    monkeypatch.setattr(sv_att, "_probe_flash_varlen", lambda: mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_available", lambda t: True)

    out = sv_att.varlen_attention(q, k, v, cu, cu)

    assert out is fake_out
    mock_flash.assert_called_once()
    args, kwargs = mock_flash.call_args
    called_q, called_k, called_v = args[0], args[1], args[2]

    # Packed (total, H, D) layout passed through unchanged, no transpose.
    assert called_q.shape == (total_q, heads, head_dim)
    assert called_k.shape == (total_k, heads, head_dim)
    assert called_v.shape == (total_k, heads, head_dim)
    assert torch.equal(called_q, q)
    assert torch.equal(called_k, k)
    assert torch.equal(called_v, v)

    # cu_seqlens must be int32 and device-matched to q.
    assert kwargs["cu_seqlens_q"].dtype == torch.int32
    assert kwargs["cu_seqlens_k"].dtype == torch.int32
    assert kwargs["cu_seqlens_q"].device == q.device
    assert kwargs["cu_seqlens_k"].device == q.device
    assert torch.equal(kwargs["cu_seqlens_q"], torch.tensor([0, 3, 7, 9], dtype=torch.int32))

    # max_seqlen is a plain python int, not a tensor, and matches the largest block.
    assert kwargs["max_seqlen_q"] == max(lens)
    assert isinstance(kwargs["max_seqlen_q"], int)
    assert kwargs["max_seqlen_k"] == max(lens)

    assert kwargs["causal"] is False


def test_flash_varlen_not_used_for_fp32_even_if_installed(monkeypatch):
    mock_flash = MagicMock()
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_probed", True)

    q = torch.randn(5, 2, 4, dtype=torch.float32)
    assert sv_att._flash_varlen_available(q) is False
    mock_flash.assert_not_called()


def test_flash_varlen_not_used_off_cuda_even_if_installed(monkeypatch):
    mock_flash = MagicMock()
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_probed", True)

    q = torch.randn(5, 2, 4, dtype=torch.float16)  # cpu tensor
    assert sv_att._flash_varlen_available(q) is False


# --------------------------------------------------------------------------- #
# runtime failure -> fallback (roadmap S18/#18): import-clean but the kernel
# call itself raises (unsupported capability, head_dim past this build's
# limit, etc.) — must fall back, never crash the generation.
# --------------------------------------------------------------------------- #

def test_flash_varlen_runtime_failure_falls_back_and_warns_once(monkeypatch, caplog):
    mock_flash = MagicMock(side_effect=RuntimeError("simulated unsupported head_dim"))
    monkeypatch.setattr(sv_att, "_probe_flash_varlen", lambda: mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_available", lambda t: True)

    torch.manual_seed(0)
    q = torch.randn(9, 2, 4, dtype=torch.float64)
    k = torch.randn(9, 2, 4, dtype=torch.float64)
    v = torch.randn(9, 2, 4, dtype=torch.float64)
    cu = _cu([3, 4, 2]).long()

    import logging

    with caplog.at_level(logging.WARNING):
        out = sv_att.varlen_attention(q, k, v, cu, cu)

    # The real kernel raised, but the fallback still produced real output —
    # matches the correctness of the plain fallback path (test above).
    assert out.shape == (9, 2, 4)
    mock_flash.assert_called_once()
    assert any("falling back" in r.message for r in caplog.records)


def test_flash_varlen_broken_flag_prevents_further_attempts(monkeypatch):
    """Once a runtime failure is observed, subsequent calls must not retry
    the already-proven-broken kernel at all (not just fall back per-call)."""
    mock_flash = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(sv_att, "_probe_flash_varlen", lambda: mock_flash)
    monkeypatch.setattr(sv_att, "_flash_varlen_func", mock_flash)
    # A stub that mirrors the real function's contract: available unless the
    # module has already recorded a runtime failure.
    monkeypatch.setattr(sv_att, "_flash_varlen_available", lambda t: not sv_att._flash_varlen_broken)

    torch.manual_seed(0)
    q = torch.randn(6, 2, 4, dtype=torch.float64)
    k = torch.randn(6, 2, 4, dtype=torch.float64)
    v = torch.randn(6, 2, 4, dtype=torch.float64)
    cu = _cu([3, 3]).long()

    sv_att.varlen_attention(q, k, v, cu, cu)
    assert mock_flash.call_count == 1
    assert sv_att._flash_varlen_broken is True

    sv_att.varlen_attention(q, k, v, cu, cu)
    assert mock_flash.call_count == 1  # not retried on the second call
