"""Step-windowed LoRA: window math, and the hook's apply/remove at step edges.

The hook is exercised against a REAL tiny Flux DiT patched by a REAL kohya LoRA
(same fixtures as ``test_lora.py``), so "applied" and "removed" are checked by
reading the weight tensor rather than by counting calls on a mock.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora import apply_loras
from src.platform.runtime.native.lora.step_window import (
    LoraStepWindow,
    LoraStepWindowHook,
    has_lora_window,
    parse_lora_window,
)
from vendor.gpl.comfyui.ops import pick_operations

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
TARGET = "double_blocks.0.img_attn.qkv"


def _build():
    m = Flux.from_config(TINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".scale") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.05
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _kohya_lora(rank=4, seed=1, scale=0.1):
    g = torch.Generator().manual_seed(seed)
    stem = "lora_unet_double_blocks_0_img_attn_qkv"
    return {
        f"{stem}.lora_up.weight": torch.randn(192, rank, generator=g) * scale,
        f"{stem}.lora_down.weight": torch.randn(rank, 64, generator=g) * scale,
        f"{stem}.alpha": torch.tensor(float(rank)),
    }


def _target_weight(module):
    return dict(module.named_modules())[TARGET].weight


def _is_patched(module, baseline) -> bool:
    """Tolerance, not equality: an in-place remove restores to ~1 ulp of storage
    rounding rather than bit-identically (see ``remove_loras``' docstring), so
    only a difference far above that noise floor counts as "still patched"."""
    return not torch.allclose(_target_weight(module), baseline, atol=1e-6)


# --- window math ---------------------------------------------------------

def test_first_n_steps_is_start_1_end_n():
    """The motivating model card's "first 2 of the 8 denoise steps"."""
    window = LoraStepWindow(start=1, end=2)
    assert [window.contains(i) for i in range(8)] == [True, True, False, False, False, False, False, False]


def test_open_ended_window_stays_on_to_the_end():
    window = LoraStepWindow(start=5, end=None)
    assert [window.contains(i) for i in range(8)] == [False] * 4 + [True] * 4


def test_mid_run_window_is_inclusive_on_both_ends():
    window = LoraStepWindow(start=3, end=5)
    assert [i for i in range(8) if window.contains(i)] == [2, 3, 4]


@pytest.mark.parametrize("kwargs", [
    {"start": 0},              # 0-based start is the classic off-by-one
    {"start": -1},
    {"start": 1, "end": 0},
    {"start": 4, "end": 3},    # end before start = permanently off
])
def test_invalid_windows_raise(kwargs):
    with pytest.raises(ValueError):
        LoraStepWindow(**kwargs)


def test_parse_reads_both_keys_and_defaults_start_to_one():
    assert parse_lora_window({"step_start": 2, "step_end": 6}) == LoraStepWindow(2, 6)
    assert parse_lora_window({"step_end": 2}) == LoraStepWindow(1, 2)
    assert parse_lora_window({"step_start": 3}) == LoraStepWindow(3, None)


def test_parse_treats_absent_and_blank_as_unwindowed():
    for entry in ({}, {"step_start": None, "step_end": None}, {"step_start": "", "step_end": ""}):
        assert parse_lora_window(entry) is None
        assert has_lora_window(entry) is False


def test_parse_accepts_numeric_strings_from_form_json():
    assert parse_lora_window({"step_start": "1", "step_end": "2"}) == LoraStepWindow(1, 2)


def test_parse_rejects_a_non_numeric_window_rather_than_dropping_it():
    with pytest.raises(ValueError, match="step_end"):
        parse_lora_window({"step_end": "two"})


# --- hook: real weights at real step edges -------------------------------

def _dit(module) -> NativeModel:
    """Wrap a bare module the way the generator hands one to the hook."""
    return NativeModel("diffusion_model", module)



def test_hook_patches_only_inside_the_window():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])

    hook.on_start(8)
    assert _is_patched(module, baseline), "window opens at step 1: must be patched entering step 0"

    hook.on_step(0, 8, None, 0.0, None)
    assert _is_patched(module, baseline), "still inside the window entering step 1"

    hook.on_step(1, 8, None, 0.0, None)
    assert not _is_patched(module, baseline), "window closed after step 2: must be unpatched"

    for i in range(2, 7):
        hook.on_step(i, 8, None, 0.0, None)
        assert not _is_patched(module, baseline)
    hook.on_end()
    assert torch.allclose(_target_weight(module), baseline, atol=1e-6)


def test_hook_opens_a_late_window_at_the_right_step():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(), 1.0, LoraStepWindow(3, 4))])

    hook.on_start(8)
    assert not _is_patched(module, baseline)
    hook.on_step(0, 8, None, 0.0, None)
    assert not _is_patched(module, baseline), "entering step 1, window starts at step 3"
    hook.on_step(1, 8, None, 0.0, None)
    assert _is_patched(module, baseline), "entering step 2 (0-based) = step 3 (1-based)"
    hook.on_step(3, 8, None, 0.0, None)
    assert not _is_patched(module, baseline), "window ended after step 4"
    hook.close()


def test_close_restores_the_weights_when_the_run_dies_mid_window():
    """A cancel or an error inside the window must not leave the SHARED, cached
    model patched — this is the cache-poisoning guarantee."""
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(), 1.0, LoraStepWindow(1, 6))])

    hook.on_start(8)
    hook.on_step(0, 8, None, 0.0, None)
    assert _is_patched(module, baseline), "sanity: the run died while the LoRA was on"

    hook.close()
    assert torch.allclose(_target_weight(module), baseline, atol=1e-6)


def test_close_is_idempotent():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])
    hook.on_start(8)
    hook.close()
    hook.close()
    assert torch.allclose(_target_weight(module), baseline, atol=1e-6)


def test_a_loader_baked_lora_survives_the_window_hook():
    """The hook restores to a snapshot taken AFTER the loader baked the
    always-on stack, so closing a window must not strip that stack too."""
    module = _build()
    bare = _target_weight(module).clone()
    apply_loras(module, [(_kohya_lora(seed=7), 0.9)])
    baked = _target_weight(module).clone()
    assert not torch.equal(baked, bare)

    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(seed=3), 1.0, LoraStepWindow(1, 2))])
    hook.on_start(8)
    hook.on_step(1, 8, None, 0.0, None)
    hook.close()
    assert torch.allclose(_target_weight(module), baked, atol=1e-6), \
        "the baked stack must still be applied after the window closed"


def test_overlapping_windows_each_leave_at_their_own_edge():
    module = _build()
    baseline = _target_weight(module).clone()
    a = _kohya_lora(seed=11)
    b = _kohya_lora(seed=12)
    hook = LoraStepWindowHook(_dit(module), [
        (a, 1.0, LoraStepWindow(1, 4)),
        (b, 1.0, LoraStepWindow(3, 6)),
    ])

    def expected(stack):
        m = _build_from(baseline)
        apply_loras(m, stack)
        return _target_weight(m).clone()

    hook.on_start(8)
    assert torch.allclose(_target_weight(module), expected([(a, 1.0)]), atol=1e-5)
    hook.on_step(1, 8, None, 0.0, None)  # entering step 3: both on
    assert torch.allclose(_target_weight(module), expected([(a, 1.0), (b, 1.0)]), atol=1e-5)
    hook.on_step(3, 8, None, 0.0, None)  # entering step 5: only b
    assert torch.allclose(_target_weight(module), expected([(b, 1.0)]), atol=1e-5)
    hook.on_step(5, 8, None, 0.0, None)  # entering step 7: neither
    assert torch.allclose(_target_weight(module), baseline, atol=1e-5)
    hook.close()


def _build_from(baseline):
    """A fresh module whose target weight is ``baseline`` — the reference the
    overlap test compares the hook's in-place arithmetic against."""
    module = _build()
    with torch.no_grad():
        _target_weight(module).copy_(baseline)
    return module


def test_no_windowed_loras_is_a_pure_noop():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [])
    hook.on_start(8)
    for i in range(8):
        hook.on_step(i, 8, None, 0.0, None)
    hook.on_end()
    assert torch.equal(_target_weight(module), baseline)


def test_window_starting_past_the_run_warns_and_never_applies(caplog):
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(_dit(module), [(_kohya_lora(), 1.0, LoraStepWindow(20, 24))])
    with caplog.at_level("WARNING"):
        hook.on_start(8)
    assert "never apply" in caplog.text
    for i in range(8):
        hook.on_step(i, 8, None, 0.0, None)
    assert torch.equal(_target_weight(module), baseline)
    hook.close()


# --- effective-weight identity across a window edge -----------------------

def _cached_forward(cache, dit, module):
    """Cache one weight-dependent value under the DiT's live revision, as an arch does."""
    key = ("windowed.probe", cache.revision)
    hit = cache.get(key)
    if hit is not None:
        return hit, True
    value = _target_weight(module).sum().item()
    cache.put(key, value)
    return value, False


def test_a_cached_value_cannot_survive_a_window_edge():
    """A RunCache entry computed before a window applies must not answer after it.

    The value here IS the patched weight, so a stale hit is a wrong answer, not
    just a stale one.
    """
    from src.platform.runtime.native.engine import RunCache

    module = _build()
    dit = _dit(module)
    cache = RunCache(lambda: dit.effective_revision)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(3, 4))])

    hook.on_start(6)
    before, hit = _cached_forward(cache, dit, module)
    assert hit is False
    assert _cached_forward(cache, dit, module) == (before, True)  # same weights, reused

    hook.on_step(1, 6, None, 0.0, None)  # entering step 2 (0-based) = window opens
    after, hit = _cached_forward(cache, dit, module)
    assert hit is False, "the window applied: the cached value must not answer"
    assert after != before

    hook.on_step(3, 6, None, 0.0, None)  # entering step 4 = window closes
    restored, hit = _cached_forward(cache, dit, module)
    assert hit is False, "the window restored: still a fresh epoch, correctness first"
    assert restored == pytest.approx(before)


def test_close_leaves_a_fresh_epoch_rather_than_the_pre_apply_revision():
    module = _build()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 4))])
    start = dit.effective_revision

    hook.on_start(4)
    applied = dit.effective_revision
    hook.close()

    assert applied > start
    assert dit.effective_revision > applied


def test_an_unused_window_never_moves_either_revision():
    module = _build()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(20, 24))])
    effective, weights = dit.effective_revision, dit.weight_revision

    hook.on_start(4)
    for step in range(4):
        hook.on_step(step, 4, None, 0.0, None)
    hook.close()

    assert (dit.effective_revision, dit.weight_revision) == (effective, weights)


def test_a_failed_apply_forces_the_cross_run_identity_forward(monkeypatch):
    """A half-patched module is no longer the base stack the loader stamped."""
    import src.platform.runtime.native.lora.step_window as step_window

    module = _build()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])
    weights = dit.weight_revision

    def _boom(module, stack):
        raise RuntimeError("half-patched")

    monkeypatch.setattr(step_window, "apply_loras", _boom)
    with pytest.raises(RuntimeError, match="half-patched"):
        hook.on_start(4)

    assert dit.weight_revision > weights


def test_the_success_path_never_moves_the_cross_run_identity():
    """Warm-start keys on weight_revision, so a window crossing must not move it."""
    module = _build()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(2, 3))])
    weights = dit.weight_revision

    hook.on_start(6)
    for step in range(5):
        hook.on_step(step, 6, None, 0.0, None)
    hook.close()

    assert dit.weight_revision == weights


def test_a_full_window_cycle_leaves_the_trajectory_key_reusable():
    """The point of the two-level split: warm reuse survives a windowed run.

    Bumping ``weight_revision`` per apply/restore would make every run cold.
    """
    from types import SimpleNamespace

    from src.platform.runtime.native.engine import NativeGenerator
    from src.platform.runtime.native.sampling.trajectory_cache import get_trajectory_cache

    module = _build()
    dit = _dit(module)
    gen = SimpleNamespace(spec=SimpleNamespace(family="flux", variant="dev"), dit=dit)
    cond = {"context": torch.randn(1, 4, 8)}

    def plan():
        resume, _ = NativeGenerator._plan_warm_start(
            gen, True, "euler", None, cond, None, 1234, (1, 4, 4, 4), 6, None, (),
            {"guidance": None, "shift": 2.02}, {}, None, None, sigmas=None,
        )
        return resume[0] if resume is not None else None

    cache = get_trajectory_cache()
    cache.clear()
    try:
        assert plan() is None  # cold first run
        cache.get(next(iter(cache._entries))).checkpoints = {4: torch.full((1, 4, 4, 4), 2.0)}

        hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])
        hook.on_start(6)
        for step in range(5):
            hook.on_step(step, 6, None, 0.0, None)
        hook.close()

        assert plan() == 4, "an unchanged schedule must still resume after a windowed run"
    finally:
        cache.clear()


# --- a restore that fails partway through ---------------------------------

def _half_restore(leaf):
    """Stand-in for ``restore_lora_state``: moves ONE leaf, then dies.

    That is the shape of a real partial restore — ``_restore_linear_states``
    walks Linears one at a time — and it leaves a module that is neither the
    patched nor the base state. ``leaf`` resolves the tensor to corrupt at raise
    time, since applying a LoRA can rebind ``weight.data``.
    """
    def _boom(_snapshot):
        with torch.no_grad():
            leaf().add_(1.0)
        raise RuntimeError("restore died after one leaf")

    return _boom


def test_a_partial_restore_cannot_leave_a_usable_cached_value(monkeypatch):
    """Invalidation must precede the restore, not follow it.

    The sampler isolates hook exceptions, so the forward right after this failure
    still runs and still reads the run cache.
    """
    import src.platform.runtime.native.lora.step_window as step_window
    from src.platform.runtime.native.engine import RunCache

    module = _build()
    dit = _dit(module)
    cache = RunCache(lambda: dit.effective_revision)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])

    hook.on_start(6)
    patched, hit = _cached_forward(cache, dit, module)
    assert hit is False
    assert _cached_forward(cache, dit, module) == (patched, True)

    monkeypatch.setattr(step_window, "restore_lora_state",
                        _half_restore(lambda: _target_weight(module)))
    with pytest.raises(RuntimeError, match="restore died"):
        hook.on_step(1, 6, None, 0.0, None)  # entering step 2: the window closes

    value, hit = _cached_forward(cache, dit, module)
    assert hit is False, "a half-restored module must not answer from the patched epoch"
    assert value != patched


def test_a_partial_restore_makes_the_trajectory_key_unreusable(monkeypatch):
    import src.platform.runtime.native.lora.step_window as step_window

    module = _build()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])
    weights = dit.weight_revision

    hook.on_start(6)
    monkeypatch.setattr(step_window, "restore_lora_state",
                        _half_restore(lambda: _target_weight(module)))
    with pytest.raises(RuntimeError, match="restore died"):
        hook.on_step(1, 6, None, 0.0, None)

    assert dit.weight_revision > weights


def test_a_failed_close_is_retried_rather_than_reported_successful(monkeypatch):
    """A raising close must not mark itself done; the next call finishes the job.

    The corrupted leaf here is one the LoRA does not target, so the retry's own
    restore math is observable: the windowed patch really does come off.
    """
    import src.platform.runtime.native.lora.step_window as step_window

    module = _build()
    baseline = _target_weight(module).clone()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 4))])

    hook.on_start(4)
    assert _is_patched(module, baseline)

    monkeypatch.setattr(step_window, "restore_lora_state",
                        _half_restore(lambda: module.img_in.weight.data))
    with pytest.raises(RuntimeError, match="restore died"):
        hook.close()
    assert _is_patched(module, baseline), "the failed restore did not put the module back"

    monkeypatch.undo()
    hook.close()  # the retry the failure left open
    assert not _is_patched(module, baseline)


def test_a_close_that_fails_twice_poisons_the_wrapper_so_the_next_request_reloads(monkeypatch):
    """The end of the retry chain: nothing else will ever come back for it.

    ``on_end`` closes and the generator's ``finally`` closes again, so one
    failure is retried. When the retry fails too the windowed patches stay in a
    module the loader's stamp calls the bare base stack — and that module is
    shared through the MODELS cache. It must be refused there, not sampled.
    """
    import src.platform.runtime.native.lora.step_window as step_window
    from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle

    module = _build()
    baseline = _target_weight(module).clone()
    dit = _dit(module)

    models = ModelLifecycle(gpu_monitor=None, settings=None)
    key, fingerprint = "native/dit/tiny.safetensors", "tiny.safetensors|float32"
    loads = []

    def _load():
        loads.append(1)
        return dit if len(loads) == 1 else _dit(_build())

    assert models.acquire(key, fingerprint, _load) is dit
    assert models.acquire(key, fingerprint, _load) is dit, "control: a healthy entry is reused"

    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 4))])
    hook.on_start(4)
    assert _is_patched(module, baseline)

    monkeypatch.setattr(step_window, "restore_lora_state",
                        _half_restore(lambda: module.img_in.weight.data))
    with pytest.raises(RuntimeError, match="restore died"):
        hook.close()  # on_end's attempt, which the sampler swallows
    assert dit.unusable_reason is None, "one failure is still retryable"

    with pytest.raises(RuntimeError, match="restore died"):
        hook.close()  # the generator's finally: the retry
    assert dit.unusable_reason is not None

    assert _is_patched(module, baseline), "sanity: the poisoned module really is still patched"
    fresh = models.acquire(key, fingerprint, _load)
    assert fresh is not dit, "the next request must never be handed the poisoned wrapper"
    assert len(loads) == 2, "it must be a real reload, not a hit"


def test_a_retried_close_that_succeeds_leaves_the_wrapper_usable(monkeypatch):
    """The repair path f932b13 exists for: a retry that works must not poison."""
    import src.platform.runtime.native.lora.step_window as step_window

    module = _build()
    baseline = _target_weight(module).clone()
    dit = _dit(module)
    hook = LoraStepWindowHook(dit, [(_kohya_lora(), 1.0, LoraStepWindow(1, 4))])
    hook.on_start(4)

    monkeypatch.setattr(step_window, "restore_lora_state",
                        _half_restore(lambda: module.img_in.weight.data))
    with pytest.raises(RuntimeError, match="restore died"):
        hook.close()

    monkeypatch.undo()
    hook.close()
    assert dit.unusable_reason is None
    assert not _is_patched(module, baseline)
