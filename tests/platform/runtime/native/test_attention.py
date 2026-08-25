"""Tests for the native attention backend dispatcher."""

from __future__ import annotations

import logging
import math

import pytest
import torch

import src.platform.runtime.native.attention as att


@pytest.fixture(autouse=True)
def _reset_cache():
    att.reset_backend_cache()
    att.set_backend_override(None)
    yield
    att.reset_backend_cache()
    att.set_backend_override(None)


def _configure(monkeypatch, *, cuda, cap, modules, sage_v2=True,
                cap_full=None, cuda_runtime=(0, 0)):
    """Simulate a hardware/import environment and re-probe.

    ``cap`` is the legacy major-only capability (``_cuda_capability_major``);
    ``cap_full``/``cuda_runtime`` additionally drive sage3's exact-capability +
    CUDA-runtime gate (``_cuda_capability``/``_cuda_runtime_version``).
    Defaults keep sage3 unavailable unless a test opts in.
    """
    # Accept (and ignore) an optional device_index positional arg — the real
    # functions take one now (per-device probing/caching), but these tests
    # simulate a single uniform environment regardless of device.
    monkeypatch.setattr(att, "_has_module", lambda name: name in modules)
    monkeypatch.setattr(att, "_cuda_capability_major", lambda device_index=None: cap)
    monkeypatch.setattr(
        att, "_cuda_capability",
        lambda device_index=None: cap_full if cap_full is not None else (cap, 0),
    )
    monkeypatch.setattr(att, "_cuda_runtime_version", lambda: cuda_runtime)
    monkeypatch.setattr(att, "_sageattention_is_v2", lambda: sage_v2)
    monkeypatch.setattr(att.torch.cuda, "is_available", lambda: cuda)
    monkeypatch.delenv(att.ENV_VAR, raising=False)
    att.reset_backend_cache()


# --------------------------------------------------------------------------- #
# availability matrix
# --------------------------------------------------------------------------- #

def test_sdpa_always_available_no_gpu(monkeypatch):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    assert att.available_backends() == ["sdpa"]


def test_flash_requires_module_and_cuda(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    assert att.available_backends() == ["flash", "sdpa"]

    # flash present but no CUDA -> unavailable
    _configure(monkeypatch, cuda=False, cap=0, modules={"flash_attn"})
    assert att.available_backends() == ["sdpa"]


def test_sage_needs_triton_cuda_and_capability(monkeypatch):
    # sm70, triton + sageattention v1 -> sage but not sage2
    _configure(monkeypatch, cuda=True, cap=7, modules={"sageattention", "triton"}, sage_v2=False)
    assert att.available_backends() == ["sage", "sdpa"]

    # sm80 + v2 -> sage2 and sage
    _configure(monkeypatch, cuda=True, cap=8, modules={"sageattention", "triton"}, sage_v2=True)
    assert att.available_backends() == ["sage2", "sage", "sdpa"]

    # sageattention without triton -> neither
    _configure(monkeypatch, cuda=True, cap=8, modules={"sageattention"}, sage_v2=True)
    assert att.available_backends() == ["sdpa"]

    # sage2 needs sm80: on sm70 with v2 build, only sage
    _configure(monkeypatch, cuda=True, cap=7, modules={"sageattention", "triton"}, sage_v2=True)
    assert att.available_backends() == ["sage", "sdpa"]


def test_full_stack_priority_order(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=9,
               modules={"sageattention", "triton", "flash_attn"}, sage_v2=True)
    assert att.available_backends() == ["sage2", "sage", "flash", "sdpa"]


# --------------------------------------------------------------------------- #
# sage3 (SageAttention3 / Blackwell FP4) — exact-capability + CUDA-runtime gate
# --------------------------------------------------------------------------- #

def test_sage3_selected_on_sm120_with_module_and_new_enough_cuda(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=12, cap_full=(12, 0), cuda_runtime=(12, 8),
               modules={"sageattn3"})
    assert att.available_backends() == ["sage3", "sdpa"]
    assert att.get_attention_backend() == "sage3"


@pytest.mark.parametrize("cap_full", [(10, 0), (12, 0), (12, 1)])
def test_sage3_available_on_every_supported_blackwell_capability(monkeypatch, cap_full):
    _configure(monkeypatch, cuda=True, cap=cap_full[0], cap_full=cap_full, cuda_runtime=(12, 8),
               modules={"sageattn3"})
    assert "sage3" in att.available_backends()


@pytest.mark.parametrize("cap_full", [(9, 0), (11, 0), (12, 2), (13, 0)])
def test_sage3_unavailable_on_non_blackwell_or_mismatched_capability(monkeypatch, cap_full):
    # sage3's kernel is built with family-specific (`...a`-suffixed) arch flags
    # for exactly (10,0)/(12,0)/(12,1) — this must be an EXACT-membership
    # check, not `>= (12, 0)`: sm121 isn't in range for sm120's build and vice
    # versa, and a hypothetical sm130 needs its own rebuild.
    _configure(monkeypatch, cuda=True, cap=cap_full[0], cap_full=cap_full, cuda_runtime=(12, 8),
               modules={"sageattn3"})
    assert "sage3" not in att.available_backends()


def test_sage3_unavailable_without_the_sageattn3_module(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=12, cap_full=(12, 0), cuda_runtime=(12, 8), modules=set())
    assert "sage3" not in att.available_backends()


def test_sage3_unavailable_on_old_cuda_runtime(monkeypatch):
    # sage3 hard-requires CUDA >= 12.8 even on a supported GPU.
    _configure(monkeypatch, cuda=True, cap=12, cap_full=(12, 0), cuda_runtime=(12, 6),
               modules={"sageattn3"})
    assert "sage3" not in att.available_backends()


def test_sage3_outranks_sage2_in_priority(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=12, cap_full=(12, 0), cuda_runtime=(12, 8),
               modules={"sageattn3", "sageattention", "triton", "flash_attn"}, sage_v2=True)
    assert att.available_backends() == ["sage3", "sage2", "sage", "flash", "sdpa"]
    assert att.get_attention_backend() == "sage3"


def test_sage3_override_unavailable_warns_and_falls_back(monkeypatch, caplog):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())  # only sdpa
    with caplog.at_level(logging.WARNING):
        chosen = att.get_attention_backend("sage3")
    assert chosen == "sdpa"
    assert any("unavailable" in r.message and "sage3" in r.message for r in caplog.records)


def test_sage3_active_still_falls_back_to_sdpa_for_mask_or_fp32(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=12, cap_full=(12, 0), cuda_runtime=(12, 8),
               modules={"sageattn3"})
    assert att.get_attention_backend() == "sage3"

    # fp32 -> sdpa fallback, no crash into an uninstalled/uncalled kernel.
    q = torch.randn(1, 2, 4, 8)
    ref = torch.nn.functional.scaled_dot_product_attention(q, q, q, is_causal=False)
    assert torch.equal(att.attention(q, q, q), ref)

    # fp16 + a dense mask -> sdpa fallback too (sage3 supports neither).
    q16 = torch.randn(1, 2, 4, 8, dtype=torch.float16)
    mask = torch.zeros(1, 2, 4, 4, dtype=torch.float16)
    ref16 = torch.nn.functional.scaled_dot_product_attention(q16, q16, q16, attn_mask=mask, is_causal=False)
    assert torch.equal(att.attention(q16, q16, q16, mask=mask), ref16)


# --------------------------------------------------------------------------- #
# sparge (SpargeAttention) — pin-only backend, never auto-selected
# --------------------------------------------------------------------------- #

def test_sparge_probes_available_on_ampere_and_hopper_with_module(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"spas_sage_attn"})
    # available (probe found it) but NEVER in the auto-selectable list.
    assert att.get_attention_backend("sparge") == "sparge"
    assert "sparge" not in att.available_backends()

    _configure(monkeypatch, cuda=True, cap=9, modules={"spas_sage_attn"})
    assert att.get_attention_backend("sparge") == "sparge"


def test_sparge_unavailable_without_module(monkeypatch, caplog):
    _configure(monkeypatch, cuda=True, cap=8, modules=set())
    with caplog.at_level(logging.WARNING):
        chosen = att.get_attention_backend("sparge")
    assert chosen == "sdpa"
    assert any("unavailable" in r.message and "sparge" in r.message for r in caplog.records)


@pytest.mark.parametrize("cap", [7, 6, 10, 12])
def test_sparge_unavailable_outside_ampere_hopper(monkeypatch, cap):
    # thu-ml/SpargeAttn's setup.py only builds for compute-capability major 8
    # or 9 (Ampere/Ada/Hopper) — no Blackwell (10/12) entry as of this writing.
    _configure(monkeypatch, cuda=True, cap=cap, modules={"spas_sage_attn"})
    assert att.get_attention_backend("sparge") == "sdpa"


def test_sparge_never_auto_selected_even_when_only_backend_available(monkeypatch):
    # The defining property: sparge is the ONLY non-sdpa backend the probe
    # would find, yet auto-selection (no override/env/pin) must still land on
    # sdpa, never sparge.
    _configure(monkeypatch, cuda=True, cap=8, modules={"spas_sage_attn"})
    assert att.available_backends() == ["sdpa"]
    assert att.get_attention_backend() == "sdpa"


def test_sparge_explicit_pin_is_honored_over_auto_selection(monkeypatch):
    # Even with sage2/flash also available (so auto-selection has a
    # higher-priority choice), an explicit pin to sparge must win.
    _configure(monkeypatch, cuda=True, cap=8,
               modules={"spas_sage_attn", "sageattention", "triton", "flash_attn"}, sage_v2=True)
    assert att.get_attention_backend() != "sparge"  # auto still prefers sage2
    att.set_backend_override("sparge")
    assert att.get_attention_backend() == "sparge"


def test_sparge_via_env_var_is_honored(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=9, modules={"spas_sage_attn"})
    monkeypatch.setenv(att.ENV_VAR, "sparge")
    assert att.get_attention_backend() == "sparge"


def test_sparge_active_falls_back_to_sdpa_for_mask_or_fp32(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"spas_sage_attn"})
    att.set_backend_override("sparge")
    assert att.get_attention_backend() == "sparge"

    q = torch.randn(1, 2, 256, 64)
    ref = torch.nn.functional.scaled_dot_product_attention(q, q, q, is_causal=False)
    assert torch.equal(att.attention(q, q, q), ref)

    q16 = torch.randn(1, 2, 256, 64, dtype=torch.float16)
    mask = torch.zeros(1, 2, 256, 256, dtype=torch.float16)
    ref16 = torch.nn.functional.scaled_dot_product_attention(q16, q16, q16, attn_mask=mask, is_causal=False)
    assert torch.equal(att.attention(q16, q16, q16, mask=mask), ref16)


def test_sparge_active_falls_back_to_sdpa_for_short_sequence_or_bad_headdim(monkeypatch):
    # SpargeAttn's kernel hard-requires seq_len >= 128 and head_dim in {64, 128}
    # -- a call outside those bounds must still produce a correct (sdpa) result
    # rather than crash into an unsupported kernel shape.
    _configure(monkeypatch, cuda=True, cap=8, modules={"spas_sage_attn"})
    att.set_backend_override("sparge")

    # seq_len 64 < 128 -> shape fallback.
    q_short = torch.randn(1, 2, 64, 64, dtype=torch.float16)
    ref_short = torch.nn.functional.scaled_dot_product_attention(q_short, q_short, q_short, is_causal=False)
    assert torch.equal(att.attention(q_short, q_short, q_short), ref_short)

    # head_dim 96 not in {64, 128} -> shape fallback.
    q_baddim = torch.randn(1, 2, 256, 96, dtype=torch.float16)
    ref_baddim = torch.nn.functional.scaled_dot_product_attention(q_baddim, q_baddim, q_baddim, is_causal=False)
    assert torch.equal(att.attention(q_baddim, q_baddim, q_baddim), ref_baddim)


def test_unknown_backend_warning_lists_pin_only_names(monkeypatch, caplog):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    with caplog.at_level(logging.WARNING):
        att.get_attention_backend("totally-not-a-backend")
    assert any("sparge" in r.message for r in caplog.records if "unknown" in r.message.lower())


def test_known_backends_is_union_of_priority_and_pin_only():
    # The single source of truth callers validating a user-supplied pin (e.g.
    # the admin API) should use instead of duplicating a name list.
    known = att.known_backends()
    assert known == frozenset(att.BACKEND_PRIORITY) | att.PIN_ONLY_BACKENDS
    assert "sparge" in known  # pin-only, still a KNOWN (valid-to-request) name
    for name in att.BACKEND_PRIORITY:
        assert name in known


# --------------------------------------------------------------------------- #
# selection: override / env / fallback-warn
# --------------------------------------------------------------------------- #

def test_default_picks_highest_priority(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=9,
               modules={"sageattention", "triton", "flash_attn"}, sage_v2=True)
    assert att.get_attention_backend() == "sage2"


def test_override_wins_when_available(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    assert att.get_attention_backend("flash") == "flash"
    assert att.get_attention_backend("sdpa") == "sdpa"


def test_env_var_selects_backend(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    monkeypatch.setenv(att.ENV_VAR, "flash")
    assert att.get_attention_backend() == "flash"


def test_override_beats_env(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    monkeypatch.setenv(att.ENV_VAR, "sdpa")
    assert att.get_attention_backend("flash") == "flash"


def test_unavailable_override_warns_and_falls_back(monkeypatch, caplog):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())  # only sdpa
    with caplog.at_level(logging.WARNING):
        chosen = att.get_attention_backend("flash")
    assert chosen == "sdpa"
    assert any("unavailable" in r.message and "flash" in r.message for r in caplog.records)


def test_unknown_backend_warns_and_falls_back(monkeypatch, caplog):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    with caplog.at_level(logging.WARNING):
        chosen = att.get_attention_backend("nonsense")
    assert chosen == "sdpa"
    assert any("unknown" in r.message.lower() for r in caplog.records)


def test_fallback_warns_once_per_name(monkeypatch, caplog):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    with caplog.at_level(logging.WARNING):
        att.get_attention_backend("flash")
        att.get_attention_backend("flash")
    flash_warnings = [r for r in caplog.records if "flash" in r.message]
    assert len(flash_warnings) == 1


# --------------------------------------------------------------------------- #
# in-memory backend pin (admin "Optimizations" panel)
# --------------------------------------------------------------------------- #

def test_backend_override_used_when_no_override_arg_or_env(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    att.set_backend_override("flash")
    assert att.get_attention_backend() == "flash"
    assert att.get_backend_override() == "flash"


def test_env_var_beats_backend_override(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    att.set_backend_override("flash")
    monkeypatch.setenv(att.ENV_VAR, "sdpa")
    assert att.get_attention_backend() == "sdpa"


def test_override_arg_beats_backend_override(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    att.set_backend_override("sdpa")
    assert att.get_attention_backend("flash") == "flash"


@pytest.mark.parametrize("cleared", ["auto", "", None])
def test_backend_override_auto_and_empty_and_none_clear_pin(monkeypatch, cleared):
    _configure(monkeypatch, cuda=True, cap=9,
               modules={"sageattention", "triton", "flash_attn"}, sage_v2=True)
    att.set_backend_override("flash")
    assert att.get_backend_override() == "flash"
    att.set_backend_override(cleared)
    assert att.get_backend_override() is None
    assert att.get_attention_backend() == "sage2"  # falls through to best-available


def test_backend_override_normalizes_case_and_whitespace(monkeypatch):
    _configure(monkeypatch, cuda=True, cap=8, modules={"flash_attn"})
    att.set_backend_override("  Flash  ")
    assert att.get_backend_override() == "flash"
    assert att.get_attention_backend() == "flash"


# --------------------------------------------------------------------------- #
# sdpa correctness
# --------------------------------------------------------------------------- #

def _manual_attention(q, k, v, mask=None):
    d = q.shape[-1]
    scores = (q @ k.transpose(-2, -1)) / math.sqrt(d)
    if mask is not None:
        if mask.dtype == torch.bool:
            scores = scores.masked_fill(~mask, float("-inf"))
        else:
            scores = scores + mask
    return scores.softmax(dim=-1) @ v


def test_sdpa_matches_manual_softmax(monkeypatch):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    torch.manual_seed(0)
    q = torch.randn(2, 3, 5, 8, dtype=torch.float64)
    k = torch.randn(2, 3, 5, 8, dtype=torch.float64)
    v = torch.randn(2, 3, 5, 8, dtype=torch.float64)
    out = att.attention(q, k, v)
    assert out.shape == (2, 3, 5, 8)
    assert torch.allclose(out, _manual_attention(q, k, v), atol=1e-12)


def test_sdpa_matches_manual_with_additive_mask(monkeypatch):
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    torch.manual_seed(1)
    q = torch.randn(1, 2, 4, 8, dtype=torch.float64)
    k = torch.randn(1, 2, 4, 8, dtype=torch.float64)
    v = torch.randn(1, 2, 4, 8, dtype=torch.float64)
    mask = torch.zeros(1, 2, 4, 4, dtype=torch.float64)
    mask[..., 3] = float("-inf")  # forbid attending to last key
    out = att.attention(q, k, v, mask=mask)
    assert torch.allclose(out, _manual_attention(q, k, v, mask), atol=1e-12)


def test_dispatch_bit_identical_to_raw_sdpa(monkeypatch):
    # The refactor must not perturb the sdpa numerics vs a direct F.sdpa call.
    _configure(monkeypatch, cuda=False, cap=0, modules=set())
    torch.manual_seed(2)
    q = torch.randn(2, 4, 7, 16)
    k = torch.randn(2, 4, 7, 16)
    v = torch.randn(2, 4, 7, 16)
    ref = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=False)
    assert torch.equal(att.attention(q, k, v), ref)


def test_heads_mismatch_raises():
    q = torch.randn(1, 4, 5, 8)
    with pytest.raises(ValueError):
        att.attention(q, q, q, heads=8)


def test_accelerated_backend_falls_back_to_sdpa_for_fp32_and_mask(monkeypatch):
    # Pretend sage2 is selected, but fp32 input must route through sdpa (correct,
    # not a crash into an uninstalled kernel).
    _configure(monkeypatch, cuda=True, cap=9, modules={"sageattention", "triton"}, sage_v2=True)
    assert att.get_attention_backend() == "sage2"
    q = torch.randn(1, 2, 4, 8)  # fp32 -> sdpa fallback
    ref = torch.nn.functional.scaled_dot_product_attention(q, q, q, is_causal=False)
    assert torch.equal(att.attention(q, q, q), ref)


# --------------------------------------------------------------------------- #
# cross-backend agreement — only if the kernels are actually installed here
# --------------------------------------------------------------------------- #

def test_cross_backend_agreement_if_installed():
    att.reset_backend_cache()
    backends = att.available_backends()
    accel = [b for b in backends if b != "sdpa"]
    if not accel or not torch.cuda.is_available():
        pytest.skip(f"no accelerated attention kernels installed (have {backends})")

    torch.manual_seed(3)
    q = torch.randn(1, 4, 64, 64, dtype=torch.float16, device="cuda")
    k = torch.randn(1, 4, 64, 64, dtype=torch.float16, device="cuda")
    v = torch.randn(1, 4, 64, 64, dtype=torch.float16, device="cuda")
    ref = att.attention(q, k, v, backend="sdpa")
    for b in accel:
        try:
            out = att.attention(q, k, v, backend=b)
        except ImportError as exc:
            pytest.skip(f"{b} kernel module is installed but fails to import ({exc})")
        assert torch.allclose(out, ref, atol=2e-2, rtol=2e-2), b


# --------------------------------------------------------------------------- #
# per-device availability cache (multi-GPU correctness, roadmap E15)
# --------------------------------------------------------------------------- #

class _FakeCudaTensor:
    """Duck-types just enough of torch.Tensor for attention()'s dispatch path
    up to (but not including) the actual kernel call, which the test stubs
    out — real CUDA tensors don't allow overriding .is_cuda/.device."""

    def __init__(self, device_index, dtype=torch.float16, shape=(1, 2, 4, 8)):
        self.ndim = len(shape)
        self.shape = shape
        self.dtype = dtype
        self.is_cuda = True
        self.device = torch.device(f"cuda:{device_index}")


def test_get_availability_caches_independently_per_device(monkeypatch):
    # device 0 has sage3-capable hardware, device 1 doesn't — a single global
    # (device-unaware) cache could only ever report one of these.
    def cap_for(device_index=None):
        return (12, 0) if device_index == 0 else (7, 5)

    monkeypatch.setattr(att, "_has_module", lambda name: name == "sageattn3")
    monkeypatch.setattr(att, "_cuda_capability_major", lambda device_index=None: cap_for(device_index)[0])
    monkeypatch.setattr(att, "_cuda_capability", cap_for)
    monkeypatch.setattr(att, "_cuda_runtime_version", lambda: (12, 8))
    monkeypatch.setattr(att.torch.cuda, "is_available", lambda: True)
    att.reset_backend_cache()

    assert "sage3" in att.available_backends(device_index=0)
    assert "sage3" not in att.available_backends(device_index=1)
    # Re-checking device 0 must still see its own cached (favorable) result,
    # not device 1's, proving the two are cached under separate keys.
    assert "sage3" in att.available_backends(device_index=0)


def test_reset_backend_cache_clears_every_device(monkeypatch):
    calls = []

    def counting_capability_major(device_index=None):
        calls.append(device_index)
        return 0

    monkeypatch.setattr(att, "_has_module", lambda name: False)
    monkeypatch.setattr(att, "_cuda_capability_major", counting_capability_major)
    monkeypatch.setattr(att, "_cuda_capability", lambda device_index=None: (0, 0))
    monkeypatch.setattr(att, "_cuda_runtime_version", lambda: (0, 0))
    monkeypatch.setattr(att.torch.cuda, "is_available", lambda: True)
    att.reset_backend_cache()

    att.available_backends(device_index=0)
    att.available_backends(device_index=1)
    assert calls == [0, 1]
    att.available_backends(device_index=0)  # cached: no new probe
    assert calls == [0, 1]

    att.reset_backend_cache()
    att.available_backends(device_index=0)
    att.available_backends(device_index=1)
    assert calls == [0, 1, 0, 1]  # both devices re-probed after reset


def test_attention_dispatch_validates_against_q_device_not_current_device(monkeypatch):
    """The regression this exists to prevent: attention() must resolve
    availability from q.device, never from whatever CUDA device happens to be
    "current" on this thread (e.g. a text encoder spilled to a second card)."""
    seen_device_indices = []

    def fake_get_attention_backend(backend=None, device_index=None):
        seen_device_indices.append(device_index)
        return "sdpa"

    monkeypatch.setattr(att, "get_attention_backend", fake_get_attention_backend)
    monkeypatch.setattr(att, "_sdpa", lambda q, k, v, mask: q)

    fake_q = _FakeCudaTensor(device_index=3)
    att.attention(fake_q, fake_q, fake_q)
    assert seen_device_indices == [3]

    fake_q_other = _FakeCudaTensor(device_index=0)
    att.attention(fake_q_other, fake_q_other, fake_q_other)
    assert seen_device_indices == [3, 0]


def test_attention_dispatch_device_index_is_none_for_cpu_tensors(monkeypatch):
    seen_device_indices = []

    def fake_get_attention_backend(backend=None, device_index=None):
        seen_device_indices.append(device_index)
        return "sdpa"

    monkeypatch.setattr(att, "get_attention_backend", fake_get_attention_backend)
    q = torch.randn(1, 2, 4, 8)  # real CPU tensor
    att.attention(q, q, q)
    assert seen_device_indices == [None]


# --------------------------------------------------------------------------- #
# conditional sage V-scale and nan_to_num safety net
#
# The original unconditional V-prescale (/256 then *256) protected Qwen-Image's
# hot V (~N(0, 5000^2)) from overflowing sage's fp16 internals (task #43 black
# images), but for LTX 2.3's small-magnitude V it introduced fp16 rounding
# noise that compounded into film-grain over ~1500 attention calls.
# _sage() now scales CONDITIONALLY: raw pass-through (ComfyUI-identical) below
# _SAGE_V_SAFE_MAX, the exact /256 path above it, decided on-GPU with no host
# sync; nan_to_num remains the non-finite safety floor in both regimes.
# --------------------------------------------------------------------------- #

class _FakeSageModule:
    """Stand-in for the ``sageattention`` package: records the exact tensor it
    was called with and returns a caller-controlled tensor, so the pass-through
    wiring in ``_sage`` can be verified without CUDA or the real package
    installed."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.calls = []

    def sageattn(self, q, k, v, tensor_layout="HND", is_causal=False):
        self.calls.append({"q": q, "k": k, "v": v})
        return self.return_value


def _patch_sageattention(monkeypatch, fake_module):
    import sys

    monkeypatch.setitem(sys.modules, "sageattention", fake_module)


def test_sage_passes_small_v_through_to_the_kernel_unscaled(monkeypatch):
    """LTX regime: max|V| below _SAGE_V_SAFE_MAX must reach the kernel
    bit-identical (no prescale -> no fp16 subnormal noise -> no film grain)."""
    v = torch.full((1, 2, 4, 8), 160.0)  # LTX 2.3-typical magnitude
    fake = _FakeSageModule(return_value=torch.zeros(1, 2, 4, 8))
    _patch_sageattention(monkeypatch, fake)

    q = torch.randn(1, 2, 4, 8)
    k = torch.randn(1, 2, 4, 8)
    att._sage(q, k, v)

    assert len(fake.calls) == 1
    called_v = fake.calls[0]["v"]
    # V must arrive at the kernel UNCHANGED -- v / 1.0 is IEEE-exact.
    assert torch.equal(called_v, v)
    # q/k also pass through unscaled.
    assert torch.equal(fake.calls[0]["q"], q)
    assert torch.equal(fake.calls[0]["k"], k)


def test_sage_small_v_returns_kernel_output_without_postscale(monkeypatch):
    kernel_out = torch.full((1, 2, 4, 8), 3.0)
    fake = _FakeSageModule(return_value=kernel_out)
    _patch_sageattention(monkeypatch, fake)

    q = k = v = torch.randn(1, 2, 4, 8)  # randn -> max|V| well below threshold
    out = att._sage(q, k, v)

    # Output must equal the kernel output directly (scale == 1.0).
    assert torch.allclose(out, kernel_out)


def test_sage_hot_v_engages_the_256_prescale(monkeypatch):
    """Qwen regime: max|V| above _SAGE_V_SAFE_MAX must engage the exact
    production-validated /256 prescale (the task #43 overflow guard) and
    multiply the kernel output back by 256."""
    v = torch.full((1, 2, 4, 8), 5000.0)  # Qwen-Image-repro magnitude
    kernel_out = torch.full((1, 2, 4, 8), 3.0)
    fake = _FakeSageModule(return_value=kernel_out)
    _patch_sageattention(monkeypatch, fake)

    q = torch.randn(1, 2, 4, 8)
    k = torch.randn(1, 2, 4, 8)
    out = att._sage(q, k, v)

    assert len(fake.calls) == 1
    # V must arrive prescaled by exactly 1/256 (power of two -> exact).
    assert torch.equal(fake.calls[0]["v"], v / 256.0)
    # q/k are never scaled.
    assert torch.equal(fake.calls[0]["q"], q)
    assert torch.equal(fake.calls[0]["k"], k)
    # Kernel output must be scaled back up by 256.
    assert torch.allclose(out, kernel_out * 256.0)


def test_sage_round_trips_a_linear_kernel_exactly_in_both_regimes(monkeypatch):
    """A linear-in-V mock kernel must reproduce its own result exactly through
    _sage in BOTH regimes: scale 1.0 trivially, and /256 * 256 exactly because
    the scale is a power of two."""

    def linear_in_v(q, k, v, tensor_layout="HND", is_causal=False):
        return v * 2.0  # stand-in "kernel": any linear function of V

    fake = _FakeSageModule(return_value=None)
    fake.sageattn = linear_in_v
    _patch_sageattention(monkeypatch, fake)

    q = k = torch.randn(1, 2, 4, 8)
    for magnitude in (10.0, 5000.0):  # below / above _SAGE_V_SAFE_MAX
        v = torch.randn(1, 2, 4, 8) * magnitude
        out = att._sage(q, k, v)
        assert torch.equal(out, v * 2.0), f"round-trip broke at magnitude {magnitude}"


def test_sage_output_is_never_nan_or_inf_even_if_the_kernel_returns_them(monkeypatch):
    """Defensive floor: nan_to_num must prevent ``_sage`` from ever handing a
    non-finite tensor back to its caller (the mechanism that produced task #43's
    black images)."""
    poisoned = torch.tensor([[[[float("nan"), float("inf"), float("-inf"), 1.0]]]])
    fake = _FakeSageModule(return_value=poisoned)
    _patch_sageattention(monkeypatch, fake)

    q = k = v = torch.randn(1, 1, 1, 4)
    out = att._sage(q, k, v)

    assert torch.isfinite(out).all()
    # nan/posinf/neginf all map to 0.0; the one finite value passes through as-is.
    assert out[0, 0, 0, 0].item() == 0.0
    assert out[0, 0, 0, 1].item() == 0.0
    assert out[0, 0, 0, 2].item() == 0.0
    assert out[0, 0, 0, 3].item() == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Large-row regime (task: H3 upscale refine OOM at ~78k rows): above
# _SAGE_SYNC_ROWS, _sage pays one host sync to decide the V-prescale so the
# pass-through case allocates NO V copy at all (the torch.where path's
# `v / scale` + `out * scale` cost ~2.3GB there). Numerics are identical in
# both regimes -- only allocation behavior changes, and only above the
# threshold.
# --------------------------------------------------------------------------- #

def _large_row_tensors(magnitude: float):
    rows = att._SAGE_SYNC_ROWS
    q = torch.randn(1, 1, rows, 8)
    k = torch.randn(1, 1, rows, 8)
    v = torch.randn(1, 1, rows, 8) * magnitude
    return q, k, v


def test_sage_large_rows_small_v_skips_the_copy_entirely(monkeypatch):
    q, k, v = _large_row_tensors(10.0)
    fake = _FakeSageModule(return_value=torch.zeros_like(v))
    _patch_sageattention(monkeypatch, fake)

    att._sage(q, k, v)

    # THE SAME OBJECT, not an equal copy -- the entire point of the branch.
    assert fake.calls[0]["v"] is v
    assert fake.calls[0]["q"] is q
    assert fake.calls[0]["k"] is k


def test_sage_large_rows_hot_v_still_prescales_exactly(monkeypatch):
    q, k, v = _large_row_tensors(5000.0)
    kernel_out = torch.full_like(v, 3.0)
    fake = _FakeSageModule(return_value=kernel_out)
    _patch_sageattention(monkeypatch, fake)

    out = att._sage(q, k, v)

    assert torch.equal(fake.calls[0]["v"], v / 256.0)
    assert torch.allclose(out, kernel_out * 256.0)


def test_sage_below_the_row_threshold_keeps_the_sync_free_path(monkeypatch):
    # The validated hot path still goes through torch.where's `v / scale`,
    # which materializes a copy -- pinned here so a "simplification" can't
    # silently swap the small-sequence regime onto the syncing branch.
    v = torch.full((1, 2, 4, 8), 160.0)
    fake = _FakeSageModule(return_value=torch.zeros(1, 2, 4, 8))
    _patch_sageattention(monkeypatch, fake)

    att._sage(torch.randn(1, 2, 4, 8), torch.randn(1, 2, 4, 8), v)

    called_v = fake.calls[0]["v"]
    assert torch.equal(called_v, v)
    assert called_v is not v


def test_absmax_matches_abs_amax_without_the_full_size_temp():
    # _absmax must equal abs().amax() on mixed-sign, all-negative, and
    # all-positive tensors -- the all-negative case is the one a naive
    # `t.amax()` alone would get wrong.
    for t in (torch.randn(3, 5) * 100, -torch.rand(4, 4) - 5.0, torch.rand(2, 8) + 3.0):
        assert torch.equal(att._absmax(t), t.abs().amax())
