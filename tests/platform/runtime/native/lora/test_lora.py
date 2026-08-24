"""Tests for native Flux LoRA key mapping + runtime application."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.lora.apply import (
    _needs_runtime_deltas,
    apply_loras,
    remove_loras,
    temporarily_applied_loras,
)
from src.platform.runtime.native.lora.key_mapping import (
    LoraDelta,
    build_flux_lora_key_map,
    map_lora_keys,
)
from vendor.gpl.comfyui.ops import apply_lora_deltas, pick_operations

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
HIDDEN = 64


def _build(ops=None):
    ops = ops or pick_operations(torch.float32, torch.float32)
    m = Flux.from_config(TINY, ops)
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


def _kohya_lora(stem="lora_unet_double_blocks_0_img_attn_qkv", out=192, inf=64, rank=4, seed=1):
    g = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_up.weight": torch.randn(out, rank, generator=g) * 0.1,
        f"{stem}.lora_down.weight": torch.randn(rank, inf, generator=g) * 0.1,
        f"{stem}.alpha": torch.tensor(float(rank)),
    }


def _diffusers_qkv_lora(rank=4, inf=64, seed=2):
    g = torch.Generator().manual_seed(seed)
    sd = {}
    for proj in ("to_q", "to_k", "to_v"):
        pfx = f"transformer.transformer_blocks.0.attn.{proj}"
        sd[f"{pfx}.lora_A.weight"] = torch.randn(rank, inf, generator=g) * 0.1
        sd[f"{pfx}.lora_B.weight"] = torch.randn(HIDDEN, rank, generator=g) * 0.1
    return sd


# --- key map -------------------------------------------------------------

def test_key_map_covers_all_dialects():
    km = build_flux_lora_key_map(_build())
    qkv = "double_blocks.0.img_attn.qkv.weight"
    assert km["lora_unet_double_blocks_0_img_attn_qkv"] == (qkv, None)
    assert km["diffusion_model.double_blocks.0.img_attn.qkv"] == (qkv, None)
    assert km["double_blocks.0.img_attn.qkv"] == (qkv, None)
    # diffusers split -> fused qkv row-slices
    assert km["transformer.transformer_blocks.0.attn.to_q"] == (qkv, (0, 0, HIDDEN))
    assert km["transformer.transformer_blocks.0.attn.to_k"] == (qkv, (0, HIDDEN, HIDDEN))
    assert km["transformer.transformer_blocks.0.attn.to_v"] == (qkv, (0, HIDDEN * 2, HIDDEN))
    # single-block fused linear1 (qkv + mlp)
    assert km["transformer.single_transformer_blocks.0.proj_mlp"] == (
        "single_blocks.0.linear1.weight", (0, HIDDEN * 3, HIDDEN * 4))


def test_key_map_skips_absent_flux2_targets():
    """img_mod does not exist on Flux2 (shared modulation) -> not registered."""
    km = build_flux_lora_key_map(_build())
    assert "transformer.transformer_blocks.0.norm1.linear" not in km


# --- map_lora_keys -------------------------------------------------------

def test_map_kohya_single_delta():
    mapped, unmatched = map_lora_keys(_kohya_lora(), _build())
    assert unmatched == []
    assert list(mapped) == ["double_blocks.0.img_attn.qkv.weight"]
    (delta,) = mapped["double_blocks.0.img_attn.qkv.weight"]
    assert delta.target_slice is None
    assert delta.alpha == 4.0 and delta.down.shape[0] == 4


def test_map_diffusers_three_sliced_deltas():
    mapped, unmatched = map_lora_keys(_diffusers_qkv_lora(), _build())
    assert unmatched == []
    deltas = mapped["double_blocks.0.img_attn.qkv.weight"]
    assert len(deltas) == 3
    assert {d.target_slice for d in deltas} == {
        (0, 0, HIDDEN), (0, HIDDEN, HIDDEN), (0, HIDDEN * 2, HIDDEN)}


def test_map_missing_alpha_defaults_to_rank():
    lora = _kohya_lora()
    del lora["lora_unet_double_blocks_0_img_attn_qkv.alpha"]
    mapped, _ = map_lora_keys(lora, _build())
    (delta,) = mapped["double_blocks.0.img_attn.qkv.weight"]
    assert delta.alpha == float(delta.down.shape[0])  # scale == 1


def test_map_reports_unmatched():
    lora = {
        "lora_unet_totally_bogus_layer.lora_up.weight": torch.randn(8, 4),
        "lora_unet_totally_bogus_layer.lora_down.weight": torch.randn(4, 8),
        "some_stray_tensor.weight": torch.randn(2, 2),
    }
    mapped, unmatched = map_lora_keys(lora, _build())
    assert mapped == {}
    assert "lora_unet_totally_bogus_layer" in unmatched


# --- delta math ----------------------------------------------------------

def test_apply_lora_deltas_matches_manual_math():
    base = torch.randn(192, 64)
    rank = 4
    up, down = torch.randn(192, rank), torch.randn(rank, 64)
    d = LoraDelta(down=down, up=up, alpha=8.0, scale=2.0)
    got = apply_lora_deltas(base, [d])
    expected = base + (up @ down) * (2.0 * 8.0 / rank)
    assert torch.allclose(got, expected, atol=1e-5)


def test_apply_lora_deltas_slice_only_touches_slice():
    base = torch.zeros(192, 64)
    up, down = torch.ones(64, 2), torch.ones(2, 64)
    d = LoraDelta(down=down, up=up, alpha=2.0, scale=1.0, target_slice=(0, 64, 64))
    got = apply_lora_deltas(base, [d])
    assert got[:64].abs().sum() == 0        # q rows untouched
    assert got[64:128].abs().sum() > 0      # k rows patched
    assert got[128:].abs().sum() == 0       # v rows untouched


# --- standard (in-place) apply / remove ----------------------------------

def test_apply_changes_forward_and_remove_restores():
    m = _build()
    x, t, ctx = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)
    with torch.no_grad():
        base = m(x, t, ctx)
    n, unmatched = apply_loras(m, [(_kohya_lora(), 1.0)])
    assert n == 1 and unmatched == []
    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base, after)
    remove_loras(m)
    with torch.no_grad():
        restored = m(x, t, ctx)
    assert torch.allclose(base, restored, atol=1e-5)


def test_strength_scales_delta():
    m = _build()
    lora = _kohya_lora()
    w0 = m.double_blocks[0].img_attn.qkv.weight.clone()
    apply_loras(m, [(lora, 1.0)])
    w1 = m.double_blocks[0].img_attn.qkv.weight.clone()
    remove_loras(m)
    apply_loras(m, [(lora, 2.0)])
    w2 = m.double_blocks[0].img_attn.qkv.weight.clone()
    # delta at strength 2 is exactly twice the delta at strength 1.
    assert torch.allclose(w2 - w0, 2.0 * (w1 - w0), atol=1e-5)


def test_multiple_loras_stack():
    m = _build()
    a = _kohya_lora(seed=1)
    b = _kohya_lora(seed=5)
    w_base = m.double_blocks[0].img_attn.qkv.weight.clone()
    apply_loras(m, [(a, 1.0), (b, 1.0)])
    w_both = m.double_blocks[0].img_attn.qkv.weight.clone()
    remove_loras(m)
    # applying a then b separately equals applying both at once.
    apply_loras(m, [(a, 1.0)])
    apply_loras(m, [(b, 1.0)])
    assert torch.allclose(m.double_blocks[0].img_attn.qkv.weight, w_both, atol=1e-5)
    assert not torch.allclose(w_both, w_base)


# --- cast/fp8 path equivalence -------------------------------------------

def test_cast_hook_matches_standard_inplace():
    """A manual_cast Linear (hook) and a plain Linear (in-place) with the same
    LoRA must produce the same forward output within dequant tolerance."""
    std_ops = pick_operations(torch.float32, torch.float32)     # comfy_cast_weights False
    cast_ops = pick_operations(torch.float16, torch.float32)    # manual_cast, hook path

    torch.manual_seed(7)
    base_w = torch.randn(192, 64)
    up, down = torch.randn(192, 4) * 0.1, torch.randn(4, 64) * 0.1
    delta = LoraDelta(down=down, up=up, alpha=4.0, scale=1.0)

    std = std_ops.Linear(64, 192, bias=False)
    std.weight.data = base_w.clone()
    from src.platform.runtime.native.lora.apply import _apply_inplace
    _apply_inplace(std, [delta])

    cast = cast_ops.Linear(64, 192, bias=False)
    cast.weight.data = base_w.clone().to(torch.float16)
    cast.lora_deltas = [delta]

    x = torch.randn(3, 64)
    assert std.comfy_cast_weights is False
    assert cast.comfy_cast_weights is True
    out_std = std(x)
    out_cast = cast(x.to(torch.float16).float()) if False else cast(x)
    assert torch.allclose(out_std, out_cast, atol=2e-2)


def test_fp8_module_forward_with_lora_runs():
    """End-to-end: LoRA on a GENUINELY fp8-dtype-stored Linear goes through the
    hook path (runtime deltas), never patching the fp8 storage. Building via
    `pick_operations(torch.float8_e4m3fn, ...)` alone only selects the fp8_ops
    *class* - `Flux.from_config` builds with `dtype=None` (-> float32), so the
    test must explicitly cast the weight to fp8 to exercise real fp8 storage
    (the routing criterion is storage dtype, not which ops class the module
    happens to be wrapped in - see `_needs_runtime_deltas`)."""
    m = _build(pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    assert qkv.comfy_cast_weights is True
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)
    storage_before = qkv.weight.data.clone()

    x = torch.randn(1, 16, 16, 16, dtype=torch.bfloat16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32, dtype=torch.bfloat16)
    with torch.no_grad():
        base = m(x, t, ctx)
    apply_loras(m, [(_kohya_lora(), 1.0)])
    # fp8-dtype storage -> runtime deltas attached, weight (fp8) untouched.
    assert qkv.weight.dtype == torch.float8_e4m3fn
    assert qkv.lora_deltas and len(qkv.lora_deltas) == 1
    assert torch.equal(qkv.weight.data.float(), storage_before.float())
    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base.float(), after.float())
    remove_loras(m)
    assert qkv.lora_deltas is None
    assert torch.equal(qkv.weight.data.float(), storage_before.float())


# --- storage-dtype routing criterion (the actual fix) --------------------
#
# Root cause of the reported bug: routing was previously keyed off
# `comfy_cast_weights` (True for ANY Linear under manual_cast/fp8_ops, e.g.
# every Linear in a mixed-precision Krea-2 checkpoint), not off whether the
# weight's own storage dtype can actually be patched in place. These tests
# exercise the corrected, storage-dtype-based `_needs_runtime_deltas`.

def test_needs_runtime_deltas_false_for_patchable_dtypes_even_under_cast_mode():
    """The actual bug fix: a bf16/fp16/fp32-stored Linear must be treated as
    in-place-patchable EVEN THOUGH it lives under manual_cast/fp8_ops
    (comfy_cast_weights=True) - this is exactly Krea-2's mixed checkpoint."""
    cast_ops = pick_operations(torch.float16, torch.float32)  # manual_cast
    for dtype in (torch.float32, torch.float16, torch.bfloat16):
        lin = cast_ops.Linear(8, 8, bias=False)
        lin.weight.data = lin.weight.data.to(dtype)
        assert lin.comfy_cast_weights is True
        assert _needs_runtime_deltas(lin) is False, dtype


def test_needs_runtime_deltas_true_for_fp8_dtype():
    cast_ops = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    lin = cast_ops.Linear(8, 8, bias=False)
    lin.weight.data = lin.weight.data.to(torch.float8_e4m3fn)
    assert _needs_runtime_deltas(lin) is True


def test_needs_runtime_deltas_true_for_nvfp4_marker_regardless_of_weight():
    cast_ops = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    lin = cast_ops.Linear(8, 8, bias=False)
    lin._is_nvfp4 = True
    assert _needs_runtime_deltas(lin) is True


def test_cast_mode_bf16_linear_patched_in_place_not_runtime_deltas():
    """The primary regression test: a bf16-storage Linear under manual_cast
    must be patched in place (no lora_deltas), and its forward output must
    match what the OLD runtime-delta path would have produced."""
    cast_ops = pick_operations(torch.bfloat16, torch.float32)  # manual_cast, storage=bf16

    torch.manual_seed(11)
    base_w = torch.randn(32, 16, dtype=torch.bfloat16)
    up, down = torch.randn(32, 4) * 0.1, torch.randn(4, 16) * 0.1
    delta = LoraDelta(down=down, up=up, alpha=4.0, scale=1.0)

    lin = cast_ops.Linear(16, 32, bias=False)
    lin.weight.data = base_w.clone()
    assert lin.comfy_cast_weights is True
    assert _needs_runtime_deltas(lin) is False

    from src.platform.runtime.native.lora.apply import _apply_inplace

    _apply_inplace(lin, [delta])
    assert lin.lora_deltas is None
    assert lin.weight.dtype == torch.bfloat16

    x = torch.randn(3, 16)
    out_inplace = lin(x)

    # What the OLD runtime-delta path would have produced on the SAME base weight.
    ref_lin = cast_ops.Linear(16, 32, bias=False)
    ref_lin.weight.data = base_w.clone()
    ref_lin.lora_deltas = [delta]
    out_runtime = ref_lin(x)

    assert torch.allclose(out_inplace, out_runtime, atol=2e-2)


def test_lokr_in_place_matches_runtime_path():
    """LoKr (torch.kron) delta patched in place must match the runtime-delta
    (per-forward hook) result for the same base weight."""
    cast_ops = pick_operations(torch.bfloat16, torch.float32)

    torch.manual_seed(13)
    base_w = torch.randn(16, 16, dtype=torch.bfloat16)
    w1, w2 = torch.randn(4, 4) * 0.1, torch.randn(4, 4) * 0.1
    delta = LoraDelta(down=w2, up=w1, alpha=1.0, scale=1.0, kron=True)

    from src.platform.runtime.native.lora.apply import _apply_inplace

    lin = cast_ops.Linear(16, 16, bias=False)
    lin.weight.data = base_w.clone()
    _apply_inplace(lin, [delta])
    assert lin.lora_deltas is None

    ref_lin = cast_ops.Linear(16, 16, bias=False)
    ref_lin.weight.data = base_w.clone()
    ref_lin.lora_deltas = [delta]

    x = torch.randn(2, 16)
    assert torch.allclose(lin(x), ref_lin(x), atol=2e-2)


def test_apply_inplace_never_rebinds_weight_storage():
    """`_apply_inplace` must mutate the EXISTING tensor storage (`add_`), never
    rebind `weight.data` to a new tensor - required so pinned CPU memory
    (partial-residency streaming) and any external views survive a LoRA apply."""
    from src.platform.runtime.native.lora.apply import _apply_inplace

    ops = pick_operations(torch.float32, torch.float32)
    lin = ops.Linear(8, 8, bias=False)
    original_storage = lin.weight.data
    delta = LoraDelta(
        down=torch.randn(2, 8) * 0.1, up=torch.randn(8, 2) * 0.1, alpha=2.0, scale=1.0,
    )
    _apply_inplace(lin, [delta])
    assert lin.weight.data.data_ptr() == original_storage.data_ptr()


def test_apply_inplace_pinned_cpu_tensor_stays_pinned_and_same_storage():
    """A pinned CPU weight (partial-residency streaming) must stay pinned and
    remain the same storage object after a LoRA patch."""
    if not torch.cuda.is_available():
        import pytest
        pytest.skip("pinning requires CUDA")

    from src.platform.runtime.native.lora.apply import _apply_inplace

    ops = pick_operations(torch.float32, torch.float32)
    lin = ops.Linear(8, 8, bias=False)
    pinned = lin.weight.data.pin_memory()
    lin.weight.data = pinned
    assert lin.weight.data.is_pinned()
    ptr_before = lin.weight.data.data_ptr()

    delta = LoraDelta(
        down=torch.randn(2, 8) * 0.1, up=torch.randn(8, 2) * 0.1, alpha=2.0, scale=1.0,
    )
    _apply_inplace(lin, [delta])

    assert lin.weight.data.is_pinned()
    assert lin.weight.data.data_ptr() == ptr_before


def test_apply_loras_end_to_end_patches_in_place_on_manual_cast_module():
    """The actual reported-bug scenario: a module built under `manual_cast`
    ops (comfy_cast_weights=True for every Linear, exactly what a mixed
    bf16/f32 Krea-2 checkpoint gets) whose weights are still plain float
    storage. `apply_loras()` must route through `_apply_inplace`, not attach
    `lora_deltas` - i.e. no per-forward delta recompute."""
    m = _build(pick_operations(torch.float16, torch.float32))  # manual_cast
    qkv = m.double_blocks[0].img_attn.qkv
    assert qkv.comfy_cast_weights is True
    assert qkv.weight.dtype == torch.float32  # real storage, despite manual_cast ops

    x, t, ctx = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)
    with torch.no_grad():
        base = m(x, t, ctx)

    n, unmatched = apply_loras(m, [(_kohya_lora(), 1.0)])
    assert n == 1 and unmatched == []
    assert qkv.lora_deltas is None  # patched in place, NOT attached for per-forward apply

    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base, after)

    remove_loras(m)
    with torch.no_grad():
        restored = m(x, t, ctx)
    assert torch.allclose(base, restored, atol=1e-5)


# --- temporarily_applied_loras (scoped apply/restore context manager) ----

def test_temporarily_applied_loras_empty_stack_is_noop():
    m = _build()
    w0 = m.double_blocks[0].img_attn.qkv.weight.data.clone()
    with temporarily_applied_loras(m, []):
        assert torch.equal(m.double_blocks[0].img_attn.qkv.weight.data, w0)
    assert torch.equal(m.double_blocks[0].img_attn.qkv.weight.data, w0)


def test_temporarily_applied_loras_empty_stack_never_calls_apply_loras(monkeypatch):
    import src.platform.runtime.native.lora.apply as apply_mod

    called = {"n": 0}
    monkeypatch.setattr(apply_mod, "apply_loras", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    m = _build()
    with temporarily_applied_loras(m, []):
        pass
    assert called["n"] == 0


def test_temporarily_applied_loras_baked_applies_then_restores():
    m = _build()
    x, t, ctx = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)
    with torch.no_grad():
        base = m(x, t, ctx)
    w0 = m.double_blocks[0].img_attn.qkv.weight.data.clone()

    with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)]):
        with torch.no_grad():
            during = m(x, t, ctx)
        assert not torch.allclose(base, during)
        assert not torch.allclose(w0, m.double_blocks[0].img_attn.qkv.weight.data)

    with torch.no_grad():
        after = m(x, t, ctx)
    assert torch.allclose(base, after, atol=1e-5)
    assert torch.allclose(w0, m.double_blocks[0].img_attn.qkv.weight.data, atol=1e-5)


def test_temporarily_applied_loras_resident_fp8_applies_then_restores():
    m = _build(pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)

    x = torch.randn(1, 16, 16, 16, dtype=torch.bfloat16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32, dtype=torch.bfloat16)
    with torch.no_grad():
        base = m(x, t, ctx)

    with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)]):
        assert qkv.lora_deltas and len(qkv.lora_deltas) == 1
        with torch.no_grad():
            during = m(x, t, ctx)
        assert not torch.allclose(base.float(), during.float())

    assert qkv.lora_deltas is None
    with torch.no_grad():
        after = m(x, t, ctx)
    assert torch.allclose(base.float(), after.float())


def test_temporarily_applied_loras_preserves_preexisting_resident_deltas():
    """The scoped-restore guarantee: a LoRA already resident on the module
    before the `with` block (standing in for the generation-stage stack a
    model loader applied at load time) must survive the block untouched --
    both in content (list length) and in effect (forward output) -- while
    ONLY the block's own addition is undone."""
    m = _build(pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)

    x = torch.randn(1, 16, 16, 16, dtype=torch.bfloat16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32, dtype=torch.bfloat16)

    apply_loras(m, [(_kohya_lora(seed=1), 1.0)])  # "generation stack"
    assert len(qkv.lora_deltas) == 1
    with torch.no_grad():
        base_with_generation_lora = m(x, t, ctx)

    with temporarily_applied_loras(m, [(_kohya_lora(seed=5), 1.0)]):  # "stage-2 extra"
        assert len(qkv.lora_deltas) == 2

    assert len(qkv.lora_deltas) == 1  # only the generation-stage entry remains
    with torch.no_grad():
        restored = m(x, t, ctx)
    assert torch.allclose(base_with_generation_lora.float(), restored.float())


def test_temporarily_applied_loras_preserves_preexisting_baked_deltas():
    m = _build()
    apply_loras(m, [(_kohya_lora(seed=1), 1.0)])  # "generation stack", baked in-place
    w_with_generation_lora = m.double_blocks[0].img_attn.qkv.weight.data.clone()

    with temporarily_applied_loras(m, [(_kohya_lora(seed=5), 1.0)]):  # "stage-2 extra"
        assert not torch.allclose(w_with_generation_lora, m.double_blocks[0].img_attn.qkv.weight.data)

    assert torch.allclose(
        w_with_generation_lora, m.double_blocks[0].img_attn.qkv.weight.data, atol=1e-5,
    )


def test_temporarily_applied_loras_restores_even_on_exception():
    m = _build()
    x, t, ctx = torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32)
    with torch.no_grad():
        base = m(x, t, ctx)

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with temporarily_applied_loras(m, [(_kohya_lora(), 1.0)]):
            raise _Boom("mid-sampling failure")

    with torch.no_grad():
        after = m(x, t, ctx)
    assert torch.allclose(base, after, atol=1e-5)


def test_remove_loras_restores_close_not_bit_identical():
    """Restoration is exact only to ~1 ulp of storage-dtype rounding (two
    independent fp32-compute-then-cast roundings), not bit-identical - the
    removal-record redesign trades a full weight-shaped undo copy for
    recomputing the delta, which is fine since nothing hot calls remove_loras
    (see `remove_loras`'s docstring)."""
    m = _build()
    lora = _kohya_lora()
    w0 = m.double_blocks[0].img_attn.qkv.weight.data.clone()
    apply_loras(m, [(lora, 1.0)])
    remove_loras(m)
    w_restored = m.double_blocks[0].img_attn.qkv.weight.data
    assert torch.allclose(w0, w_restored, atol=1e-5)
