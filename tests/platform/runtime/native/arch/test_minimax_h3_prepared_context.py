"""Tests for ``MiniMaxH3Model.prepare_text_context`` / ``PreparedTextContext``.

``condition_proj`` + ``token_refiner`` (``_prepare_context``) depend only on
``encoder_hidden_states`` -- no timestep, no AdaLN, no rotary -- so a window's
whole step loop can compute the result once and replay it. These tests prove:
parity between a hoisted and a direct compute, that ``forward`` actually
SKIPS ``condition_proj``/``token_refiner`` when a valid token is handed in,
that a stale token (different prompt tensor, different weight revision,
mismatched dtype/device) is never reused, and that the cached source tensor
is not mutated by the joint block stack.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.minimax_h3.model import (
    MiniMaxH3Model,
    PreparedTextContext,
)
from src.platform.runtime.native.cache_identity import UNIDENTIFIABLE

from .test_minimax_h3_model import (
    TINY_FULL,
    TINY_PRUNED,
    _build_ready,
    _count_calls,
    _fbcache_forward,
    _fbcache_inputs,
    _tiny_layout,
)


def _forward_with(m: MiniMaxH3Model, layout: dict, inputs: dict, timestep: torch.Tensor, **kwargs):
    return _fbcache_forward(m, layout, inputs, timestep, **kwargs)


# --- parity -------------------------------------------------------------

def test_prepare_text_context_matches_direct_prepare_context():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    with torch.inference_mode():
        direct = m._prepare_context(inputs["encoder_hidden_states"])
        prepared = m.prepare_text_context(inputs["encoder_hidden_states"])
    assert torch.equal(prepared.text_embeds, direct)


def test_forward_with_prepared_context_matches_uncached_forward_full_mode():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=1)
    for ts in (torch.tensor([0.1, 0.4]), torch.tensor([0.6, 0.9]), torch.tensor([0.0, 1.0])):
        baseline = _forward_with(m, layout, inputs, ts)
        hoisted = _forward_with(m, layout, inputs, ts, prepared_context=prepared, weight_revision=1)
        for a, b in zip(baseline, hoisted):
            torch.testing.assert_close(a, b)


def test_forward_with_prepared_context_matches_uncached_forward_pruned_mode():
    m = _build_ready(TINY_PRUNED)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_PRUNED, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=1)
    for ts in (torch.tensor([0.1, 0.4]), torch.tensor([0.6, 0.9])):
        baseline = _forward_with(m, layout, inputs, ts)
        hoisted = _forward_with(m, layout, inputs, ts, prepared_context=prepared, weight_revision=1)
        for a, b in zip(baseline, hoisted):
            torch.testing.assert_close(a, b)


# --- call-count: the actual hoist ----------------------------------------

def test_forward_with_valid_prepared_context_never_calls_condition_proj_or_token_refiner():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=7)

    condition_proj_calls = _count_calls(m.condition_proj)
    token_refiner_calls = _count_calls(m.token_refiner)

    for step, ts in enumerate((torch.tensor([0.1, 0.9]),) * 4):
        _forward_with(m, layout, inputs, ts, prepared_context=prepared, weight_revision=7)
    assert condition_proj_calls["n"] == 0
    assert token_refiner_calls["n"] == 0


def test_bite_check_forward_without_prepared_context_calls_both_every_step():
    # BITE CHECK: the assertion above is not vacuous -- the baseline (no
    # `prepared_context`) really does call both modules on every step.
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)

    condition_proj_calls = _count_calls(m.condition_proj)
    token_refiner_calls = _count_calls(m.token_refiner)

    steps = 4
    for _ in range(steps):
        _forward_with(m, layout, inputs, torch.tensor([0.1, 0.9]))
    assert condition_proj_calls["n"] == steps
    assert token_refiner_calls["n"] == steps


# --- staleness: never reuse a token that no longer applies ----------------

def test_forward_recomputes_when_the_prompt_tensor_changes():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs_a = _fbcache_inputs(TINY_FULL, layout)
    prepared_a = m.prepare_text_context(inputs_a["encoder_hidden_states"], weight_revision=1)

    condition_proj_calls = _count_calls(m.condition_proj)
    inputs_b = _fbcache_inputs(TINY_FULL, layout)  # a fresh, different prompt tensor
    ts = torch.tensor([0.2, 0.8])

    with_stale_context = _forward_with(m, layout, inputs_b, ts, prepared_context=prepared_a, weight_revision=1)
    assert condition_proj_calls["n"] == 1  # recomputed, not reused

    direct_for_b = _forward_with(m, layout, inputs_b, ts)
    for a, b in zip(with_stale_context, direct_for_b):
        torch.testing.assert_close(a, b)  # correctness: b's own text, not a's


def test_forward_recomputes_when_the_weight_revision_changes():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=1)

    condition_proj_calls = _count_calls(m.condition_proj)
    ts = torch.tensor([0.2, 0.8])
    _forward_with(m, layout, inputs, ts, prepared_context=prepared, weight_revision=2)  # adapter changed
    assert condition_proj_calls["n"] == 1


def test_forward_reuses_when_the_weight_revision_matches():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=1)

    condition_proj_calls = _count_calls(m.condition_proj)
    ts = torch.tensor([0.2, 0.8])
    _forward_with(m, layout, inputs, ts, prepared_context=prepared, weight_revision=1)
    assert condition_proj_calls["n"] == 0


def test_forward_recomputes_with_no_prepared_context_at_all():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    condition_proj_calls = _count_calls(m.condition_proj)
    _forward_with(m, layout, inputs, torch.tensor([0.2, 0.8]))
    assert condition_proj_calls["n"] == 1


# --- PreparedTextContext.is_stale, unit-level ------------------------------

def test_is_stale_false_for_the_exact_tensor_it_was_built_from():
    x = torch.randn(1, 3, 10)
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=None, weight_revision=1)
    from src.platform.runtime.native.cache_identity import tensor_identity
    ctx.source_identity = tensor_identity(x)
    assert ctx.is_stale(x, 1, text_embeds.dtype, text_embeds.device) is False


def test_is_stale_true_for_a_different_tensor_object_with_equal_values():
    x = torch.zeros(1, 3, 10)
    x_other = torch.zeros(1, 3, 10)  # same values, different storage
    from src.platform.runtime.native.cache_identity import tensor_identity
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=tensor_identity(x), weight_revision=1)
    assert ctx.is_stale(x_other, 1, text_embeds.dtype, text_embeds.device) is True


def test_is_stale_true_after_an_in_place_write_to_the_source_tensor():
    from src.platform.runtime.native.cache_identity import tensor_identity
    x = torch.randn(1, 3, 10)
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=tensor_identity(x), weight_revision=1)
    assert ctx.is_stale(x, 1, text_embeds.dtype, text_embeds.device) is False
    x.mul_(2.0)  # bump the version counter in place -- same object, different content
    assert ctx.is_stale(x, 1, text_embeds.dtype, text_embeds.device) is True


def test_is_stale_true_for_a_mismatched_weight_revision():
    from src.platform.runtime.native.cache_identity import tensor_identity
    x = torch.randn(1, 3, 10)
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=tensor_identity(x), weight_revision=1)
    assert ctx.is_stale(x, 2, text_embeds.dtype, text_embeds.device) is True


def test_is_stale_true_for_a_mismatched_dtype():
    from src.platform.runtime.native.cache_identity import tensor_identity
    x = torch.randn(1, 3, 10)
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=tensor_identity(x), weight_revision=1)
    assert ctx.is_stale(x, 1, torch.float16, text_embeds.device) is True


def test_is_stale_true_for_a_mismatched_device():
    from src.platform.runtime.native.cache_identity import tensor_identity
    x = torch.randn(1, 3, 10)
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=tensor_identity(x), weight_revision=1)
    # No CUDA needed: `text_embeds` never actually moves, `is_stale` only
    # compares against the device it is asked about.
    assert ctx.is_stale(x, 1, text_embeds.dtype, torch.device("meta")) is True


def test_is_stale_true_when_the_source_identity_is_unidentifiable():
    text_embeds = torch.randn(1, 3, 64)
    ctx = PreparedTextContext(text_embeds=text_embeds, source_identity=UNIDENTIFIABLE, weight_revision=1)
    x = torch.randn(1, 3, 10)
    assert ctx.is_stale(x, 1, text_embeds.dtype, text_embeds.device) is True


# --- the cached source tensor is never mutated by the joint blocks --------

def test_prepared_text_embeds_unmutated_across_repeated_forward_calls():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    inputs = _fbcache_inputs(TINY_FULL, layout)
    prepared = m.prepare_text_context(inputs["encoder_hidden_states"], weight_revision=1)
    before = prepared.text_embeds.clone()
    version_before = prepared.text_embeds._version

    for _ in range(3):
        _forward_with(m, layout, inputs, torch.tensor([0.2, 0.8]), prepared_context=prepared, weight_revision=1)

    assert torch.equal(prepared.text_embeds, before)
    assert prepared.text_embeds._version == version_before
