"""Tests for the Krea-2 LoRA key mapping (kohya/comfy/PEFT/diffusers dialects).

Krea-2's attention is already split (blocks.N.attn.{wq,wk,wv,wo,gate}), so unlike
Flux there are no fused-qkv row-slices — every trainable Linear maps 1:1 by name.
The PEFT and diffusers fixtures below use the REAL key spellings observed in
public files (2026-07-10): gokaygokay/Krea-2-Realism-LoRA (PEFT wrapper over the
native names) and krea/Krea-2-LoRA-darkbrush (official diffusers naming —
to_q/to_k/to_v/to_out.0/to_gate, ff., transformer_blocks., text_fusion.,
img_in/final_layer/time_embed/time_mod_proj/txt_in).
"""

from __future__ import annotations

import torch
import torch.nn as nn

from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.lora import apply_loras, map_lora_keys, remove_loras
from src.platform.runtime.native.lora.key_mapping import build_krea2_lora_key_map, _select_key_map, _krea2_diffusers_stem
from vendor.gpl.comfyui.ops import pick_operations

TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1, "channels": 4,
    "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16, "txtheads": 2,
    "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build() -> Krea2:
    m = Krea2.from_config(TINY, _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _kohya(stem: str, out: int, inf: int, rank: int = 4, seed: int = 1):
    g = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_up.weight": torch.randn(out, rank, generator=g) * 0.1,
        f"{stem}.lora_down.weight": torch.randn(rank, inf, generator=g) * 0.1,
        f"{stem}.alpha": torch.tensor(float(rank)),
    }


# --- key map --------------------------------------------------------------

def test_key_map_covers_native_names_no_slices():
    km = build_krea2_lora_key_map(_build())
    # kohya underscore + comfy bare + diffusion_model.-prefixed, all 1:1 (no slice).
    assert km["lora_unet_blocks_0_attn_wq"] == ("blocks.0.attn.wq.weight", None)
    assert km["blocks.0.attn.wq"] == ("blocks.0.attn.wq.weight", None)
    assert km["diffusion_model.blocks.0.attn.wq"] == ("blocks.0.attn.wq.weight", None)
    assert km["lora_unet_blocks_0_mlp_gate"] == ("blocks.0.mlp.gate.weight", None)
    # trainable txtfusion Linears are targetable too.
    assert km["lora_unet_txtfusion_layerwise_blocks_0_attn_wq"][0] == \
        "txtfusion.layerwise_blocks.0.attn.wq.weight"
    # Krea-2 never produces fused-qkv slices.
    assert all(sl is None for _, sl in km.values())


def test_select_key_map_dispatches_by_arch():
    # Krea-2 module -> krea2 (split) map; a fused-qkv target is NOT registered.
    km = _select_key_map(_build())
    assert "blocks.0.attn.wq" in km
    assert not any(".img_attn.qkv" in k for k in km)  # no flux keys


def test_flux_module_still_uses_flux_map():
    from src.platform.runtime.native.arch.flux.model import Flux
    ftiny = {"image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
             "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
             "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
             "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False}
    with torch.device("meta"):
        fm = Flux.from_config(ftiny, _fp32_ops())
    km = _select_key_map(fm)
    # flux map carries the diffusers fused-qkv slices Krea-2's never has.
    to_q = km["transformer.transformer_blocks.0.attn.to_q"]
    assert to_q[1] is not None and to_q[0].endswith(".img_attn.qkv.weight")


# --- non-Krea-2 fallback (native LTX diffusers dialect) --------------------
#
# LTX has no `.params.hidden_size`, so `_select_key_map` routes it through
# `build_krea2_lora_key_map` too (see its docstring: "Anything else falls
# back to Krea-2's plain comfy/kohya scheme"). Its native param names are
# already diffusers-shaped (`transformer_blocks.N.attn1.to_q`, no Krea-2
# renaming applies), so `_krea2_diffusers_stem` returns None for every LTX
# stem and only the `transformer.{stem}` alias covers Lightricks' published
# (diffusers/IC-LoRA) key spelling.

class _FakeAttn(nn.Module):
    def __init__(self):
        super().__init__()
        self.to_q = nn.Linear(8, 8, bias=False)


class _FakeLTXBlock(nn.Module):
    def __init__(self):
        super().__init__()
        self.attn1 = _FakeAttn()


class _FakeLTXTransformer(nn.Module):
    """Minimal stand-in for the native LTX module: no `.params`, diffusers-
    shaped Linear names -- enough to exercise `_select_key_map`'s fallback
    and the diffusers-dialect alias without needing the real LTX arch."""

    def __init__(self):
        super().__init__()
        self.transformer_blocks = nn.ModuleList([_FakeLTXBlock()])


def test_krea2_diffusers_stem_returns_none_for_ltx_shaped_names():
    # Confirms the premise: Krea-2's renaming table has nothing to do for an
    # already-diffusers-shaped stem, which is exactly why the unconditional
    # `transformer.{stem}` alias is needed.
    assert _krea2_diffusers_stem("transformer_blocks.0.attn1.to_q") is None


def test_key_map_registers_transformer_prefixed_alias_for_diffusers_shaped_stems():
    km = build_krea2_lora_key_map(_FakeLTXTransformer())
    assert km["transformer.transformer_blocks.0.attn1.to_q"] == \
        ("transformer_blocks.0.attn1.to_q.weight", None)
    # The plain comfy/kohya aliases are still registered alongside it.
    assert km["transformer_blocks.0.attn1.to_q"] == ("transformer_blocks.0.attn1.to_q.weight", None)
    assert km["diffusion_model.transformer_blocks.0.attn1.to_q"] == \
        ("transformer_blocks.0.attn1.to_q.weight", None)


def test_select_key_map_routes_ltx_shaped_module_through_krea2_fallback():
    # No `.params` attribute at all -> falls back to build_krea2_lora_key_map,
    # exactly like the docstring on _select_key_map promises for "anything else".
    km = _select_key_map(_FakeLTXTransformer())
    assert "transformer.transformer_blocks.0.attn1.to_q" in km


def test_map_transformer_prefixed_ltx_dialect_patches_params():
    """A Lightricks-style LTX/IC-LoRA state dict, keyed `transformer.*`, must
    map onto the native module -- this is the exact regression the loud
    'zero params patched' warning in loader_helpers.py was guarding."""
    m = _FakeLTXTransformer()
    lora = _diffusers("transformer_blocks.0.attn1.to_q", 8, 8)
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    assert set(mapped) == {"transformer_blocks.0.attn1.to_q.weight"}


def test_existing_krea2_transformer_alias_precedence_unchanged():
    """Regression proof: adding the unconditional `transformer.{stem}` alias
    must not disturb Krea-2's own diffusers-renamed `transformer.*` entries
    (registered from `diffusers_stem`, a DIFFERENT string from the raw
    native `stem` whenever `_krea2_diffusers_stem` fires)."""
    km = build_krea2_lora_key_map(_build())
    # Krea-2's diffusers rename: blocks.0.attn.wq -> transformer_blocks.0.attn.to_q.
    assert km["transformer.transformer_blocks.0.attn.to_q"] == ("blocks.0.attn.wq.weight", None)
    # The new raw-stem alias exists too, and points at the SAME (correct)
    # param -- it's just a second, redundant spelling for this arch, not a
    # collision (the two prefixed keys are textually different).
    assert km["transformer.blocks.0.attn.wq"] == ("blocks.0.attn.wq.weight", None)


# --- map + apply ----------------------------------------------------------

def test_map_kohya_and_bare_dialects():
    m = _build()
    lora = _kohya("lora_unet_blocks_0_attn_wq", 32, 32)          # kohya
    lora.update({                                                # comfy bare-dotted
        "blocks.0.attn.wo.lora_up.weight": torch.randn(32, 4) * 0.1,
        "blocks.0.attn.wo.lora_down.weight": torch.randn(4, 32) * 0.1,
    })
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    assert set(mapped) == {"blocks.0.attn.wq.weight", "blocks.0.attn.wo.weight"}


def test_apply_changes_forward_and_remove_restores():
    m = _build()
    lora = _kohya("lora_unet_blocks_0_attn_wq", 32, 32)
    x, t, ctx = torch.randn(1, 4, 8, 8), torch.tensor([0.5]), torch.randn(1, 5, 3, 16)
    with torch.no_grad():
        base = m(x, t, ctx)
    n, unmatched = apply_loras(m, [(lora, 1.0)])
    assert n == 1 and unmatched == []
    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base, after)
    remove_loras(m)
    with torch.no_grad():
        restored = m(x, t, ctx)
    assert torch.allclose(base, restored, atol=1e-5)


def test_unmatched_reported():
    m = _build()
    _mapped, unmatched = map_lora_keys(_kohya("lora_unet_totally_bogus", 8, 8), m)
    assert "lora_unet_totally_bogus" in unmatched


# --- real-world dialects (key spellings from actual public LoRA files) ------

def _peft(stem: str, out: int, inf: int, rank: int = 4):
    """PEFT wrapper spelling (community trainers): base_model.model.{native stem}."""
    return {
        f"base_model.model.{stem}.lora_B.weight": torch.randn(out, rank) * 0.1,
        f"base_model.model.{stem}.lora_A.weight": torch.randn(rank, inf) * 0.1,
    }


def _diffusers(stem: str, out: int, inf: int, rank: int = 4):
    """Official diffusers spelling (krea/Krea-2-LoRA-*): transformer.{renamed stem}."""
    return {
        f"transformer.{stem}.lora_B.weight": torch.randn(out, rank) * 0.1,
        f"transformer.{stem}.lora_A.weight": torch.randn(rank, inf) * 0.1,
    }


def test_map_peft_dialect():
    m = _build()
    lora = {}
    lora.update(_peft("blocks.0.attn.wq", 32, 32))
    lora.update(_peft("blocks.0.mlp.down", 32, 32))          # tiny cfg: mult=1
    lora.update(_peft("txtfusion.layerwise_blocks.0.attn.wk", 16, 16))
    lora.update(_peft("first", 32, 16))
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    assert set(mapped) == {
        "blocks.0.attn.wq.weight", "blocks.0.mlp.down.weight",
        "txtfusion.layerwise_blocks.0.attn.wk.weight", "first.weight",
    }


def test_map_diffusers_dialect():
    m = _build()
    lora = {}
    lora.update(_diffusers("transformer_blocks.0.attn.to_q", 32, 32))
    lora.update(_diffusers("transformer_blocks.0.attn.to_k", 16, 32))   # GQA out
    lora.update(_diffusers("transformer_blocks.0.attn.to_out.0", 32, 32))
    lora.update(_diffusers("transformer_blocks.0.attn.to_gate", 32, 32))
    lora.update(_diffusers("transformer_blocks.0.ff.gate", 32, 32))
    lora.update(_diffusers("text_fusion.layerwise_blocks.0.attn.to_v", 16, 16))
    lora.update(_diffusers("img_in", 32, 16))
    lora.update(_diffusers("final_layer.linear", 16, 32))
    lora.update(_diffusers("time_embed.linear_1", 32, 16))
    lora.update(_diffusers("time_mod_proj", 32, 32))
    lora.update(_diffusers("txt_in.linear_1", 32, 16))
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    assert set(mapped) == {
        "blocks.0.attn.wq.weight", "blocks.0.attn.wk.weight",
        "blocks.0.attn.wo.weight", "blocks.0.attn.gate.weight",
        "blocks.0.mlp.gate.weight",
        "txtfusion.layerwise_blocks.0.attn.wv.weight",
        "first.weight", "last.linear.weight",
        "tmlp.0.weight", "tproj.1.weight", "txtmlp.1.weight",
    }


# --- LoKr (LyCORIS Kronecker) — the format the user's krea2 files ship -------

def _lokr_direct(stem: str, out: int, inf: int, o1: int = 4, i1: int = 4):
    """Direct (unfactorized) LoKr: w1 (o1, i1), w2 (out/o1, inf/i1)."""
    return {
        f"{stem}.lokr_w1": torch.randn(o1, i1) * 0.1,
        f"{stem}.lokr_w2": torch.randn(out // o1, inf // i1) * 0.1,
    }


def test_map_lokr_direct_and_delta_math():
    m = _build()
    lora = _lokr_direct("diffusion_model.blocks.0.attn.wq", 32, 32)
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    assert set(mapped) == {"blocks.0.attn.wq.weight"}
    d = mapped["blocks.0.attn.wq.weight"][0]
    assert d.kron and d.alpha == 1.0          # no factorization -> no alpha/dim scaling
    # The applied delta must be exactly kron(w1, w2).
    from vendor.gpl.comfyui.ops import apply_lora_deltas
    w = torch.zeros(32, 32)
    out = apply_lora_deltas(w, [d])
    expected = torch.kron(
        lora["diffusion_model.blocks.0.attn.wq.lokr_w1"].float(),
        lora["diffusion_model.blocks.0.attn.wq.lokr_w2"].float(),
    )
    assert torch.allclose(out, expected, atol=1e-6)


def test_map_lokr_factorized_alpha_over_dim():
    m = _build()
    rank = 2
    w2a, w2b = torch.randn(8, rank) * 0.1, torch.randn(rank, 8) * 0.1
    lora = {
        "blocks.0.attn.wo.lokr_w1": torch.randn(4, 4) * 0.1,
        "blocks.0.attn.wo.lokr_w2_a": w2a,
        "blocks.0.attn.wo.lokr_w2_b": w2b,
        "blocks.0.attn.wo.alpha": torch.tensor(float(rank)),  # alpha == dim -> scale 1
    }
    mapped, unmatched = map_lora_keys(lora, m)
    assert unmatched == []
    d = mapped["blocks.0.attn.wo.weight"][0]
    assert d.kron and abs(d.alpha - 1.0) < 1e-6              # alpha/dim = rank/rank
    assert torch.allclose(d.down, (w2a.float() @ w2b.float()), atol=1e-6)


def test_apply_lokr_changes_forward_and_remove_restores():
    m = _build()
    lora = _lokr_direct("blocks.0.attn.wq", 32, 32)
    x, t, ctx = torch.randn(1, 4, 8, 8), torch.tensor([0.5]), torch.randn(1, 5, 3, 16)
    with torch.no_grad():
        base = m(x, t, ctx)
    n, unmatched = apply_loras(m, [(lora, 1.0)])
    assert n == 1 and unmatched == []
    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base, after)
    remove_loras(m)
    with torch.no_grad():
        restored = m(x, t, ctx)
    assert torch.allclose(base, restored, atol=1e-5)


def test_lokr_tucker_reported_not_half_applied():
    m = _build()
    lora = _lokr_direct("blocks.0.attn.wq", 32, 32)
    lora["blocks.0.attn.wq.lokr_t2"] = torch.randn(2, 2, 1, 1)
    mapped, unmatched = map_lora_keys(lora, m)
    assert mapped == {} and "blocks.0.attn.wq" in unmatched


def test_apply_peft_dialect_changes_forward():
    m = _build()
    lora = _peft("blocks.0.attn.wq", 32, 32)
    x, t, ctx = torch.randn(1, 4, 8, 8), torch.tensor([0.5]), torch.randn(1, 5, 3, 16)
    with torch.no_grad():
        base = m(x, t, ctx)
    n, unmatched = apply_loras(m, [(lora, 1.0)])
    assert n == 1 and unmatched == []
    with torch.no_grad():
        after = m(x, t, ctx)
    assert not torch.allclose(base, after)
    remove_loras(m)
