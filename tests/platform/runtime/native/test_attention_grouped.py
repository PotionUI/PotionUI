"""The shared grouped-query (GQA/MQA) attention seam.

``grouped_attention`` takes ``k``/``v`` with fewer heads than ``q`` and either
hands them to the backend as-is (when that backend consumes grouped K/V) or
expands them with ``repeat_interleave`` first. The two layouts must compute the
same attention; on ``sdpa`` they are bit-identical, which is what these tests
pin, along with the capability decision itself (probed once per backend, and
false for anything but ``sdpa``).
"""

from __future__ import annotations

import pytest
import torch
import torch.nn.functional as F

import src.platform.runtime.native.attention as att


@pytest.fixture(autouse=True)
def _clean_backend_state(monkeypatch):
    monkeypatch.delenv(att.ENV_VAR, raising=False)
    att.set_backend_override(None)
    att.reset_backend_cache()
    yield
    att.set_backend_override(None)
    att.reset_backend_cache()


def _qkv(heads=8, kvheads=2, q_len=6, kv_len=6, dim=8, seed=0, dtype=torch.float32):
    g = torch.Generator().manual_seed(seed)
    q = torch.randn(2, heads, q_len, dim, generator=g, dtype=torch.float32).to(dtype)
    k = torch.randn(2, kvheads, kv_len, dim, generator=g, dtype=torch.float32).to(dtype)
    v = torch.randn(2, kvheads, kv_len, dim, generator=g, dtype=torch.float32).to(dtype)
    return q, k, v


def _repeat_reference(q, k, v, mask=None):
    """The layout every caller used before this seam: expand, then dispatch."""
    repeat = q.shape[1] // k.shape[1]
    return att.attention(q, k.repeat_interleave(repeat, dim=1),
                         v.repeat_interleave(repeat, dim=1), mask=mask)


def _causal(rows: int, cols: int, dtype=torch.float32) -> torch.Tensor:
    return torch.full((rows, cols), float("-inf"), dtype=dtype).triu(cols - rows + 1)


# --- capability -------------------------------------------------------------

def test_sdpa_capability_matches_the_installed_torch():
    expected = "enable_gqa" in str(F.scaled_dot_product_attention.__doc__ or "")
    assert att.supports_grouped_kv(att.SDPA) is expected


def test_non_sdpa_backends_report_no_grouped_support():
    for backend in (att.FLASH, att.SAGE, att.SAGE2, att.SAGE3, att.SPARGE):
        assert att.supports_grouped_kv(backend) is False


def test_capability_is_computed_once_per_backend(monkeypatch):
    calls = []
    real = att._probe_grouped_sdpa
    monkeypatch.setattr(att, "_probe_grouped_sdpa", lambda: (calls.append(1), real())[1])

    first = att.supports_grouped_kv(att.SDPA)
    for _ in range(5):
        assert att.supports_grouped_kv(att.SDPA) is first
    assert len(calls) == 1

    att.reset_backend_cache()
    att.supports_grouped_kv(att.SDPA)
    assert len(calls) == 2


def test_probe_reports_false_when_torch_rejects_the_kwarg(monkeypatch):
    def old_sdpa(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None):
        return q

    monkeypatch.setattr(F, "scaled_dot_product_attention", old_sdpa)
    assert att._probe_grouped_sdpa() is False


# --- parity: grouped layout vs the repeat layout ----------------------------

@pytest.mark.parametrize("q_len", [1, 3, 6])
@pytest.mark.parametrize("masked", [False, True])
def test_grouped_matches_repeat_bit_for_bit(q_len, masked):
    """8 query heads over 2 kv heads, prefill (q_len == kv_len) and decode
    (q_len < kv_len), masked and unmasked."""
    if not att.supports_grouped_kv(att.SDPA):
        pytest.skip("installed torch SDPA has no enable_gqa; only the repeat layout exists")
    q, k, v = _qkv(heads=8, kvheads=2, q_len=q_len, kv_len=6)
    mask = _causal(q_len, 6) if masked else None

    grouped = att.grouped_attention(q, k, v, heads=8, kvheads=2, mask=mask)
    assert torch.equal(grouped, _repeat_reference(q, k, v, mask))


@pytest.mark.parametrize("pos", [0, 1, 4, 9])
def test_decode_positions_match_the_repeat_layout(pos):
    """Successive decode steps against a growing prefix — the shape the Music3
    LM's ``step`` produces."""
    q, _, _ = _qkv(heads=8, kvheads=2, q_len=1, kv_len=1, seed=pos)
    _, k, v = _qkv(heads=8, kvheads=2, q_len=1, kv_len=pos + 1, seed=100 + pos)

    grouped = att.grouped_attention(q, k, v, heads=8, kvheads=2)
    assert torch.allclose(grouped, _repeat_reference(q, k, v), atol=0, rtol=0)


def test_multi_query_single_kv_head_matches_repeat():
    q, k, v = _qkv(heads=8, kvheads=1)
    assert torch.equal(att.grouped_attention(q, k, v), _repeat_reference(q, k, v))


def test_equal_head_counts_take_the_plain_path():
    q, k, v = _qkv(heads=4, kvheads=4)
    assert torch.equal(att.grouped_attention(q, k, v, heads=4, kvheads=4),
                       att.attention(q, k, v, heads=4))


@pytest.mark.parametrize("masked", [False, True])
def test_repeat_fallback_computes_the_same_attention(monkeypatch, masked):
    """The fallback layout is still a real path — on a backend that reports no
    grouped support it must produce what the grouped layout produces."""
    if not att.supports_grouped_kv(att.SDPA):
        pytest.skip("installed torch SDPA has no enable_gqa; only the repeat layout exists")
    q, k, v = _qkv(heads=8, kvheads=2, q_len=3, kv_len=6)
    mask = _causal(3, 6) if masked else None
    grouped = att.grouped_attention(q, k, v, mask=mask)

    monkeypatch.setattr(att, "supports_grouped_kv", lambda backend: False)
    assert torch.equal(att.grouped_attention(q, k, v, mask=mask), grouped)


# --- fallback selection -----------------------------------------------------

def test_unsupported_backend_gets_the_expanded_layout(monkeypatch):
    """A backend that reports no grouped support must never see grouped K/V."""
    seen = {}

    def fake_run(chosen, q, k, v, mask, grouped=False):
        seen.update(kv_heads=k.shape[1], v_heads=v.shape[1], grouped=grouped)
        return torch.zeros_like(q)

    monkeypatch.setattr(att, "_run", fake_run)
    monkeypatch.setattr(att, "supports_grouped_kv", lambda backend: False)

    q, k, v = _qkv(heads=8, kvheads=2)
    att.grouped_attention(q, k, v, heads=8, kvheads=2)
    assert seen == {"kv_heads": 8, "v_heads": 8, "grouped": False}


def test_supported_backend_gets_the_grouped_layout(monkeypatch):
    seen = {}

    def fake_run(chosen, q, k, v, mask, grouped=False):
        seen.update(kv_heads=k.shape[1], v_heads=v.shape[1], grouped=grouped)
        return torch.zeros_like(q)

    monkeypatch.setattr(att, "_run", fake_run)
    monkeypatch.setattr(att, "supports_grouped_kv", lambda backend: True)

    q, k, v = _qkv(heads=8, kvheads=2)
    att.grouped_attention(q, k, v, heads=8, kvheads=2)
    assert seen == {"kv_heads": 2, "v_heads": 2, "grouped": True}


def test_capability_is_asked_about_the_backend_actually_used(monkeypatch):
    """A sage/flash call that falls back to sdpa (mask, fp32, CPU) must be
    judged as sdpa, not as the backend that was selected and abandoned."""
    asked = []
    monkeypatch.setattr(att, "supports_grouped_kv", lambda backend: asked.append(backend) or False)
    monkeypatch.setattr(att, "_get_availability",
                        lambda device_index=None: {att.SDPA: True, att.SAGE: True})
    monkeypatch.setenv(att.ENV_VAR, att.SAGE)

    q, k, v = _qkv(heads=8, kvheads=2)  # fp32 on CPU: sage can take neither
    att.grouped_attention(q, k, v)
    assert asked == [att.SDPA]


# --- contract validation ----------------------------------------------------

def test_head_count_mismatch_raises():
    q, k, v = _qkv(heads=8, kvheads=2)
    with pytest.raises(ValueError, match="heads=4"):
        att.grouped_attention(q, k, v, heads=4, kvheads=2)
    with pytest.raises(ValueError, match="kvheads=4"):
        att.grouped_attention(q, k, v, heads=8, kvheads=4)


def test_non_divisible_head_counts_raise():
    q, _, _ = _qkv(heads=6, kvheads=6)
    _, k, v = _qkv(heads=6, kvheads=4)
    with pytest.raises(ValueError, match="not a multiple"):
        att.grouped_attention(q, k, v)


# --- allocation -------------------------------------------------------------

def test_grouped_layout_does_not_materialize_the_expanded_kv():
    """The point of the seam: no ``repeat_interleave`` copy of the prefix."""
    if not att.supports_grouped_kv(att.SDPA):
        pytest.skip("installed torch SDPA has no enable_gqa; only the repeat layout exists")
    q, k, v = _qkv(heads=8, kvheads=2, q_len=1, kv_len=512)
    seen = {}

    real_repeat = torch.Tensor.repeat_interleave

    def tracking_repeat(self, *args, **kwargs):
        seen["called"] = True
        return real_repeat(self, *args, **kwargs)

    torch.Tensor.repeat_interleave = tracking_repeat
    try:
        att.grouped_attention(q, k, v, heads=8, kvheads=2)
    finally:
        torch.Tensor.repeat_interleave = real_repeat
    assert "called" not in seen
