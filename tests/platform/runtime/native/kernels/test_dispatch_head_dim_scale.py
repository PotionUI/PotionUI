from __future__ import annotations

import logging

import pytest
import torch

import src.platform.runtime.native.kernels.dispatch as att
from src.platform.runtime.native.kernels import FLASH, SAGE, SAGE2, SAGE3, SDPA, SPARGE, attention
from tests.platform.runtime.native.kernels.test_dispatch_contract import (
    HEADS,
    LENGTH,
    _clean_state,
    _cuda_like,
    install_all,
    real_adapter_calls,
)

SCALE_KEYWORD = {FLASH: "softmax_scale", SAGE: "sm_scale", SAGE2: "sm_scale", SPARGE: "scale", SDPA: "scale"}
HEAD_DIM_LIMIT = {FLASH: 256, SAGE: 128, SAGE2: 128, SAGE3: 128}


def _qkv(head_dim, gqa=False):
    generator = torch.Generator().manual_seed(0)
    kv_heads = HEADS // 2 if gqa else HEADS
    q = torch.randn(1, HEADS, LENGTH, head_dim, generator=generator).half()
    k = torch.randn(1, kv_heads, LENGTH, head_dim, generator=generator).half()
    v = torch.randn(1, kv_heads, LENGTH, head_dim, generator=generator).half()
    return _cuda_like(q), k, v


def _served_by_sdpa(call):
    return "attn_mask" in call[3]


def _fallbacks(caplog, reason):
    return [
        record.getMessage()
        for record in caplog.records
        if "falls back to sdpa" in record.getMessage() and f"({reason}-constrained" in record.getMessage()
    ]


@pytest.mark.parametrize("backend", sorted(HEAD_DIM_LIMIT))
def test_a_head_dim_at_the_backend_limit_runs_the_accelerated_kernel(real_adapter_calls, backend):
    q, k, v = _qkv(HEAD_DIM_LIMIT[backend])

    attention(q, k, v, backend=backend)

    assert len(real_adapter_calls) == 1
    assert not _served_by_sdpa(real_adapter_calls[0])


@pytest.mark.parametrize("backend", sorted(HEAD_DIM_LIMIT))
@pytest.mark.parametrize("gqa", [False, True], ids=["mha", "gqa"])
def test_a_head_dim_over_the_backend_limit_falls_back_to_sdpa_and_is_logged_once(
    real_adapter_calls, caplog, backend, gqa
):
    q, k, v = _qkv(HEAD_DIM_LIMIT[backend] + 64, gqa=gqa)

    with caplog.at_level(logging.INFO, logger=att.__name__):
        attention(q, k, v, backend=backend)
        attention(q, k, v, backend=backend)

    served = [call for call in real_adapter_calls if call[0].shape[-1] == q.shape[-1]]
    assert len(served) == 2
    assert all(_served_by_sdpa(call) for call in served)
    assert len(_fallbacks(caplog, "head_dim")) == 1


@pytest.mark.parametrize("backend", sorted(SCALE_KEYWORD))
def test_scale_reaches_the_adapter_under_the_kernels_own_keyword(real_adapter_calls, backend):
    q, k, v = _qkv(64)

    attention(q, k, v, backend=backend, scale=0.25)

    assert len(real_adapter_calls) == 1
    assert real_adapter_calls[0][3][SCALE_KEYWORD[backend]] == 0.25


@pytest.mark.parametrize("backend", sorted(SCALE_KEYWORD))
def test_no_scale_adds_no_scale_keyword(real_adapter_calls, backend):
    q, k, v = _qkv(64)

    attention(q, k, v, backend=backend)

    assert SCALE_KEYWORD[backend] not in real_adapter_calls[0][3]


@pytest.mark.parametrize("backend", [SDPA, FLASH, SAGE, SAGE2, SPARGE])
def test_scale_reaches_the_grouped_path(real_adapter_calls, backend):
    q, k, v = _qkv(64, gqa=True)

    attention(q, k, v, backend=backend, scale=0.25)

    assert real_adapter_calls[-1][3][SCALE_KEYWORD[backend]] == 0.25


def test_a_backend_without_a_scale_argument_falls_back_to_sdpa_with_the_scale(real_adapter_calls, caplog):
    q, k, v = _qkv(64)

    with caplog.at_level(logging.INFO, logger=att.__name__):
        attention(q, k, v, backend=SAGE3, scale=0.25)
        attention(q, k, v, backend=SAGE3, scale=0.25)

    assert len(real_adapter_calls) == 2
    assert all(_served_by_sdpa(call) and call[3]["scale"] == 0.25 for call in real_adapter_calls)
    assert len(_fallbacks(caplog, "scale")) == 1


def test_sdpa_with_a_scale_matches_the_manual_softmax():
    generator = torch.Generator().manual_seed(2)
    q, k, v = (torch.randn(1, 2, 8, 16, generator=generator) for _ in range(3))

    out = attention(q, k, v, backend=SDPA, scale=1.0)

    expected = torch.softmax(q @ k.transpose(-1, -2), dim=-1) @ v
    torch.testing.assert_close(out, expected, atol=1e-5, rtol=1e-5)


@pytest.mark.parametrize("backend", sorted(HEAD_DIM_LIMIT))
def test_the_head_dim_gate_reads_the_query_head_dim_only(real_adapter_calls, backend):
    limit = HEAD_DIM_LIMIT[backend]
    q, k, _ = _qkv(64)
    v = _qkv(limit + 64)[2]

    attention(q, k, v, backend=backend)

    assert len(real_adapter_calls) == 1
    assert not _served_by_sdpa(real_adapter_calls[0])

    real_adapter_calls.clear()
    q, k, _ = _qkv(limit + 64)
    v = _qkv(64)[2]

    attention(q, k, v, backend=backend)

    assert len(real_adapter_calls) == 1
    assert _served_by_sdpa(real_adapter_calls[0])


@pytest.mark.parametrize("backend", [SAGE, SAGE2])
def test_sage_scale_survives_the_value_rescale_path(real_adapter_calls, backend):
    q, k, v = _qkv(64)
    v = v * 2000

    attention(q, k, v, backend=backend, scale=0.25)

    assert [call[3]["sm_scale"] for call in real_adapter_calls] == [0.25]
