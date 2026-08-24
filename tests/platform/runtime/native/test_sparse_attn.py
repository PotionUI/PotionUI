"""Tests for the sparse-attention dispatcher
(`src/platform/runtime/native/sparse_attn.py`).

The dispatcher owns exactly one decision -- which backend a context belongs
to -- so these tests never touch either backend's real kernel; they replace
`sol_attention`/`sla_attention` at the module seam and assert on which one was
called with what.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native import sparse_attn as sparse_attn_module
from src.platform.runtime.native.sla_attn import SlaAttnContext
from src.platform.runtime.native.sol_attn import SolAttnContext
from src.platform.runtime.native.sparse_attn import sparse_attention


def _qkv():
    shape = (1, 8, 2, 128)
    return torch.zeros(shape), torch.zeros(shape), torch.zeros(shape)


def test_none_context_returns_none_and_calls_nothing(monkeypatch):
    monkeypatch.setattr(sparse_attn_module, "sol_attention", lambda *a, **k: pytest.fail("sol called"))
    monkeypatch.setattr(sparse_attn_module, "sla_attention", lambda *a, **k: pytest.fail("sla called"))
    q, k, v = _qkv()
    assert sparse_attention(q, k, v, None) is None


def test_sol_attn_context_routes_to_sol_attention(monkeypatch):
    calls = []
    monkeypatch.setattr(sparse_attn_module, "sol_attention",
                         lambda q, k, v, ctx: calls.append(("sol", ctx)) or "sol-out")
    monkeypatch.setattr(sparse_attn_module, "sla_attention",
                         lambda *a, **k: pytest.fail("sla called for a SolAttnContext"))
    q, k, v = _qkv()
    ctx = SolAttnContext(tau=1.2)
    assert sparse_attention(q, k, v, ctx) == "sol-out"
    assert calls == [("sol", ctx)]


def test_sla_attn_context_routes_to_sla_attention(monkeypatch):
    calls = []
    monkeypatch.setattr(sparse_attn_module, "sla_attention",
                         lambda q, k, v, ctx: calls.append(("sla", ctx)) or "sla-out")
    monkeypatch.setattr(sparse_attn_module, "sol_attention",
                         lambda *a, **k: pytest.fail("sol called for a SlaAttnContext"))
    q, k, v = _qkv()
    ctx = SlaAttnContext(sparsity=0.85)
    assert sparse_attention(q, k, v, ctx) == "sla-out"
    assert calls == [("sla", ctx)]


def test_backend_none_propagates(monkeypatch):
    monkeypatch.setattr(sparse_attn_module, "sol_attention", lambda *a, **k: None)
    q, k, v = _qkv()
    assert sparse_attention(q, k, v, SolAttnContext()) is None


def test_unknown_context_type_raises_type_error():
    q, k, v = _qkv()
    with pytest.raises(TypeError):
        sparse_attention(q, k, v, object())
