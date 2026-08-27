"""Tests for the sequence-length-aware DiT placement helper.

Covers: the per-token activation-reserve formula (floor, scaling, audio
addition), the resident-vs-partial decision at the exact scenario described in
the audit (5s/15s full pin, 40s partial on a 32GB card with a 23.3GB DiT), the
OOM-degrade ladder (mirrors ``NativeGenerator._move_dit_to_gpu``/
``_stream_dit_to_gpu``), the foreign-eviction exclude-list plumbing (including
the one-shot-generator footgun), and the CPU/no-CUDA passthrough.

No real GPU or CUDA needed: ``get_residency_registry``/``free_vram_gb``/
``minimum_inference_memory_gb`` are patched at the module boundary (the same
style as ``test_dit_restore.py``), and OOM is simulated by raising the real
``torch.cuda.OutOfMemoryError`` class directly (constructing/raising it needs
no actual device).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import torch
import torch.nn as nn

from src.pipelines.pipes._shared.generation.dit_placement import (
    _ACTIVATION_RESERVE_FLOOR_GB,
    _dit_has_active_lora,
    _dit_lora_delta_gb,
    _ffn_transient_bytes_per_token,
    _LTX_INNER_DIM,
    DitPlacementDecision,
    estimate_activation_reserve_gb,
    place_dit_for_sequence,
)

_MOD = "src.pipelines.pipes._shared.generation.dit_placement"


# -- activation reserve formula -----------------------------------------------

def test_reserve_floors_at_zero_tokens():
    assert estimate_activation_reserve_gb(0, 0) == _ACTIVATION_RESERVE_FLOOR_GB


def test_reserve_scales_linearly_once_above_the_floor():
    # Both values chosen well above _ACTIVATION_RESERVE_FLOOR_GB so the ratio
    # reflects the linear per-token formula, not the floor clamp.
    small = estimate_activation_reserve_gb(20_000)
    large = estimate_activation_reserve_gb(2_000_000)
    assert large > small
    assert large / small == pytest.approx(100, rel=0.05)


def test_reserve_matches_audit_scenarios_within_margin():
    # Audit: S~14,080 (5s) peaked ~1.2GB; S~110,880 (40s) peaked
    # ~9.4GB -- both WITHOUT this module's safety margin. This module adds a
    # 15% multiplicative margin on top, so the estimate should sit a bit above
    # (never below) those measured peaks.
    five_s = estimate_activation_reserve_gb(14_080)
    forty_s = estimate_activation_reserve_gb(110_880)
    assert 1.2 <= five_s <= 1.6
    assert 9.4 <= forty_s <= 12.0


def test_audio_tokens_add_to_the_reserve():
    video_only = estimate_activation_reserve_gb(50_000, audio_tokens=0)
    with_audio = estimate_activation_reserve_gb(50_000, audio_tokens=5_000)
    assert with_audio > video_only
    assert with_audio == estimate_activation_reserve_gb(55_000, audio_tokens=0)


# -- inner_dim parameterization (MiniMax-H3: attn inner 7168 != hidden 5376) --

def test_inner_dim_defaults_to_ltx_no_behavior_change():
    assert estimate_activation_reserve_gb(50_000) == estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)


def test_wider_inner_dim_increases_the_reserve():
    ltx_reserve = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)
    h3_reserve = estimate_activation_reserve_gb(50_000, inner_dim=7168)  # H3's attn inner dim
    assert h3_reserve > ltx_reserve


def test_inner_dim_scales_the_reserve_linearly_above_the_floor():
    small = estimate_activation_reserve_gb(50_000, inner_dim=1000)
    large = estimate_activation_reserve_gb(50_000, inner_dim=2000)
    assert large / small == pytest.approx(2.0, rel=1e-6)


def test_inner_dim_is_a_no_op_at_zero_tokens_the_floor_still_wins():
    assert estimate_activation_reserve_gb(0, inner_dim=7168) == _ACTIVATION_RESERVE_FLOOR_GB


H3_ATTN_INNER_DIM = 56 * 128  # MiniMax-H3: 56 heads x 128 head_dim


def test_place_dit_for_sequence_threads_inner_dim_into_the_reserve(monkeypatch):
    # Two calls that would land on OPPOSITE sides of the resident/partial
    # decision purely because of inner_dim -- proves place_dit_for_sequence
    # actually forwards it to estimate_activation_reserve_gb rather than
    # silently dropping the kwarg.
    monkeypatch.setattr(f"{_MOD}.free_vram_gb", lambda device: 15.0)
    monkeypatch.setattr(f"{_MOD}.minimum_inference_memory_gb", lambda: 0.0)
    manager = SimpleNamespace(ensure_free=lambda *a, **k: False, offload_all=lambda *a, **k: False)
    monkeypatch.setattr(f"{_MOD}.get_residency_registry", lambda: manager)

    calls = []
    dit = SimpleNamespace(
        estimated_vram_gb=9.5,
        move_to=lambda device: calls.append(("resident", device)),
        stream_to=lambda device, budget: calls.append(("partial", device, budget)),
    )
    tokens = 400_000  # large enough that a wide inner_dim pushes the reserve past 0.5GB of headroom

    decision_narrow = place_dit_for_sequence(dit, "cuda", video_tokens=tokens, inner_dim=64)
    assert decision_narrow.mode == "resident"

    decision_wide = place_dit_for_sequence(dit, "cuda", video_tokens=tokens, inner_dim=H3_ATTN_INNER_DIM)
    assert decision_wide.mode == "partial"


# -- LoRA output-branch term -------------------------------------------

def test_lora_active_defaults_to_off_no_behavior_change():
    assert estimate_activation_reserve_gb(50_000) == estimate_activation_reserve_gb(
        50_000, lora_active=False,
    )


def test_lora_active_increases_the_reserve_above_the_floor():
    # well above the floor so the LoRA term's contribution is visible.
    without = estimate_activation_reserve_gb(50_000, lora_active=False)
    with_lora = estimate_activation_reserve_gb(50_000, lora_active=True)
    assert with_lora > without


def test_lora_active_is_a_no_op_at_zero_tokens_the_floor_still_wins():
    # zero tokens -> zero contribution from any per-token term, so both sides
    # land on the same floor regardless of the LoRA flag.
    assert estimate_activation_reserve_gb(0, lora_active=True) == _ACTIVATION_RESERVE_FLOOR_GB
    assert estimate_activation_reserve_gb(0, lora_active=True) == estimate_activation_reserve_gb(
        0, lora_active=False,
    )


# -- LoRA detection (_dit_has_active_lora) --------------------------------------

def _linear_with_deltas(deltas) -> nn.Linear:
    linear = nn.Linear(4, 4)
    linear.lora_deltas = deltas
    return linear


def test_dit_without_a_module_attribute_has_no_active_lora():
    dit, _ = _dit()  # the shared test double never sets .module
    assert _dit_has_active_lora(dit) is False


def test_dit_module_that_is_not_an_nn_module_is_inactive():
    # Some pipe unit tests stub ``dit.module`` as a bare callable (the DiT
    # forward function itself, not a real ``nn.Module``) -- must not raise.
    dit = SimpleNamespace(module=lambda x: x)
    assert _dit_has_active_lora(dit) is False


def test_dit_module_with_no_lora_deltas_attribute_anywhere_is_inactive():
    dit = SimpleNamespace(module=nn.Sequential(nn.Linear(4, 4), nn.ReLU()))
    assert _dit_has_active_lora(dit) is False


def test_dit_module_with_only_empty_lora_deltas_is_inactive():
    dit = SimpleNamespace(module=nn.Sequential(_linear_with_deltas([]), _linear_with_deltas(None)))
    assert _dit_has_active_lora(dit) is False


def test_dit_module_with_a_populated_lora_deltas_is_active():
    active = SimpleNamespace(down=torch.zeros(4, 4), up=torch.zeros(4, 4))
    dit = SimpleNamespace(
        module=nn.Sequential(_linear_with_deltas([]), _linear_with_deltas([active])),
    )
    assert _dit_has_active_lora(dit) is True


# -- test doubles --------------------------------------------------------------

class _FakeResidencyRegistry:
    """Records every eviction call; nothing is ever actually offloaded (the
    tests each set up ``free_vram_gb`` to already reflect the desired state)."""

    def __init__(self):
        self.ensure_free_calls = []
        self.offload_all_calls = []

    def ensure_free(self, device, need_gb, current_free_gb, *, exclude=()):
        self.ensure_free_calls.append((device, need_gb, current_free_gb, tuple(exclude)))
        return []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def _dit(estimated_vram_gb=23.3):
    calls = {"move_to": [], "stream_to": [], "offload": 0}

    def move_to(d):
        calls["move_to"].append(d)

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))

    def offload():
        calls["offload"] += 1

    dit = SimpleNamespace(estimated_vram_gb=estimated_vram_gb, move_to=move_to,
                          stream_to=stream_to, offload=offload)
    return dit, calls


def _dit_with_lora(estimated_vram_gb=23.3, *, active: bool):
    """Same test double as :func:`_dit`, plus a ``.module`` whose one Linear
    carries a populated (``active=True``) or empty/absent (``active=False``)
    ``lora_deltas`` -- exercises :func:`place_dit_for_sequence`'s real
    ``_dit_has_active_lora`` detection end to end, not just the formula."""
    dit, calls = _dit(estimated_vram_gb)
    linear = nn.Linear(4, 4)
    if active:
        delta = SimpleNamespace(down=torch.zeros(4, 4), up=torch.zeros(4, 4))
        linear.lora_deltas = [delta]
    dit.module = nn.Sequential(linear)
    return dit, calls


def _patched(free_gb, *, min_reserve=1.0, manager=None):
    manager = manager or _FakeResidencyRegistry()
    return (
        patch(f"{_MOD}.free_vram_gb", return_value=free_gb),
        patch(f"{_MOD}.minimum_inference_memory_gb", return_value=min_reserve),
        patch(f"{_MOD}.get_residency_registry", return_value=manager),
    ), manager


# -- CPU / non-CUDA passthrough -------------------------------------------------

def test_cpu_device_is_a_plain_move_no_vram_queries():
    dit, calls = _dit()
    with patch(f"{_MOD}.free_vram_gb") as mock_free:
        decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000)
    mock_free.assert_not_called()
    assert calls["move_to"] == ["cpu"]
    assert calls["stream_to"] == []
    assert decision.mode == "cpu"


# -- decision matrix: 5s / 15s / 40s @ 720x1280 on a 32GB card, 23.3GB DiT ----
# t_lat*h_lat*w_lat held at 880 tokens/frame (matches the audit's 14,080 at
# t_lat=16 i.e. 5s); frames -> t_lat via (frames-1)//8+1 at 25fps.

_TOKENS_PER_FRAME = 880


def _video_tokens_for(seconds: float) -> int:
    frames = round(seconds * 25)
    t_lat = (frames - 1) // 8 + 1
    return t_lat * _TOKENS_PER_FRAME


def test_five_seconds_fits_full_pin_zero_perf_change():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert calls["move_to"] == ["cuda"]
    assert calls["stream_to"] == []  # zero perf change: exactly the old move_to


def test_fifteen_seconds_still_fits_full_pin():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(15))
    assert decision.mode == "resident"
    assert calls["stream_to"] == []


def test_forty_seconds_needs_partial_residency():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert decision.mode == "partial"
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1
    device, budget = calls["stream_to"][0]
    assert device == "cuda"
    # weight budget = free - min_reserve - activation_reserve; the DiT (23.3GB)
    # does not fit it, but the budget itself must be a sane positive number
    # well under the DiT's own size.
    assert 0.0 < budget < 23.3
    assert decision.weight_budget_gb == pytest.approx(budget)
    assert decision.dit_weight_gb == 23.3


# -- audio tokens can tip an otherwise-fitting clip into partial --------------

def test_audio_tokens_can_tip_placement_into_partial():
    dit, calls = _dit(estimated_vram_gb=23.3)
    video_tokens = _video_tokens_for(15)  # fits alone (see above)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=video_tokens, audio_tokens=2_000_000,  # absurd, forces the tip
        )
    assert decision.mode == "partial"


# -- degenerate tiny VRAM -------------------------------------------------------

def test_degenerate_tiny_vram_still_produces_a_non_negative_budget():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=0.5)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "partial"
    assert decision.weight_budget_gb == 0.0
    assert calls["stream_to"] == [("cuda", 0.0)]


# -- foreign-resident exclusion / one-shot-generator footgun ------------------

def test_own_models_excluded_from_eviction_even_as_a_one_shot_generator():
    dit, calls = _dit(estimated_vram_gb=23.3)
    vae = object()
    patches, manager = _patched(free_gb=32.0)

    def own_models_gen():
        yield dit
        yield vae

    with patches[0], patches[1], patches[2]:
        place_dit_for_sequence(
            dit, "cuda", video_tokens=_video_tokens_for(5), own_models=own_models_gen(),
        )
    assert manager.ensure_free_calls, "expected an ensure_free eviction call before placement"
    _, _, _, exclude = manager.ensure_free_calls[0]
    assert {id(m) for m in exclude} == {id(dit), id(vae)}


# -- OOM-degrade ladder: full move ------------------------------------------

def test_full_move_oom_degrades_through_evict_retry_to_partial():
    calls = {"move_to": 0, "stream_to": []}

    def move_to(d):
        calls["move_to"] += 1
        raise torch.cuda.OutOfMemoryError("simulated")

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))

    dit = SimpleNamespace(estimated_vram_gb=23.3, move_to=move_to, stream_to=stream_to,
                          offload=lambda: None)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    # try once, evict-and-retry once (both raise), then degrade to partial.
    assert calls["move_to"] == 2
    assert len(calls["stream_to"]) == 1
    assert decision.mode == "partial"
    # The degrade path budgets off LIVE free VRAM minus the min-inference
    # reserve only (not the activation reserve) -- mirrors
    # NativeGenerator._move_dit_to_gpu's identical fallback.
    assert calls["stream_to"][0][1] == pytest.approx(31.0)


def test_partial_move_oom_degrades_to_fully_streamed():
    calls = {"stream_to": []}

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))
        if len(calls["stream_to"]) == 1:
            raise torch.cuda.OutOfMemoryError("simulated")

    dit = SimpleNamespace(estimated_vram_gb=23.3, move_to=lambda d: None, stream_to=stream_to,
                          offload=lambda: None)
    # Force the partial branch directly (40s scenario), then have the FIRST
    # stream_to attempt OOM to exercise the fully-streamed fallback.
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert len(calls["stream_to"]) == 2
    assert calls["stream_to"][1] == ("cuda", 0.0)
    assert decision.mode == "partial"


# -- decision dataclass shape --------------------------------------------------

def test_decision_is_a_frozen_dataclass_with_expected_fields():
    dit, _ = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5), audio_tokens=10)
    assert isinstance(decision, DitPlacementDecision)
    assert decision.video_tokens == _video_tokens_for(5)
    assert decision.audio_tokens == 10
    assert decision.extra_reserve_gb == 0.0  # unset by default -- prior callers unaffected
    with pytest.raises(Exception):
        decision.mode = "partial"  # frozen


# -- SwiGLU FFN transient (real H3 turbo-LoRA OOM: dit_weight_gb=19.52,
# activation_reserve_gb=4.44, free~27.4 -> chose "resident"; died on "Tried to
# allocate 1.03 GiB" == S * 2*ffn_dim * 2B, the fc1 fused value|gate output,
# never modeled by the attention-shaped terms alone) -----------------------

_H3_INNER_DIM = 56 * 128   # 7168
_H3_FFN_DIM = 14336
_TRACE_VIDEO_TOKENS = 18870
_TRACE_AUDIO_TOKENS = 414
_TRACE_FREE_GB = 27.4
_TRACE_DIT_WEIGHT_GB = 19.52
_TRACE_LORA_WEIGHT_GB = 1.4


def test_ffn_dim_none_is_a_no_op_ltx_call_sites_unchanged():
    # Every existing LTX call site never passes ffn_dim -- the default (None)
    # must reproduce the exact prior formula, byte for byte.
    with_none = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM, ffn_dim=None)
    without_arg = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)
    assert with_none == without_arg


def test_ffn_dim_zero_is_also_a_no_op():
    assert estimate_activation_reserve_gb(50_000, ffn_dim=0) == estimate_activation_reserve_gb(50_000, ffn_dim=None)


def test_ffn_transient_bytes_matches_the_observed_failing_allocation():
    # The failing allocation in the real trace was EXACTLY
    # S * 2*ffn_dim * 2B (bf16) -- the fc1 fused value|gate output alone, the
    # first and largest of the three terms _ffn_transient_bytes_per_token
    # sums. Not the full per-token term (which also includes the SiLU output
    # and the value*SiLU(gate) product) -- this pins the verified SUB-term.
    fc1_out_bytes_per_token = 2 * _H3_FFN_DIM * 2
    s = _TRACE_VIDEO_TOKENS + _TRACE_AUDIO_TOKENS
    fc1_out_gib = (s * fc1_out_bytes_per_token) / 1024 ** 3
    assert fc1_out_gib == pytest.approx(1.03, abs=0.01)
    # The full term this module actually reserves is a conservative
    # SUPERSET of that one sub-allocation (also covers SiLU + product).
    assert _ffn_transient_bytes_per_token(_H3_FFN_DIM) > fc1_out_bytes_per_token


def test_ffn_dim_increases_the_reserve_above_the_attention_only_estimate():
    without_ffn = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, lora_active=True, inner_dim=_H3_INNER_DIM,
    )
    with_ffn = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, lora_active=True, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    # Reproduces the trace's OWN reported value for the (undercounting) old
    # formula exactly -- confirms this test's setup matches the real report.
    assert without_ffn == pytest.approx(4.44, abs=0.01)
    assert with_ffn > without_ffn


def test_h3_corrected_reserve_lands_at_or_above_the_derived_floor():
    # Derived from the trace's own numbers (team-lead's math): 24.52GB
    # allocated at death - 19.52 weights - ~1.4 LoRA => ~3.6GB activations
    # already resident when it needed 1.03GiB MORE and failed => true reserve
    # need >= ~4.6GB before any margin. This module's own (more conservative)
    # first-principles estimate must clear that floor.
    reserve = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, lora_active=True, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    assert reserve >= 4.6


def test_ltx_reserve_byte_identical_with_and_without_ffn_dim_kwarg_present_in_signature():
    # Regression guard: adding the ffn_dim parameter must not perturb ANY
    # existing LTX-shaped call (no inner_dim, no ffn_dim -- the historical
    # call shape from before either kwarg existed).
    assert estimate_activation_reserve_gb(110_880) == pytest.approx(9.4, rel=0.3)  # sanity vs. the audit figure


# -- LoRA delta counted as resident weight (the OTHER half of the same fix) --

def _linear_with_sized_delta(down_shape, up_shape, *, dtype=torch.float32) -> nn.Linear:
    linear = nn.Linear(4, 4)
    delta = SimpleNamespace(down=torch.zeros(down_shape, dtype=dtype), up=torch.zeros(up_shape, dtype=dtype))
    linear.lora_deltas = [delta]
    return linear


def test_dit_lora_delta_gb_is_zero_with_no_lora():
    dit = SimpleNamespace(module=nn.Sequential(nn.Linear(4, 4)))
    assert _dit_lora_delta_gb(dit) == 0.0


def test_dit_lora_delta_gb_is_zero_for_a_non_module():
    dit = SimpleNamespace(module=lambda x: x)
    assert _dit_lora_delta_gb(dit) == 0.0


def test_dit_lora_delta_gb_sums_down_and_up_tensors_exactly():
    # down: 100x50, up: 50x100, both fp32 -- exactly 2*100*50*4 bytes.
    dit = SimpleNamespace(module=nn.Sequential(_linear_with_sized_delta((100, 50), (50, 100))))
    expected_gb = (2 * 100 * 50 * 4) / 1024 ** 3
    assert _dit_lora_delta_gb(dit) == pytest.approx(expected_gb, rel=1e-9)


def test_dit_lora_delta_gb_sums_across_multiple_linears_and_stacked_loras():
    dit = SimpleNamespace(module=nn.Sequential(
        _linear_with_sized_delta((10, 10), (10, 10)),
        _linear_with_sized_delta((20, 20), (20, 20)),
    ))
    expected_gb = (2 * 10 * 10 * 4 + 2 * 20 * 20 * 4) / 1024 ** 3
    assert _dit_lora_delta_gb(dit) == pytest.approx(expected_gb, rel=1e-9)


def test_dit_lora_delta_gb_ignores_non_tensor_delta_fields():
    linear = nn.Linear(4, 4)
    linear.lora_deltas = [SimpleNamespace(down=None, up="not a tensor")]
    dit = SimpleNamespace(module=nn.Sequential(linear))
    assert _dit_lora_delta_gb(dit) == 0.0


def test_place_dit_for_sequence_folds_lora_delta_into_dit_weight_gb():
    dit, calls = _dit(estimated_vram_gb=19.52)
    dit.module = nn.Sequential(_linear_with_sized_delta((100, 50), (50, 100)))
    lora_gb = _dit_lora_delta_gb(dit)
    assert lora_gb > 0.0
    patches, manager = _patched(free_gb=100.0)  # plenty of room, isolate the weight_gb accounting
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    assert decision.lora_active is True
    assert decision.lora_weight_gb == pytest.approx(lora_gb)
    assert decision.dit_weight_gb == pytest.approx(19.52 + lora_gb)


def test_place_dit_for_sequence_without_lora_reports_zero_lora_weight_gb():
    dit, calls = _dit(estimated_vram_gb=19.52)
    patches, manager = _patched(free_gb=100.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    assert decision.lora_active is False
    assert decision.lora_weight_gb == 0.0
    assert decision.dit_weight_gb == pytest.approx(19.52)


# -- the real OOM trace: both fixes together flip resident -> partial --------

def _trace_dit(*, lora_weight_gb: float) -> Any:
    """A dit test double shaped like the real trace: 19.52GB base weight,
    LoRA active with `lora_weight_gb` of ACTUAL resident delta bytes (a real
    tensor, not mocked, sized exactly -- avoids allocating a full 1.4GB
    fixture while still exercising the real byte-summing code path)."""
    dit, calls = _dit(estimated_vram_gb=_TRACE_DIT_WEIGHT_GB)
    if lora_weight_gb > 0.0:
        # One down/up pair sized to land on lora_weight_gb exactly (fp32,
        # square matrices: 2*n*n*4 bytes total).
        n = int((lora_weight_gb * 1024 ** 3 / 8) ** 0.5)
        dit.module = nn.Sequential(_linear_with_sized_delta((n, n), (n, n)))
    else:
        dit.module = nn.Sequential(nn.Linear(4, 4))
    return dit, calls


def test_real_trace_inputs_resolve_to_partial_with_both_fixes():
    dit, calls = _trace_dit(lora_weight_gb=_TRACE_LORA_WEIGHT_GB)
    patches, manager = _patched(free_gb=_TRACE_FREE_GB)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_TRACE_VIDEO_TOKENS, audio_tokens=_TRACE_AUDIO_TOKENS,
            inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
        )
    assert decision.mode == "partial"
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1


def test_bite_check_without_ffn_dim_the_same_trace_wrongly_stays_resident():
    # BITE CHECK 1/2: reverting JUST the activation-reserve half of the fix
    # (drop ffn_dim) reproduces the ORIGINAL bug -- the exact scenario that
    # OOM'd on real hardware would still be placed fully resident.
    dit, calls = _trace_dit(lora_weight_gb=_TRACE_LORA_WEIGHT_GB)
    patches, manager = _patched(free_gb=_TRACE_FREE_GB)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_TRACE_VIDEO_TOKENS, audio_tokens=_TRACE_AUDIO_TOKENS,
            inner_dim=_H3_INNER_DIM,  # ffn_dim NOT passed
        )
    assert decision.mode == "resident"


def test_bite_check_without_lora_weight_correction_the_same_trace_wrongly_stays_resident():
    # BITE CHECK 2/2: reverting JUST the weight_gb half of the fix (no
    # resident LoRA delta on the module -- `estimated_vram_gb` alone) ALSO
    # reproduces the bug, even with the corrected activation reserve.
    # Confirms BOTH halves of the fix are independently load-bearing -- the
    # activation-reserve fix alone was not sufficient to flip this decision.
    dit, calls = _trace_dit(lora_weight_gb=0.0)  # no resident delta on the module
    patches, manager = _patched(free_gb=_TRACE_FREE_GB)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_TRACE_VIDEO_TOKENS, audio_tokens=_TRACE_AUDIO_TOKENS,
            inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
        )
    assert decision.mode == "resident"


# -- warm residency: a DiT the PRIOR generation already left resident must not
# be needlessly offloaded and re-streamed just because free_vram_gb() counts
# its own weight bytes as "used" rather than "available if kept". Real trace:
# mode=partial, weight_budget_gb=0.0, dit_weight_gb=21.27 (incl. 1.75 LoRA),
# activation_reserve=6.82, on a card with ~31GB genuinely free -- sampling
# alone took 147s of a 196s total because the warm DiT was streamed from host
# every step instead of staying put. ------------------------------------

from src.pipelines.pipes._shared.generation.dit_placement import _dit_is_fully_resident  # noqa: E402


def test_dit_is_fully_resident_true_when_device_matches_and_not_streaming():
    dit = SimpleNamespace(device="cuda")
    assert _dit_is_fully_resident(dit, "cuda") is True


def test_dit_is_fully_resident_false_with_no_device_set():
    dit = SimpleNamespace()
    assert _dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_false_on_a_different_device_type():
    dit = SimpleNamespace(device="cpu")
    assert _dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_true_across_cuda_ordinal_spelling():
    # "cuda" and "cuda:0" are the same device TYPE -- a warm restore that
    # landed on "cuda:0" must still be recognised against a bare "cuda" ask.
    dit = SimpleNamespace(device="cuda:0")
    assert _dit_is_fully_resident(dit, "cuda") is True


def test_dit_is_fully_resident_false_while_actively_streaming():
    # Partial residency (an active streamer) is EXCLUDED on purpose -- that
    # leaf split was sized for a different call and cannot be trusted without
    # recomputing; see the function's own docstring.
    streamer = SimpleNamespace(active=True)
    dit = SimpleNamespace(device="cuda", _streamer=streamer)
    assert _dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_true_with_an_inactive_streamer():
    # A streamer object exists (this DiT was streamed at some earlier point
    # in the process) but is not CURRENTLY active -- a later move_to() would
    # have made it fully resident again; must not be permanently excluded
    # just because a streamer object was ever constructed once.
    streamer = SimpleNamespace(active=False)
    dit = SimpleNamespace(device="cuda", _streamer=streamer)
    assert _dit_is_fully_resident(dit, "cuda") is True


def test_warm_resident_dit_that_still_fits_is_kept_without_move_or_stream():
    dit, calls = _dit(estimated_vram_gb=19.6)
    dit.device = "cuda"
    # As if 19.6GB of a ~31.4GB-total card is already used by dit's OWN
    # resident copy -- free_vram_gb() only reports what's left besides it.
    patches, manager = _patched(free_gb=11.8)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident"
    assert decision.kept_resident is True
    assert calls["move_to"] == []
    assert calls["stream_to"] == []
    assert calls["offload"] == 0
    assert decision.dit_weight_gb == 19.6


def test_bite_check_without_crediting_self_this_exact_scenario_would_wrongly_stream():
    # BITE CHECK: the SAME numbers as the "still fits" test above, but
    # computed the OLD way (free_vram_gb() alone, no credit for the DiT's own
    # resident bytes) -- proves the credit-back is actually load-bearing,
    # not a no-op: without it, this configuration would have concluded the
    # DiT no longer fits and streamed it, reproducing the reported bug.
    free_gb = 11.8
    total_reserve = 5.0 + _ACTIVATION_RESERVE_FLOOR_GB  # video_tokens=0 -> floor
    uncredited_budget = max(0.0, free_gb - total_reserve)
    assert uncredited_budget < 19.6, "expected the OLD (uncredited) computation to wrongly conclude 'doesn't fit'"


def test_warm_resident_dit_that_no_longer_fits_offloads_then_places_fresh():
    dit, calls = _dit(estimated_vram_gb=23.3)
    dit.device = "cuda"
    # Read 1 (fast-path check): only 2.0GB free -- 2.0+23.3=25.3 credited,
    # minus a 10.5GB reserve, doesn't clear 23.3 -> falls through and offloads.
    # Reads 2-3 (post-offload: _ensure_room_for's own read, then the main
    # measurement) both see the FULL 40.0GB the stale copy's release
    # genuinely freed.
    free_reads = iter([2.0, 40.0, 40.0])
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.free_vram_gb", side_effect=lambda device: next(free_reads)), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0), \
         patch(f"{_MOD}.get_residency_registry", return_value=manager):
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=10.0)
    assert calls["offload"] == 1, "expected the stale resident copy to be offloaded before re-measuring"
    assert decision.mode == "resident"
    assert decision.kept_resident is False  # a FRESH placement, not the fast-path skip
    assert calls["move_to"] == ["cuda"]


def test_warm_resident_dit_that_no_longer_fits_can_still_degrade_to_partial():
    dit, calls = _dit(estimated_vram_gb=23.3)
    dit.device = "cuda"
    # total_reserve = reserve_gb(10.0) + floor(0.5) = 10.5. Read 1: 2.0GB free
    # < 10.5 -> doesn't fit even credited -> offloads. Reads 2-3 (post-
    # offload): 5.0GB free -> weight_budget = max(0, 5.0-10.5) = 0.0, still
    # nowhere near 23.3 -> genuinely must degrade to partial, not force a
    # resident placement it cannot back.
    free_reads = iter([2.0, 5.0, 5.0])
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.free_vram_gb", side_effect=lambda device: next(free_reads)), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0), \
         patch(f"{_MOD}.get_residency_registry", return_value=manager):
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=10.0)
    assert calls["offload"] == 1
    assert decision.mode == "partial"
    assert decision.kept_resident is False
    assert len(calls["stream_to"]) == 1


def test_warm_residency_check_is_skipped_when_dit_has_no_device_attribute():
    # A cold-start dit (never placed anywhere) has no `.device` to compare --
    # must reach the ordinary path unchanged, not raise.
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert decision.kept_resident is False
    assert calls["move_to"] == ["cuda"]


# -- reserve_gb: extra headroom on top of the token-derived reserve (a caller
# whose post-placement GPU work isn't proportional
# to video_tokens, e.g. the detailer's per-tube VAE decode).


def test_reserve_gb_defaults_to_zero_no_behavior_change():
    """A tiny-token call with no reserve_gb behaves exactly as before: the
    activation reserve alone decides, and a comfortably-fitting DiT goes
    fully resident."""
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=28.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000)
    assert decision.mode == "resident"
    assert decision.extra_reserve_gb == 0.0
    assert calls["move_to"] == ["cuda"]


def test_reserve_gb_can_tip_a_tiny_token_placement_into_partial():
    """The exact round-2 shape: a tiny tube (a few thousand tokens)
    gets a near-floor activation reserve on its own, which alone would fit
    the DiT fully resident -- but a caller-supplied reserve_gb (this tube's
    upcoming VAE decode) must be able to tip that decision into partial so
    real headroom survives past placement."""
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=24.0)  # 24 - 23.3 = 0.7GB slack, thin
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=3.0)
    assert decision.mode == "partial"
    assert decision.extra_reserve_gb == 3.0
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1
    device, budget = calls["stream_to"][0]
    assert device == "cuda"
    # weight budget = free - activation_reserve(~floor) - reserve_gb(3.0)
    assert budget == pytest.approx(24.0 - decision.activation_reserve_gb - 3.0, abs=1e-6)


def test_reserve_gb_is_additive_with_the_activation_reserve():
    dit, _ = _dit(estimated_vram_gb=1.0)  # tiny DiT so both cases stay "resident"
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        without = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=0.0)
    with patches[0], patches[1], patches[2]:
        with_reserve = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=2.5)
    assert with_reserve.weight_budget_gb == pytest.approx(without.weight_budget_gb - 2.5)


def test_negative_reserve_gb_is_clamped_to_zero():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=28.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=-5.0)
    assert decision.extra_reserve_gb == 0.0
    assert decision.mode == "resident"


def test_cpu_device_decision_has_zero_extra_reserve_regardless_of_argument():
    dit, calls = _dit()
    decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000, reserve_gb=5.0)
    assert decision.mode == "cpu"
    assert decision.extra_reserve_gb == 0.0


# -- LoRA gating end to end (place_dit_for_sequence) ----------------------------

def test_placement_reports_lora_active_from_the_real_module():
    dit, _ = _dit_with_lora(estimated_vram_gb=23.3, active=True)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=50_000)
    assert decision.lora_active is True


def test_placement_reports_lora_inactive_without_deltas():
    dit, _ = _dit_with_lora(estimated_vram_gb=23.3, active=False)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=50_000)
    assert decision.lora_active is False


def test_lora_active_placement_reserves_more_than_inactive_at_the_same_tokens():
    tokens = 50_000
    patches, manager = _patched(free_gb=32.0)
    dit_off, _ = _dit_with_lora(estimated_vram_gb=1.0, active=False)  # tiny DiT: both stay resident
    with patches[0], patches[1], patches[2]:
        off = place_dit_for_sequence(dit_off, "cuda", video_tokens=tokens)
    dit_on, _ = _dit_with_lora(estimated_vram_gb=1.0, active=True)
    with patches[0], patches[1], patches[2]:
        on = place_dit_for_sequence(dit_on, "cuda", video_tokens=tokens)
    assert on.activation_reserve_gb > off.activation_reserve_gb
    assert on.weight_budget_gb < off.weight_budget_gb


def test_lora_active_can_tip_an_otherwise_resident_placement_into_partial():
    tokens = 50_000
    # Free VRAM sized to comfortably fit the no-LoRA reserve but not the
    # LoRA-active one -- the exact "under-reserved" scenario this fixes.
    without = estimate_activation_reserve_gb(tokens, lora_active=False)
    with_lora = estimate_activation_reserve_gb(tokens, lora_active=True)
    assert with_lora > without  # sanity: the gate must actually move the number
    dit_weight_gb = 20.0
    free_gb = dit_weight_gb + without + 0.05  # fits without LoRA, not with it

    patches, manager = _patched(free_gb=free_gb)
    dit_off, _ = _dit_with_lora(estimated_vram_gb=dit_weight_gb, active=False)
    with patches[0], patches[1], patches[2]:
        off = place_dit_for_sequence(dit_off, "cuda", video_tokens=tokens)
    assert off.mode == "resident"

    dit_on, _ = _dit_with_lora(estimated_vram_gb=dit_weight_gb, active=True)
    with patches[0], patches[1], patches[2]:
        on = place_dit_for_sequence(dit_on, "cuda", video_tokens=tokens)
    assert on.mode == "partial"


def test_cpu_device_lora_active_still_detected_but_reserve_stays_zero():
    dit, calls = _dit_with_lora(estimated_vram_gb=23.3, active=True)
    decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000)
    assert decision.mode == "cpu"
    assert decision.lora_active is True
    assert decision.activation_reserve_gb == 0.0


# -- torch.compile hook: place_dit_for_sequence gives LTX/DFR/MiniMax-H3 the
# same gated, reversible regional torch.compile the image path gets from
# NativeGenerator._maybe_compile, since these pipes have no NativeGenerator
# instance to call that private method on. ------------------------------------

class _CompilableBlocks(nn.Module):
    """Homogeneous ``blocks`` ModuleList -- a real compile_gate "ok" target."""

    def __init__(self, n: int = 2, dim: int = 4) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(nn.Linear(dim, dim) for _ in range(n))


def _dit_compilable(estimated_vram_gb=23.3):
    dit, calls = _dit(estimated_vram_gb)
    dit.module = _CompilableBlocks()
    dit.quant_format = None
    return dit, calls


def _enable_compile(monkeypatch):
    from src.platform.runtime.native.optimizations import compile as tc

    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "on")
    return tc


def test_resident_placement_compiles_when_enabled(monkeypatch):
    tc = _enable_compile(monkeypatch)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert dit._compiled is not None and dit._compiled.active
    assert all(tc.is_compiled(b) for b in dit.module.blocks)


def test_partial_placement_never_compiles(monkeypatch):
    _enable_compile(monkeypatch)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert decision.mode == "partial"
    assert getattr(dit, "_compiled", None) is None


def test_compile_disabled_by_default_leaves_dit_untouched(monkeypatch):
    from src.platform.runtime.native.optimizations import compile as tc

    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert getattr(dit, "_compiled", None) is None


def test_warm_resident_fast_path_also_compiles(monkeypatch):
    tc = _enable_compile(monkeypatch)
    dit, calls = _dit_compilable(estimated_vram_gb=19.6)
    dit.device = "cuda"
    patches, _manager = _patched(free_gb=11.8)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident" and decision.kept_resident is True
    assert calls["move_to"] == [] and calls["stream_to"] == []
    assert dit._compiled is not None and dit._compiled.active
    assert all(tc.is_compiled(b) for b in dit.module.blocks)
