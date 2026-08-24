"""Tests for the MiniMax-H3 LoRA key mapping (diffusers/PEFT dialect) + application.

MiniMax-H3's checkpoint fuses q/k/v into one ``blocks.{i}.attn.qkv_proj``
[3*inner, hidden] (own naming, distinct from Flux's ``qkv``). The one
published LoRA (lightx2v/Minimax-h3-Turbo, Apache-2.0, real header verified at
``scratchpad/turbo_lora_header.json`` — 624 keys) ships in the diffusers/PEFT
dialect: split ``attn.to_q/to_k/to_v/to_out.0``, ``ff.net.0.proj``/``ff.net.2``,
``token_refiner.refiner_blocks.{i}.*``, PEFT's ``.lora_A.default.weight``/
``.lora_B.default.weight`` spelling, rank 128, no AdaLN/time_embedder keys.
"""

from __future__ import annotations

import torch

import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model, MiniMaxH3MLP
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.lora.apply import apply_loras, remove_loras
from src.platform.runtime.native.lora.key_mapping import (
    LoraDelta,
    build_minimax_h3_lora_key_map,
    _select_key_map,
    _swap_swiglu_halves,
    _SWIGLU_HALF_SWAP,
    map_lora_keys,
)
from vendor.gpl.comfyui.ops import apply_lora_deltas, pick_operations

# Same shape traps as test_minimax_h3_model.py's TINY_FULL: inner(80) != hidden(64).
TINY = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": 6, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
    "pruned": False, "time_embed_dim": 12, "freq_dim": 8, "time_embed_hidden_dim": 16,
}
HIDDEN = 64
INNER = 2 * 40  # heads * head_dim


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build(config: dict = TINY, ops=None) -> MiniMaxH3Model:
    ops = ops or _fp32_ops()
    m = MiniMaxH3Model.from_config(config, ops)
    sd = {}
    for k, v in m.state_dict().items():
        if not v.is_floating_point():
            sd[k] = v.clone()
        elif ".norm" in k:
            sd[k] = torch.ones_like(v)
        else:
            sd[k] = torch.randn_like(v) * 0.02
    load_into_module(m, sd, match_model_spec(config))
    m.eval()
    return m


def _peft_lora(stem: str, out: int, inf: int, rank: int = 4, seed: int = 1) -> dict[str, torch.Tensor]:
    """PEFT-default-adapter dialect, matching the real turbo LoRA's exact
    spelling (``.lora_A.default.weight`` / ``.lora_B.default.weight``)."""
    g = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_A.default.weight": torch.randn(rank, inf, generator=g) * 0.1,
        f"{stem}.lora_B.default.weight": torch.randn(out, rank, generator=g) * 0.1,
    }


def _diffusers_qkv_lora(prefix: str, rank: int = 4, seed: int = 2) -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {}
    for i, proj in enumerate(("to_q", "to_k", "to_v")):
        sd.update(_peft_lora(f"{prefix}.attn.{proj}", INNER, HIDDEN, rank=rank, seed=seed + i))
    return sd


def _tiny_layout(text_n: int = 2, video_n: int = 3, audio_n: int = 2) -> dict[str, torch.Tensor]:
    seq_len = text_n + video_n + audio_n
    text_indices = torch.arange(0, text_n)
    video_indices = torch.arange(text_n, text_n + video_n)
    audio_indices = torch.arange(text_n + video_n, seq_len)
    token_tags = torch.zeros(seq_len, dtype=torch.long)
    token_tags[text_indices] = 1
    token_tags[audio_indices] = 2
    timestep_indices = torch.zeros(seq_len, dtype=torch.long)
    position_ids = torch.rand(seq_len, 3, dtype=torch.float64)
    return dict(
        text_indices=text_indices, video_indices=video_indices, audio_indices=audio_indices,
        token_tags=token_tags, timestep_indices=timestep_indices, position_ids=position_ids,
    )


def _fixed_inputs(layout: dict, seed: int = 3) -> dict[str, torch.Tensor]:
    """One set of (video/audio/text) inputs, generated ONCE and reused across
    every forward call in a before/after/restored comparison -- calling
    torch.randn fresh inside a forward helper would silently compare against
    DIFFERENT random inputs each time (RNG state advances), not the same
    request before and after a LoRA apply/remove."""
    g = torch.Generator().manual_seed(seed)
    video_patch_dim = TINY["in_channels"] * 4
    return dict(
        hidden_states=torch.randn(1, layout["video_indices"].numel(), video_patch_dim, generator=g),
        audio_hidden_states=torch.randn(1, layout["audio_indices"].numel(), TINY["audio_in_channels"], generator=g),
        encoder_hidden_states=torch.randn(1, layout["text_indices"].numel(), TINY["text_dim"], generator=g),
    )


def _forward(m: MiniMaxH3Model, layout: dict, inputs: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
    return m(
        inputs["hidden_states"], inputs["audio_hidden_states"], inputs["encoder_hidden_states"],
        torch.tensor([0.5]), layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
        layout["video_indices"], layout["audio_indices"], layout["text_indices"],
    )


# --- key map -----------------------------------------------------------------

def test_key_map_covers_diffusers_dialect_with_slices():
    km = build_minimax_h3_lora_key_map(_build())
    qkv = "blocks.0.attn.qkv_proj.weight"
    assert km["transformer.transformer_blocks.0.attn.to_q"] == (qkv, (0, 0, INNER))
    assert km["transformer.transformer_blocks.0.attn.to_k"] == (qkv, (0, INNER, INNER))
    assert km["transformer.transformer_blocks.0.attn.to_v"] == (qkv, (0, 2 * INNER, INNER))
    assert km["transformer.transformer_blocks.0.attn.to_out.0"] == ("blocks.0.attn.out_proj.weight", None)
    # ff.net.0.proj -> fc1 carries the half-swap sentinel (diffusers ships
    # [value|gate], native fc1 needs [gate|value] -- see MiniMaxH3MLP); fc2
    # needs no slicing/swap (its input is the single ffn-wide SwiGLU product).
    assert km["transformer.transformer_blocks.0.ff.net.0.proj"] == ("blocks.0.mlp.fc1.weight", _SWIGLU_HALF_SWAP)
    assert km["transformer.transformer_blocks.0.ff.net.2"] == ("blocks.0.mlp.fc2.weight", None)
    # bare (no transformer. prefix) is also registered.
    assert km["transformer_blocks.0.attn.to_q"] == (qkv, (0, 0, INNER))


def test_key_map_covers_token_refiner_with_renamed_prefix():
    km = build_minimax_h3_lora_key_map(_build())
    rqkv = "token_refiner.blocks.0.attn.qkv_proj.weight"
    # diffusers/turbo-LoRA calls it refiner_blocks; the native module calls it blocks.
    assert km["transformer.token_refiner.refiner_blocks.0.attn.to_q"] == (rqkv, (0, 0, INNER))
    assert km["transformer.token_refiner.refiner_blocks.0.attn.to_out.0"] == (
        "token_refiner.blocks.0.attn.out_proj.weight", None)
    assert km["transformer.token_refiner.refiner_blocks.0.ff.net.0.proj"] == (
        "token_refiner.blocks.0.mlp.fc1.weight", _SWIGLU_HALF_SWAP)
    assert "transformer.token_refiner.refiner_blocks.1.attn.to_q" not in km  # only 1 refiner layer in TINY


def test_key_map_covers_comfy_kohya_and_peft_wrapper_for_free():
    km = build_minimax_h3_lora_key_map(_build())
    qkv = "blocks.0.attn.qkv_proj.weight"
    assert km["lora_unet_blocks_0_attn_qkv_proj"] == (qkv, None)
    assert km["diffusion_model.blocks.0.attn.qkv_proj"] == (qkv, None)
    assert km["blocks.0.attn.qkv_proj"] == (qkv, None)
    assert km["base_model.model.blocks.0.attn.qkv_proj"] == (qkv, None)


def test_no_adaln_or_time_embedder_targets_registered():
    # The real turbo LoRA carries no AdaLN/time_embedder keys at all -- confirm
    # the DIFFUSERS-dialect entries (what the real LoRA file would look up)
    # don't invent targets for them either. The comfy/kohya-generic spellings
    # DO cover every native Linear including adaln_proj (same as Flux/Krea-2's
    # own maps) -- that's a separate, correct feature for a hypothetical
    # comfy-dialect adaln LoRA, not something the real turbo file ever uses.
    km = build_minimax_h3_lora_key_map(_build())
    diffusers_keys = [
        k for k in km
        if k.startswith("transformer.transformer_blocks.") or k.startswith("transformer_blocks.")
        or k.startswith("transformer.token_refiner.refiner_blocks.") or k.startswith("token_refiner.refiner_blocks.")
    ]
    assert diffusers_keys  # sanity: the diffusers dialect IS registered
    assert not any("adaln" in k for k in diffusers_keys)
    assert not any("time_embedder" in k for k in diffusers_keys)


def test_select_key_map_dispatches_to_minimax_h3():
    km = _select_key_map(_build())
    assert km["transformer.transformer_blocks.0.attn.to_q"] == (
        "blocks.0.attn.qkv_proj.weight", (0, 0, INNER))
    # not the Flux map (different native names) and not Krea2's split (no slices
    # at all) -- H3 IS fused, so a genuine slice must be present.
    assert not any(".img_attn." in k for k in km)
    assert any(sl is not None for _, sl in km.values())


def test_flux_and_krea2_modules_unaffected_by_the_new_branch():
    from src.platform.runtime.native.arch.flux.model import Flux
    ftiny = {"image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
             "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
             "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
             "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False}
    with torch.device("meta"):
        fm = Flux.from_config(ftiny, _fp32_ops())
    km = _select_key_map(fm)
    assert "double_blocks.0.img_attn.qkv" in km  # still Flux's own map

    from src.platform.runtime.native.arch.krea2.model import Krea2
    ktiny = {"image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1, "channels": 4,
             "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16, "txtheads": 2,
             "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0}
    with torch.device("meta"):
        km2 = _select_key_map(Krea2.from_config(ktiny, _fp32_ops()))
    assert all(sl is None for _, sl in km2.values())  # still Krea2's split (no slices)


# --- map_lora_keys -------------------------------------------------------------

def test_map_diffusers_three_sliced_deltas_transformer_block():
    lora = _diffusers_qkv_lora("transformer_blocks.0")
    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    deltas = mapped["blocks.0.attn.qkv_proj.weight"]
    assert len(deltas) == 3
    assert {d.target_slice for d in deltas} == {
        (0, 0, INNER), (0, INNER, INNER), (0, 2 * INNER, INNER)}


def test_map_diffusers_refiner_block_renamed_prefix():
    lora = _diffusers_qkv_lora("token_refiner.refiner_blocks.0")
    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    assert "token_refiner.blocks.0.attn.qkv_proj.weight" in mapped
    assert len(mapped["token_refiner.blocks.0.attn.qkv_proj.weight"]) == 3


def test_map_ff_targets_have_no_slice():
    lora = _peft_lora("transformer_blocks.0.ff.net.0.proj", 2 * 48, HIDDEN)
    lora.update(_peft_lora("transformer_blocks.0.ff.net.2", HIDDEN, 48))
    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    (fc1_delta,) = mapped["blocks.0.mlp.fc1.weight"]
    (fc2_delta,) = mapped["blocks.0.mlp.fc2.weight"]
    # the sentinel is resolved to a plain whole-weight delta (target_slice=None)
    # by map_lora_keys -- but its `up` rows must have been half-swapped first,
    # not passed through verbatim (that swap is checked separately below).
    assert fc1_delta.target_slice is None
    assert fc2_delta.target_slice is None
    raw_up = lora["transformer_blocks.0.ff.net.0.proj.lora_B.default.weight"]
    torch.testing.assert_close(fc1_delta.up, _swap_swiglu_halves(raw_up))
    assert not torch.allclose(fc1_delta.up, raw_up)  # bite check: swap actually changed it
    # fc2's up is untouched (no swap on the down-projection side).
    raw_fc2_up = lora["transformer_blocks.0.ff.net.2.lora_B.default.weight"]
    torch.testing.assert_close(fc2_delta.up, raw_fc2_up)


def test_map_default_peft_infix_is_stripped():
    # `.lora_A.default.weight`/`.lora_B.default.weight` (PEFT's adapter-name
    # infix) must map cleanly -- not left as unmatched stray keys.
    lora = _peft_lora("transformer_blocks.0.attn.to_out.0", HIDDEN, INNER)
    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    assert "blocks.0.attn.out_proj.weight" in mapped


def test_full_real_dialect_stack_maps_cleanly_with_zero_unmatched():
    """A lora_sd shaped exactly like the real 624-key turbo file (every
    transformer_blocks/token_refiner.refiner_blocks sub-key the real file has,
    nothing else -- no AdaLN/time_embedder) must map with zero unmatched, and
    must not silently require targets the file doesn't provide."""
    lora: dict[str, torch.Tensor] = {}
    for i in range(TINY["num_layers"]):
        pf = f"transformer_blocks.{i}"
        lora.update(_diffusers_qkv_lora(pf, seed=10 + i))
        lora.update(_peft_lora(f"{pf}.attn.to_out.0", HIDDEN, INNER, seed=20 + i))
        lora.update(_peft_lora(f"{pf}.ff.net.0.proj", 2 * 48, HIDDEN, seed=30 + i))
        lora.update(_peft_lora(f"{pf}.ff.net.2", HIDDEN, 48, seed=40 + i))
    for i in range(TINY["num_refiner_layers"]):
        pf = f"token_refiner.refiner_blocks.{i}"
        lora.update(_diffusers_qkv_lora(pf, seed=50 + i))
        lora.update(_peft_lora(f"{pf}.attn.to_out.0", HIDDEN, INNER, seed=60 + i))
        lora.update(_peft_lora(f"{pf}.ff.net.0.proj", 2 * 48, HIDDEN, seed=70 + i))
        lora.update(_peft_lora(f"{pf}.ff.net.2", HIDDEN, 48, seed=80 + i))

    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    # every block's qkv_proj got exactly 3 (q,k,v) deltas, out_proj/fc1/fc2 got 1 each.
    for i in range(TINY["num_layers"]):
        assert len(mapped[f"blocks.{i}.attn.qkv_proj.weight"]) == 3
        assert len(mapped[f"blocks.{i}.attn.out_proj.weight"]) == 1
        assert len(mapped[f"blocks.{i}.mlp.fc1.weight"]) == 1
        assert len(mapped[f"blocks.{i}.mlp.fc2.weight"]) == 1
    for i in range(TINY["num_refiner_layers"]):
        assert len(mapped[f"token_refiner.blocks.{i}.attn.qkv_proj.weight"]) == 3
    # no adaln/time_embedder param was ever touched -- nothing to ignore, the
    # mapper is driven by what's IN the lora file, not by a required set.
    assert not any("adaln" in k or "time_embedder" in k for k in mapped)


# --- application (in-place, fp32/bf16 module) ---------------------------------

def test_apply_changes_forward_and_remove_restores():
    m = _build()
    layout = _tiny_layout()
    inputs = _fixed_inputs(layout)
    with torch.no_grad():
        base_v, base_a = _forward(m, layout, inputs)
    lora = _diffusers_qkv_lora("transformer_blocks.0")
    n, unmatched = apply_loras(m, [(lora, 1.0)])
    assert n == 1 and unmatched == []
    with torch.no_grad():
        after_v, after_a = _forward(m, layout, inputs)
    assert not torch.allclose(base_v, after_v)
    remove_loras(m)
    with torch.no_grad():
        restored_v, restored_a = _forward(m, layout, inputs)
    torch.testing.assert_close(restored_v, base_v, atol=1e-4, rtol=1e-3)
    torch.testing.assert_close(restored_a, base_a, atol=1e-4, rtol=1e-3)


# --- fp8_scaled path -----------------------------------------------------------

def test_fp8_module_forward_with_lora_runs():
    """LoRA on a genuinely fp8-dtype-stored qkv_proj goes through the runtime-
    delta hook path (never patches the fp8 storage in place) -- same routing
    criterion as Flux's fp8 test, now exercised against H3's fused-qkv target."""
    m = _build(ops=pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.blocks[0].attn.qkv_proj
    assert qkv.comfy_cast_weights is True
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)
    storage_before = qkv.weight.data.clone()

    layout = _tiny_layout()
    inputs = _fixed_inputs(layout, seed=5)
    with torch.no_grad():
        base_v, _ = _forward(m, layout, inputs)

    lora = _diffusers_qkv_lora("transformer_blocks.0")
    apply_loras(m, [(lora, 1.0)])

    assert qkv.weight.dtype == torch.float8_e4m3fn
    assert qkv.lora_deltas and len(qkv.lora_deltas) == 3
    assert torch.equal(qkv.weight.data.float(), storage_before.float())  # fp8 storage untouched

    with torch.no_grad():
        after_v, _ = _forward(m, layout, inputs)
    assert not torch.allclose(base_v.float(), after_v.float())

    remove_loras(m)
    assert qkv.lora_deltas is None
    assert torch.equal(qkv.weight.data.float(), storage_before.float())


def test_token_refiner_stays_bf16_and_patched_in_place_under_fp8_module():
    """Every Linear in the module is built under the SAME fp8 ops namespace
    (the whole DiT gets one ops choice at load), but token_refiner is bf16 in
    the real checkpoint -- its LoRA must go through the in-place path, not the
    fp8 runtime-delta hook (the Embedding-analog trap class: a Linear class
    doesn't imply its loaded weight is actually quantised). ``_build()``'s
    default fill doesn't naturally produce bf16 (its placeholder tensors start
    at the ops-agnostic default dtype), so -- same as Flux's own fp8 test
    explicitly casting its qkv to fp8 -- this explicitly casts to bf16 to
    reproduce what the REAL pruned checkpoint's token_refiner actually is."""
    m = _build(ops=pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    refiner_qkv = m.token_refiner.blocks[0].attn.qkv_proj
    refiner_qkv.weight.data = refiner_qkv.weight.data.to(torch.bfloat16)
    assert refiner_qkv.weight_scale is None  # no quant sidecar, matching the real file
    w_before = refiner_qkv.weight.data.clone()

    lora = _diffusers_qkv_lora("token_refiner.refiner_blocks.0")
    apply_loras(m, [(lora, 1.0)])

    assert not refiner_qkv.lora_deltas  # NOT the runtime-delta path
    assert not torch.equal(refiner_qkv.weight.data, w_before)  # baked in place


# --- SwiGLU half-swap correctness (bite-checkable against an unfused diffusers reference) ---

def test_swiglu_lora_half_swap_matches_unfused_diffusers_reference():
    """diffusers ships `ff.net.0.proj` as one fused `[value | gate]` Linear
    (`SwiGLU.forward`: `hidden_states, gate = proj(x).chunk(2, -1); return
    hidden_states * silu(gate)` -- value first). The real turbo LoRA's `up`
    (lora_B) rows for this key are therefore also `[value delta | gate
    delta]`. Native `mlp.fc1` (post-fix) instead expects `[gate | value]`
    (ComfyUI's own `_swiglu_eager` convention -- the real consumer of the
    checkpoint this arch loads). This builds a small unfused diffusers-style
    reference (value-first weights, diffusers' own SwiGLU forward, the LoRA
    applied to its own native rows verbatim) and the fused native module
    (gate-first weights -- related to the diffusers ones by the SAME
    channel-preserving half-swap this whole fix assumes -- the FIXED
    `MiniMaxH3MLP.forward`, and the LoRA applied through
    `_swap_swiglu_halves`) and asserts the two agree exactly. Bite-checked by
    applying the LoRA unswapped, which must NOT agree.
    """
    torch.manual_seed(21)
    hidden, ffn, rank = 6, 4, 3

    # The checkpoint's ACTUAL (native, gate-first) fc1/fc2 -- what's really on disk.
    w1_native = torch.randn(2 * ffn, hidden) * 0.1
    w2 = torch.randn(hidden, ffn) * 0.1
    gate_native, value_native = w1_native[:ffn], w1_native[ffn:]

    # diffusers' logically-equivalent (value-first) unfused weight -- the same
    # channel-preserving block swap `_swap_swiglu_halves` assumes.
    w1_diffusers = torch.cat([value_native, gate_native], dim=0)

    def diffusers_forward(x: torch.Tensor, w1: torch.Tensor, w2: torch.Tensor) -> torch.Tensor:
        h = F.linear(x, w1)
        value, gate = h.chunk(2, dim=-1)          # diffusers' own SwiGLU.forward split
        return F.linear(value * F.silu(gate), w2)

    ops = _fp32_ops()
    mlp = MiniMaxH3MLP(hidden, ffn, ops, dtype=torch.float32)
    with torch.no_grad():
        mlp.fc1.weight.copy_(w1_native)
        mlp.fc2.weight.copy_(w2)

    x = torch.randn(2, 5, hidden)

    # sanity: the two UN-patched forwards already agree (confirms the
    # channel-preserving assumption before layering a LoRA on top).
    torch.testing.assert_close(mlp(x), diffusers_forward(x, w1_diffusers, w2))

    # A diffusers-dialect LoRA on ff.net.0.proj: `up` rows are [value delta | gate delta].
    down = torch.randn(rank, hidden) * 0.1
    up_diffusers = torch.randn(2 * ffn, rank) * 0.1
    alpha = float(rank)

    w1_diffusers_patched = w1_diffusers + (up_diffusers @ down) * (alpha / rank)
    y_ref = diffusers_forward(x, w1_diffusers_patched, w2)

    # Native side, through the REAL production helper.
    up_native = _swap_swiglu_halves(up_diffusers)
    w1_native_patched = apply_lora_deltas(
        w1_native, [LoraDelta(down=down, up=up_native, alpha=alpha, scale=1.0, target_slice=None)],
    )
    mlp_patched = MiniMaxH3MLP(hidden, ffn, ops, dtype=torch.float32)
    with torch.no_grad():
        mlp_patched.fc1.weight.copy_(w1_native_patched)
        mlp_patched.fc2.weight.copy_(w2)
    y_native = mlp_patched(x)

    torch.testing.assert_close(y_native, y_ref, atol=1e-5, rtol=1e-4)

    # bite check: apply the SAME LoRA to fc1 WITHOUT the half-swap -> must NOT match.
    w1_native_unswapped = apply_lora_deltas(
        w1_native, [LoraDelta(down=down, up=up_diffusers, alpha=alpha, scale=1.0, target_slice=None)],
    )
    mlp_unswapped = MiniMaxH3MLP(hidden, ffn, ops, dtype=torch.float32)
    with torch.no_grad():
        mlp_unswapped.fc1.weight.copy_(w1_native_unswapped)
        mlp_unswapped.fc2.weight.copy_(w2)
    y_unswapped = mlp_unswapped(x)
    assert not torch.allclose(y_unswapped, y_ref, atol=1e-5, rtol=1e-4)


def test_swap_swiglu_halves_is_its_own_inverse():
    """The swap is a pure half-block transposition -- applying it twice must
    round-trip (bite check against an accidental non-involutive rewrite,
    e.g. shifting by one row instead of swapping the two halves)."""
    torch.manual_seed(22)
    up = torch.randn(2 * 48, 5)
    torch.testing.assert_close(_swap_swiglu_halves(_swap_swiglu_halves(up)), up)
    assert not torch.allclose(_swap_swiglu_halves(up), up)  # sanity: not a no-op


# --- ComfyUI-generic dialect (lightx2v v1.x "comfyui" conversions) -------------
#
# lightx2v also publishes pre-converted files (e.g.
# minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors, real header
# verified: 624 keys, metadata `target_format: "ComfyUI generic LoRA"`). These
# differ from the diffusers/PEFT originals in every way the mapper must NOT
# re-normalise: keys are `diffusion_model.{native_stem}.lora_A/lora_B.weight`
# with per-stem `.alpha` scalars, qkv arrives ALREADY FUSED (block-diagonal B,
# concatenated A, alpha tripled to keep alpha/rank constant), fc1's rows are
# ALREADY gate-first (`swi_glu_mapping: "Diffusers [value;gate] -> ComfyUI
# [gate;value]"`), and the refiner uses the native `token_refiner.blocks`
# spelling. Still no AdaLN/time_embedder keys.

def _comfy_lora(stem: str, out: int, inf: int, rank: int = 4, alpha: float | None = None,
                seed: int = 1) -> dict[str, torch.Tensor]:
    g = torch.Generator().manual_seed(seed)
    return {
        f"diffusion_model.{stem}.lora_A.weight": torch.randn(rank, inf, generator=g) * 0.1,
        f"diffusion_model.{stem}.lora_B.weight": torch.randn(out, rank, generator=g) * 0.1,
        f"diffusion_model.{stem}.alpha": torch.tensor(float(alpha if alpha is not None else rank)),
    }


def _comfy_v1_stack(rank: int = 4) -> dict[str, torch.Tensor]:
    """Every key the real v1.1 file has, on TINY's block counts: 4 targets per
    block, fused qkv at 3x rank with 3x alpha, native refiner spelling."""
    lora: dict[str, torch.Tensor] = {}
    seed = 100
    for prefix, layers in (("blocks", TINY["num_layers"]),
                           ("token_refiner.blocks", TINY["num_refiner_layers"])):
        for i in range(layers):
            pf = f"{prefix}.{i}"
            lora.update(_comfy_lora(f"{pf}.attn.qkv_proj", 3 * INNER, HIDDEN,
                                    rank=3 * rank, alpha=3 * rank, seed=seed))
            lora.update(_comfy_lora(f"{pf}.attn.out_proj", HIDDEN, INNER, rank=rank, seed=seed + 1))
            lora.update(_comfy_lora(f"{pf}.mlp.fc1", 2 * 48, HIDDEN, rank=rank, seed=seed + 2))
            lora.update(_comfy_lora(f"{pf}.mlp.fc2", HIDDEN, 48, rank=rank, seed=seed + 3))
            seed += 10
    return lora


def test_comfy_generic_v1_stack_maps_whole_weights_with_zero_unmatched():
    lora = _comfy_v1_stack()
    mapped, unmatched = map_lora_keys(lora, _build())
    assert unmatched == []
    # 4 targets per (2 + 1) blocks, ONE whole-weight delta each -- never the
    # 3-slice or half-swap treatment the diffusers dialect needs.
    assert len(mapped) == 4 * (TINY["num_layers"] + TINY["num_refiner_layers"])
    for param, deltas in mapped.items():
        assert len(deltas) == 1
        assert deltas[0].target_slice is None
    # fused qkv: rank read off the concatenated A, alpha off the tripled .alpha
    # key, so alpha/rank stays exactly what the un-fused original trained.
    (qkv,) = mapped["blocks.0.attn.qkv_proj.weight"]
    assert qkv.down.shape[0] == 12 and qkv.alpha == 12.0
    # fc1's up is applied VERBATIM -- the file is already gate-first; a mapper
    # that re-applied the diffusers half-swap here would corrupt it.
    raw_fc1_up = lora["diffusion_model.blocks.0.mlp.fc1.lora_B.weight"]
    (fc1,) = mapped["blocks.0.mlp.fc1.weight"]
    torch.testing.assert_close(fc1.up, raw_fc1_up)


def test_comfy_generic_fc1_equals_diffusers_fc1_of_the_same_logical_lora():
    """One logical fc1 LoRA, both published dialects: the diffusers original
    ([value|gate] rows, mapper applies the half-swap) and the comfyui
    conversion (the SAME tensor pre-swapped to [gate|value], mapper must pass
    it through). Both routes must land the identical delta on fc1 -- and the
    comfy route must NOT equal the diffusers route's raw input (bite check
    that the comfy path really skips the swap)."""
    g = torch.Generator().manual_seed(7)
    down = torch.randn(4, HIDDEN, generator=g) * 0.1
    up_diffusers = torch.randn(2 * 48, 4, generator=g) * 0.1

    diffusers_sd = {
        "transformer_blocks.0.ff.net.0.proj.lora_A.default.weight": down,
        "transformer_blocks.0.ff.net.0.proj.lora_B.default.weight": up_diffusers,
    }
    comfy_sd = {
        "diffusion_model.blocks.0.mlp.fc1.lora_A.weight": down,
        "diffusion_model.blocks.0.mlp.fc1.lora_B.weight": _swap_swiglu_halves(up_diffusers),
        "diffusion_model.blocks.0.mlp.fc1.alpha": torch.tensor(4.0),
    }
    m = _build()
    (d_diff,) = map_lora_keys(diffusers_sd, m)[0]["blocks.0.mlp.fc1.weight"]
    (d_comfy,) = map_lora_keys(comfy_sd, m)[0]["blocks.0.mlp.fc1.weight"]
    torch.testing.assert_close(d_comfy.up, d_diff.up)
    torch.testing.assert_close(d_comfy.down, d_diff.down)
    assert d_comfy.alpha == d_diff.alpha == 4.0
    assert not torch.allclose(d_comfy.up, up_diffusers)


def test_comfy_generic_apply_changes_forward_and_remove_restores():
    m = _build()
    layout = _tiny_layout()
    inputs = _fixed_inputs(layout, seed=9)
    with torch.no_grad():
        base_v, base_a = _forward(m, layout, inputs)
    n, unmatched = apply_loras(m, [(_comfy_v1_stack(), 1.0)])
    assert n == 4 * (TINY["num_layers"] + TINY["num_refiner_layers"]) and unmatched == []
    with torch.no_grad():
        after_v, after_a = _forward(m, layout, inputs)
    assert not torch.allclose(base_v, after_v)
    assert not torch.allclose(base_a, after_a)
    remove_loras(m)
    with torch.no_grad():
        restored_v, restored_a = _forward(m, layout, inputs)
    torch.testing.assert_close(restored_v, base_v, atol=1e-4, rtol=1e-3)
    torch.testing.assert_close(restored_a, base_a, atol=1e-4, rtol=1e-3)


# --- slice-offset correctness (bite-checkable against an unfused reference) ---

def test_slice_offsets_match_unfused_reference_and_swap_bite_check():
    torch.manual_seed(11)
    rank = 4
    wq = torch.randn(INNER, HIDDEN) * 0.02
    wk = torch.randn(INNER, HIDDEN) * 0.02
    wv = torch.randn(INNER, HIDDEN) * 0.02
    fused = torch.cat([wq, wk, wv], dim=0)  # matches qkv_proj's [3*inner, hidden] layout

    up_q, down_q = torch.randn(INNER, rank) * 0.1, torch.randn(rank, HIDDEN) * 0.1
    up_k, down_k = torch.randn(INNER, rank) * 0.1, torch.randn(rank, HIDDEN) * 0.1
    up_v, down_v = torch.randn(INNER, rank) * 0.1, torch.randn(rank, HIDDEN) * 0.1

    # independent reference: patch each unfused matrix separately, then fuse.
    reference = torch.cat([
        wq + up_q @ down_q,
        wk + up_k @ down_k,
        wv + up_v @ down_v,
    ], dim=0)

    correct = [
        LoraDelta(down=down_q, up=up_q, alpha=float(rank), scale=1.0, target_slice=(0, 0, INNER)),
        LoraDelta(down=down_k, up=up_k, alpha=float(rank), scale=1.0, target_slice=(0, INNER, INNER)),
        LoraDelta(down=down_v, up=up_v, alpha=float(rank), scale=1.0, target_slice=(0, 2 * INNER, INNER)),
    ]
    got = apply_lora_deltas(fused.clone(), correct)
    torch.testing.assert_close(got, reference, atol=1e-5, rtol=1e-4)

    # bite check: swap the k/v slice offsets -> must NOT match the reference.
    swapped = [
        LoraDelta(down=down_q, up=up_q, alpha=float(rank), scale=1.0, target_slice=(0, 0, INNER)),
        LoraDelta(down=down_k, up=up_k, alpha=float(rank), scale=1.0, target_slice=(0, 2 * INNER, INNER)),
        LoraDelta(down=down_v, up=up_v, alpha=float(rank), scale=1.0, target_slice=(0, INNER, INNER)),
    ]
    got_swapped = apply_lora_deltas(fused.clone(), swapped)
    assert not torch.allclose(got_swapped, reference, atol=1e-5, rtol=1e-4)

    # confirms the map's ACTUAL offsets are the q|k|v ones the model uses --
    # cross-checked against build_minimax_h3_lora_key_map directly.
    km = build_minimax_h3_lora_key_map(_build())
    assert km["transformer.transformer_blocks.0.attn.to_q"][1] == (0, 0, INNER)
    assert km["transformer.transformer_blocks.0.attn.to_k"][1] == (0, INNER, INNER)
    assert km["transformer.transformer_blocks.0.attn.to_v"][1] == (0, 2 * INNER, INNER)
