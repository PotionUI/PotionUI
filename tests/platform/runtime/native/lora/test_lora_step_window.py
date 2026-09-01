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

def test_hook_patches_only_inside_the_window():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(module, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])

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
    hook = LoraStepWindowHook(module, [(_kohya_lora(), 1.0, LoraStepWindow(3, 4))])

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
    hook = LoraStepWindowHook(module, [(_kohya_lora(), 1.0, LoraStepWindow(1, 6))])

    hook.on_start(8)
    hook.on_step(0, 8, None, 0.0, None)
    assert _is_patched(module, baseline), "sanity: the run died while the LoRA was on"

    hook.close()
    assert torch.allclose(_target_weight(module), baseline, atol=1e-6)


def test_close_is_idempotent():
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(module, [(_kohya_lora(), 1.0, LoraStepWindow(1, 2))])
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

    hook = LoraStepWindowHook(module, [(_kohya_lora(seed=3), 1.0, LoraStepWindow(1, 2))])
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
    hook = LoraStepWindowHook(module, [
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
    hook = LoraStepWindowHook(module, [])
    hook.on_start(8)
    for i in range(8):
        hook.on_step(i, 8, None, 0.0, None)
    hook.on_end()
    assert torch.equal(_target_weight(module), baseline)


def test_window_starting_past_the_run_warns_and_never_applies(caplog):
    module = _build()
    baseline = _target_weight(module).clone()
    hook = LoraStepWindowHook(module, [(_kohya_lora(), 1.0, LoraStepWindow(20, 24))])
    with caplog.at_level("WARNING"):
        hook.on_start(8)
    assert "never apply" in caplog.text
    for i in range(8):
        hook.on_step(i, 8, None, 0.0, None)
    assert torch.equal(_target_weight(module), baseline)
    hook.close()
