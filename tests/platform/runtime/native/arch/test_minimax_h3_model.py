"""Tests for the vendored MiniMax-H3 packed-sequence DiT.

Coverage: config validation, meta-device key-set parity against the REAL
``ai/minimax_h3/{full_bf16,pruned_fp8}_header.json`` fixtures (the strongest
check available without real weights), detect<->config round trips, tiny-
config packed-sequence forward smoke (both AdaLN modes), the fused-qkv q/k/v
split order, the SwiGLU gate-is-second-half convention, an AdaLN
full-vs-pruned equivalence test that validates the curve-lookup derivation,
and the FBCache step-cache hook.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import (
    MiniMaxH3Attention,
    MiniMaxH3MLP,
    MiniMaxH3Model,
)
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache
from src.platform.runtime.native.sla_attn import SlaAttnContext
from src.platform.runtime.native.sol_attn import SolAttnContext
from vendor.gpl.comfyui.ops import pick_operations

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HEADER_DIR = _REPO_ROOT / "ai" / "minimax_h3"

# Exact config detect_unet_config derives from the real headers.
REAL_CONFIG_FULL = {
    "image_model": "minimax_h3", "hidden_size": 5376, "num_layers": 50,
    "num_refiner_layers": 2, "num_attention_heads": 56, "attention_head_dim": 128,
    "ffn_dim": 14336, "in_channels": 24, "audio_in_channels": 32,
    "patch_size": (1, 2, 2), "text_dim": 5120, "rope_freq_dim": 16,
    "pruned": False, "time_embed_dim": 2688, "freq_dim": 256, "time_embed_hidden_dim": 5376,
}
REAL_CONFIG_PRUNED = {
    k: v for k, v in REAL_CONFIG_FULL.items() if k not in ("freq_dim", "time_embed_hidden_dim")
}
REAL_CONFIG_PRUNED = dict(REAL_CONFIG_PRUNED, pruned=True, time_embed_dim=8, adaln_curve_grid=1025)

# Tiny: keeps the real model's two load-bearing shape traps (attn inner !=
# hidden: heads(2) * head_dim(40) = 80 != hidden(64); ffn fc1 fuses value+gate).
TINY_COMMON = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 4,
    "audio_in_channels": 6, "patch_size": (1, 2, 2), "text_dim": 10, "rope_freq_dim": 3,
}
TINY_FULL = dict(TINY_COMMON, pruned=False, time_embed_dim=12, freq_dim=8, time_embed_hidden_dim=16)
TINY_PRUNED = dict(TINY_COMMON, pruned=True, time_embed_dim=6, adaln_curve_grid=5)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build_ready(config: dict) -> MiniMaxH3Model:
    m = MiniMaxH3Model.from_config(config, _fp32_ops())
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


def _tiny_layout(text_n: int = 2, video_n: int = 3, audio_n: int = 2) -> dict[str, torch.Tensor]:
    seq_len = text_n + video_n + audio_n
    text_indices = torch.arange(0, text_n)
    video_indices = torch.arange(text_n, text_n + video_n)
    audio_indices = torch.arange(text_n + video_n, seq_len)
    token_tags = torch.zeros(seq_len, dtype=torch.long)
    token_tags[text_indices] = 1
    token_tags[audio_indices] = 2
    # per-row timesteps: video/text pinned at index 0, audio at its OWN index 1
    # -- exercises the model actually consuming distinct rows differently.
    timestep_indices = torch.zeros(seq_len, dtype=torch.long)
    timestep_indices[audio_indices] = 1
    position_ids = torch.rand(seq_len, 3, dtype=torch.float64)
    return dict(
        text_indices=text_indices, video_indices=video_indices, audio_indices=audio_indices,
        token_tags=token_tags, timestep_indices=timestep_indices, position_ids=position_ids,
    )


# --- config -----------------------------------------------------------------

def test_config_from_detect_config_full():
    c = MiniMaxH3Config.from_detect_config(REAL_CONFIG_FULL)
    assert c.pruned is False
    assert c.hidden_size == 5376
    assert c.inner_dim == 56 * 128
    assert c.video_patch_dim == 24 * 4
    assert c.time_embed_dim == 2688


def test_config_from_detect_config_pruned():
    c = MiniMaxH3Config.from_detect_config(REAL_CONFIG_PRUNED)
    assert c.pruned is True
    assert c.time_embed_dim == 8
    assert c.adaln_curve_grid == 1025


def test_config_rejects_wrong_image_model():
    with pytest.raises(ValueError, match="image_model"):
        MiniMaxH3Config.from_detect_config(dict(REAL_CONFIG_FULL, image_model="wan2.1"))


# --- meta key-set parity against the REAL headers ---------------------------

def test_full_meta_keyset_parity_against_real_header():
    header = json.loads((_HEADER_DIR / "full_bf16_header.json").read_text())
    header.pop("__metadata__", None)
    real_keys = set(header.keys())
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_FULL, pick_operations(torch.bfloat16, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == real_keys, (
        f"missing={sorted(real_keys - built)[:20]} extra={sorted(built - real_keys)[:20]}")


_QUANT_SIDECAR_SUFFIXES = (".weight_scale", ".input_scale", ".comfy_quant")


def test_pruned_meta_keyset_parity_against_real_header_minus_quant_sidecars():
    # weight_scale/input_scale/comfy_quant are consumed by Fp8ScaledLinear's own
    # _load_from_state_dict (popped before the strict key check ever sees them)
    # and registered as NON-persistent buffers, so they never appear in this
    # module's own state_dict() -- excluded here for a fair comparison, not
    # because the loader can't handle them (it can; see registry.py's
    # expected_unexpected_keys, which is defense-in-depth for the same reason).
    header = json.loads((_HEADER_DIR / "pruned_fp8_header.json").read_text())
    header.pop("__metadata__", None)
    real_keys = {k for k in header if not k.endswith(_QUANT_SIDECAR_SUFFIXES)}
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_PRUNED, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == real_keys, (
        f"missing={sorted(real_keys - built)[:20]} extra={sorted(built - real_keys)[:20]}")


# --- detect <-> config round trip -------------------------------------------

def test_detect_matches_real_full_config_exactly():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_FULL, pick_operations(torch.bfloat16, torch.bfloat16))
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in m.state_dict().items()}
    assert detect_unet_config(sd) == REAL_CONFIG_FULL


def test_detect_matches_real_pruned_config_exactly():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(REAL_CONFIG_PRUNED, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in m.state_dict().items()}
    assert detect_unet_config(sd) == REAL_CONFIG_PRUNED


def test_detect_spec_from_config_roundtrip():
    with torch.device("meta"):
        seed = MiniMaxH3Model.from_config(TINY_FULL, _fp32_ops())
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in seed.state_dict().items()}
    config = detect_unet_config(sd)
    spec = match_model_spec(config)
    assert spec.family == "minimax_h3"
    with torch.device("meta"):
        rebuilt = MiniMaxH3Model.from_config(config, _fp32_ops())
    assert set(rebuilt.state_dict().keys()) == set(seed.state_dict().keys())


def test_is_native_arch_module():
    with torch.device("meta"):
        m = MiniMaxH3Model.from_config(TINY_FULL, _fp32_ops())
    assert isinstance(m, NativeArchModule)


def test_post_load_recomputes_inv_freq_in_fp32():
    m = _build_ready(TINY_FULL)  # load_into_module already ran post_load once
    theta, dim = m.config.rope_theta, m.config.rope_freq_dim
    expected = 1.0 / (theta ** (torch.arange(0, 2 * dim, 2, dtype=torch.float32) / (2 * dim)))
    torch.testing.assert_close(m.rope.inv_freq, expected)
    assert m.rope.inv_freq.dtype == torch.float32


# --- packed-sequence forward smoke ------------------------------------------

def _forward(m: MiniMaxH3Model, config: dict, layout: dict, timestep: torch.Tensor):
    video_patch_dim = config["in_channels"] * 4
    hidden_states = torch.randn(1, layout["video_indices"].numel(), video_patch_dim)
    audio_hidden_states = torch.randn(1, layout["audio_indices"].numel(), config["audio_in_channels"])
    encoder_hidden_states = torch.randn(1, layout["text_indices"].numel(), config["text_dim"])
    return m(
        hidden_states, audio_hidden_states, encoder_hidden_states,
        timestep, layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
        layout["video_indices"], layout["audio_indices"], layout["text_indices"],
    )


def test_tiny_forward_shape_full_mode():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    video_out, audio_out = _forward(m, TINY_FULL, layout, torch.tensor([0.2, 0.9]))
    assert video_out.shape == (1, layout["video_indices"].numel(), TINY_FULL["in_channels"] * 4)
    assert audio_out.shape == (1, layout["audio_indices"].numel(), TINY_FULL["audio_in_channels"])
    assert torch.isfinite(video_out).all()
    assert torch.isfinite(audio_out).all()


def test_tiny_forward_shape_pruned_mode():
    m = _build_ready(TINY_PRUNED)
    layout = _tiny_layout()
    video_out, audio_out = _forward(m, TINY_PRUNED, layout, torch.tensor([0.2, 0.9]))
    assert video_out.shape == (1, layout["video_indices"].numel(), TINY_PRUNED["in_channels"] * 4)
    assert audio_out.shape == (1, layout["audio_indices"].numel(), TINY_PRUNED["audio_in_channels"])
    assert torch.isfinite(video_out).all()
    assert torch.isfinite(audio_out).all()


def test_forward_batch_and_larger_layout():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout(text_n=5, video_n=6, audio_n=4)
    video_out, audio_out = _forward(m, TINY_FULL, layout, torch.tensor([0.1, 0.6]))
    assert video_out.shape[1] == 6
    assert audio_out.shape[1] == 4


def test_forward_rejects_bad_position_ids_rank():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    layout["position_ids"] = layout["position_ids"][:, :2]  # wrong last dim
    with pytest.raises(ValueError, match="position_ids"):
        _forward(m, TINY_FULL, layout, torch.tensor([0.2, 0.9]))


def test_pe_cache_invalidated_by_apply():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    m._prepare_positional_embeddings(layout["position_ids"])
    assert m._pe_cache_key is not None
    m.float()  # any _apply call (dtype/device move) must drop the cache
    assert m._pe_cache_key is None
    assert m._pe_cache is None


# --- fused qkv split order (bite-checkable: flip the chunk order to fail) ---

def test_qkv_fused_split_matches_separate_projections_in_q_k_v_order():
    torch.manual_seed(3)
    ops = _fp32_ops()
    hidden, heads, head_dim = 8, 2, 5  # inner(10) != hidden(8), like the real model
    attn = MiniMaxH3Attention(hidden, heads, head_dim, 1e-5, ops, dtype=torch.float32)
    with torch.no_grad():
        attn.qkv_proj.weight.copy_(torch.randn_like(attn.qkv_proj.weight))
        attn.q_norm.weight.copy_(torch.ones_like(attn.q_norm.weight))
        attn.k_norm.weight.copy_(torch.ones_like(attn.k_norm.weight))

    inner = heads * head_dim
    w = attn.qkv_proj.weight
    wq, wk, wv = w[:inner], w[inner:2 * inner], w[2 * inner:]
    x = torch.randn(1, 3, hidden)
    q_ref = F.linear(x, wq).view(1, 3, heads, head_dim)
    k_ref = F.linear(x, wk).view(1, 3, heads, head_dim)
    v_ref = F.linear(x, wv).view(1, 3, heads, head_dim)
    q_ref = F.rms_norm(q_ref, (head_dim,), torch.ones(head_dim), eps=1e-5)
    k_ref = F.rms_norm(k_ref, (head_dim,), torch.ones(head_dim), eps=1e-5)

    captured: dict[str, torch.Tensor] = {}
    import src.platform.runtime.native.arch.minimax_h3.model as mod
    orig = mod._dispatch_attention

    def spy(q, k, v, **kw):
        captured["q"] = q.clone()
        captured["k"] = k.clone()
        captured["v"] = v.clone()
        return orig(q, k, v, **kw)

    mod._dispatch_attention = spy
    try:
        attn(x, rotary_emb=None)
    finally:
        mod._dispatch_attention = orig

    # captured tensors are (B, H, S, D) -- transpose back to (B, S, H, D).
    torch.testing.assert_close(captured["q"].transpose(1, 2), q_ref, atol=1e-5, rtol=1e-4)
    torch.testing.assert_close(captured["k"].transpose(1, 2), k_ref, atol=1e-5, rtol=1e-4)
    torch.testing.assert_close(captured["v"].transpose(1, 2), v_ref, atol=1e-5, rtol=1e-4)


# --- SwiGLU gate = FIRST half of fc1 (bite-checkable) ------------------------

def test_swiglu_gate_is_first_half_of_fc1_output():
    """Ground truth is ComfyUI's own ``_swiglu_eager`` (comfy/ops.py) -- the
    real, working consumer of this exact Comfy-Org single-file repack:
    ``gate, up = x.chunk(2, dim=-1); return silu(gate) * up``. This must be
    written independently of ``MiniMaxH3MLP.forward``'s own chunk order (not
    copy its variable names) or a regressed chunk order can never fail this
    test -- see the previous version of this test, which encoded diffusers'
    (wrong, for this checkpoint) value-first/gate-second ordering by mirroring
    the implementation instead of an independent reference, and could not
    catch the swap that shipped."""
    torch.manual_seed(0)
    ops = _fp32_ops()
    hidden, ffn = 6, 4
    mlp = MiniMaxH3MLP(hidden, ffn, ops, dtype=torch.float32)
    with torch.no_grad():
        mlp.fc1.weight.copy_(torch.randn_like(mlp.fc1.weight))
        mlp.fc2.weight.copy_(torch.randn_like(mlp.fc2.weight))
    x = torch.randn(1, 2, hidden)

    fc1_out = F.linear(x, mlp.fc1.weight)
    gate, up = fc1_out.chunk(2, dim=-1)  # ComfyUI's own convention, independent of forward()
    expected = F.linear(F.silu(gate) * up, mlp.fc2.weight)

    torch.testing.assert_close(mlp(x), expected)


def test_swiglu_is_not_symmetric_under_half_swap():
    """Bite-check for the test above: if ``mlp.forward`` silently swapped
    which half gets SiLU'd, this constructs an ``fc1``/``x`` pair where
    swapping the two halves changes the result by more than float noise --
    i.e. the reference test above is actually discriminating, not vacuously
    true because gate/value happened to coincide."""
    torch.manual_seed(1)
    ops = _fp32_ops()
    hidden, ffn = 6, 4
    mlp = MiniMaxH3MLP(hidden, ffn, ops, dtype=torch.float32)
    with torch.no_grad():
        mlp.fc1.weight.copy_(torch.randn_like(mlp.fc1.weight))
        mlp.fc2.weight.copy_(torch.randn_like(mlp.fc2.weight))
    x = torch.randn(1, 2, hidden)

    fc1_out = F.linear(x, mlp.fc1.weight)
    gate, up = fc1_out.chunk(2, dim=-1)
    correct = F.linear(F.silu(gate) * up, mlp.fc2.weight)
    swapped = F.linear(F.silu(up) * gate, mlp.fc2.weight)

    assert not torch.allclose(correct, swapped, atol=1e-4, rtol=1e-3)


# --- AdaLN full-vs-pruned equivalence (validates the curve derivation) ------

def test_pruned_curve_reproduces_full_modulation_at_grid_points():
    """Bakes a tiny full model's SiLU'd temb into a pruned model's adaln_t_table
    at the table's own grid points, reuses the SAME adaln_proj weights between
    the two, and asserts the two paths produce IDENTICAL modulation at every
    grid point. This is exact iff both halves of the port are right together:
    (a) the curve lookup returns the table row verbatim at a knot, and (b) the
    pruned path skips SiLU (so it doesn't double-activate an already-activated
    table row) -- flip either one and this test fails."""
    torch.manual_seed(7)
    ops = _fp32_ops()
    grid = 9
    full_config_dict = dict(TINY_FULL, time_embed_dim=12)
    full_config = MiniMaxH3Config.from_detect_config(full_config_dict)
    full = MiniMaxH3Model(full_config, ops, dtype=torch.float32)
    with torch.no_grad():
        for p in full.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
    full.eval()

    t_grid = torch.linspace(0.0, 1.0, grid)
    with torch.no_grad():
        temb_full_raw = full.time_embedder(t_grid)          # (grid, time_embed_dim), PRE-SiLU
        table = F.silu(temb_full_raw)                        # bake the activation into the curve

    pruned_config_dict = dict(TINY_FULL, pruned=True, adaln_curve_grid=grid, time_embed_dim=12)
    del pruned_config_dict["freq_dim"], pruned_config_dict["time_embed_hidden_dim"]
    pruned_config = MiniMaxH3Config.from_detect_config(pruned_config_dict)
    pruned = MiniMaxH3Model(pruned_config, ops, dtype=torch.float32)
    with torch.no_grad():
        for p in pruned.parameters():
            p.copy_(torch.randn_like(p) * 0.02)
        pruned.adaln_t_table.copy_(table)
        for fb, pb in zip(full.blocks, pruned.blocks):
            pb.adaln_proj.linear.weight.copy_(fb.adaln_proj.linear.weight)
            pb.adaln_proj.linear.bias.copy_(fb.adaln_proj.linear.bias)
        pruned.final_layer.adaln_proj.linear.weight.copy_(full.final_layer.adaln_proj.linear.weight)
        pruned.final_layer.adaln_proj.linear.bias.copy_(full.final_layer.adaln_proj.linear.bias)
    pruned.eval()

    with torch.no_grad():
        full_mod = full.blocks[0].adaln_proj(temb_full_raw)
        pruned_temb = pruned._lookup_adaln_curve(t_grid)
        torch.testing.assert_close(pruned_temb, table, atol=1e-6, rtol=1e-6)  # exact at knots
        pruned_mod = pruned.blocks[0].adaln_proj(pruned_temb)

    for full_chunk, pruned_chunk in zip(full_mod, pruned_mod):
        torch.testing.assert_close(full_chunk, pruned_chunk, atol=1e-5, rtol=1e-4)


def test_adaln_curve_lookup_clamps_out_of_range_t():
    m = _build_ready(TINY_PRUNED)
    grid, width = TINY_PRUNED["adaln_curve_grid"], TINY_PRUNED["time_embed_dim"]
    with torch.no_grad():
        m.adaln_t_table.copy_(torch.arange(grid * width, dtype=torch.float32).view(grid, width))

    for i in range(grid):
        t = torch.tensor([i / (grid - 1)])
        out = m._lookup_adaln_curve(t)
        torch.testing.assert_close(out[0], m.adaln_t_table[i])

    mid = torch.tensor([0.5 / (grid - 1)])
    expected_mid = (m.adaln_t_table[0] + m.adaln_t_table[1]) / 2
    torch.testing.assert_close(m._lookup_adaln_curve(mid)[0], expected_mid)

    torch.testing.assert_close(m._lookup_adaln_curve(torch.tensor([5.0]))[0], m.adaln_t_table[-1])
    torch.testing.assert_close(m._lookup_adaln_curve(torch.tensor([-3.0]))[0], m.adaln_t_table[0])


# --- FBCache (first-block step cache) ---------------------------------------

def _fbcache_inputs(config: dict, layout: dict) -> dict[str, torch.Tensor]:
    video_patch_dim = config["in_channels"] * 4
    return dict(
        hidden_states=torch.randn(1, layout["video_indices"].numel(), video_patch_dim),
        audio_hidden_states=torch.randn(1, layout["audio_indices"].numel(), config["audio_in_channels"]),
        encoder_hidden_states=torch.randn(1, layout["text_indices"].numel(), config["text_dim"]),
    )


def _fbcache_forward(m: MiniMaxH3Model, layout: dict, inputs: dict, timestep: torch.Tensor, **kwargs):
    with torch.inference_mode():
        return m(
            inputs["hidden_states"], inputs["audio_hidden_states"], inputs["encoder_hidden_states"],
            timestep, layout["timestep_indices"], layout["token_tags"], layout["position_ids"],
            layout["video_indices"], layout["audio_indices"], layout["text_indices"],
            **kwargs,
        )


def _count_calls(module) -> dict[str, int]:
    """Wrap ``module.forward`` with a counter, so a skipped step is proved by
    the wrapped module never running again rather than by output equality
    alone (which a re-run would also satisfy)."""
    calls = {"n": 0}
    original = module.forward

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    module.forward = counting
    return calls


def test_fbcache_step_cache_none_is_byte_identical():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(11)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    base = _fbcache_forward(m, layout, inputs, ts)
    with_none = _fbcache_forward(m, layout, inputs, ts, step_cache=None)
    for a, b in zip(base, with_none):
        assert torch.equal(a, b)


def test_fbcache_identical_inputs_skip_replays_both_streams():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(12)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    first = _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}

    video_out, audio_out = second
    assert torch.equal(video_out, first[0])
    assert torch.equal(audio_out, first[1])


def test_fbcache_skip_bypasses_later_blocks_and_final_layer():
    m = _build_ready(TINY_FULL)  # num_layers=2 -> blocks[-1] is block 1
    layout = _tiny_layout()
    torch.manual_seed(13)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    block_calls = _count_calls(m.blocks[-1])
    final_calls = _count_calls(m.final_layer)

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert (block_calls["n"], final_calls["n"]) == (1, 1)
    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert (block_calls["n"], final_calls["n"]) == (1, 1)


def test_fbcache_disabled_threshold_never_skips():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(14)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.0, warmup_steps=0)

    base = _fbcache_forward(m, layout, inputs, ts)
    for _ in range(3):
        out = _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
        for a, b in zip(base, out):
            assert torch.equal(a, b)
    # A disabled cache still refreshes its anchors (harmless); what matters is
    # that it never skips, so every step's output is the real forward's.
    assert cache.stats() == {"computed": 3, "skipped": 0}


def test_fbcache_warmup_forces_real_computes():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(15)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=3)

    for _ in range(3):
        _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert cache.stats() == {"computed": 3, "skipped": 0}
    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert cache.stats() == {"computed": 3, "skipped": 1}


def test_fbcache_max_consecutive_skips_forces_a_recompute():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(16)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0, max_consecutive_skips=2)

    for _ in range(4):
        _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 2}


def test_fbcache_changed_input_recomputes():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(17)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    moved = dict(inputs, hidden_states=inputs["hidden_states"] * 5.0)
    _fbcache_forward(m, layout, moved, ts, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_fbcache_audio_only_change_recomputes():
    """The probe is block-0's output over the WHOLE packed sequence, so a
    change confined to the audio rows must still break the cache -- H3 packs
    both modalities into one stream, and a video-row-only probe would reuse a
    stale audio velocity."""
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(18)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    moved = dict(inputs, audio_hidden_states=inputs["audio_hidden_states"] * 5.0)
    _fbcache_forward(m, layout, moved, ts, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_fbcache_sequence_length_change_forces_compute():
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(19)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache)
    bigger_layout = _tiny_layout(text_n=2, video_n=5, audio_n=2)
    bigger = _fbcache_inputs(TINY_FULL, bigger_layout)
    _fbcache_forward(m, bigger_layout, bigger, ts, step_cache=cache)
    assert cache.stats()["skipped"] == 0


# --- sparse attention (opt-in Sol-Attn / SLA, dispatched by context type) ---

def _sparse_attn_recorder(monkeypatch, result=None):
    """Replace the model module's `sparse_attention` seam and record every
    context it is handed. Returning None (the default) is what the real seam
    does on any machine that cannot run either backend, so the forward must
    stay dense."""
    seen: list = []

    def recording(q, k, v, ctx):
        seen.append(ctx)
        # Mirrors the real seam: no context is always the dense path, whatever
        # the stub would otherwise return. Without this the refiner's own
        # attention would be stubbed too and a "did the sparse result reach the
        # output" test could pass against an equally-stubbed baseline.
        if ctx is None or result is None:
            return None
        return result(q)

    monkeypatch.setattr(
        "src.platform.runtime.native.arch.minimax_h3.model.sparse_attention", recording,
    )
    return seen


def test_sparse_attn_context_absent_is_byte_identical():
    """The opt-out: no context, and the packed forward is exactly what it was
    before either sparse-attention option existed."""
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(31)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    base = _fbcache_forward(m, layout, inputs, ts)
    with_none = _fbcache_forward(m, layout, inputs, ts, sparse_attn_ctx=None)
    for a, b in zip(base, with_none):
        assert torch.equal(a, b)


def test_sol_attn_context_reaches_every_main_block_and_no_refiner_block(monkeypatch):
    """The refiner runs a short text-only sequence with nothing to route over,
    so it must keep calling the dense path — proved by its attention receiving
    a None context while all `num_layers` main blocks receive the real one."""
    seen = _sparse_attn_recorder(monkeypatch)
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(32)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ctx = SolAttnContext(tau=1.2, sink_tokens=4)

    _fbcache_forward(m, layout, inputs, torch.tensor([0.2, 0.9]), sparse_attn_ctx=ctx)

    assert [c for c in seen if c is ctx] == [ctx] * TINY_FULL["num_layers"]
    assert len([c for c in seen if c is None]) == TINY_FULL["num_refiner_layers"]


def test_sla_attn_context_reaches_every_main_block_and_no_refiner_block(monkeypatch):
    """Same routing contract as Sol-Attn, proved against the other context
    type — the seam dispatches on the context's own type, not on which one
    the caller happened to use first."""
    seen = _sparse_attn_recorder(monkeypatch)
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(38)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ctx = SlaAttnContext(sparsity=0.9, block_size=64, prefix_tokens=4)

    _fbcache_forward(m, layout, inputs, torch.tensor([0.2, 0.9]), sparse_attn_ctx=ctx)

    assert [c for c in seen if c is ctx] == [ctx] * TINY_FULL["num_layers"]
    assert len([c for c in seen if c is None]) == TINY_FULL["num_refiner_layers"]


def test_sol_attn_refusal_leaves_the_output_unchanged(monkeypatch):
    """A machine that cannot run the backend returns None from the seam; the
    generation must be bit-identical to one that never asked for it."""
    _sparse_attn_recorder(monkeypatch)
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(33)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    base = _fbcache_forward(m, layout, inputs, ts)
    refused = _fbcache_forward(m, layout, inputs, ts, sparse_attn_ctx=SolAttnContext())
    for a, b in zip(base, refused):
        assert torch.equal(a, b)


def test_sol_attn_output_is_consumed_by_the_out_projection(monkeypatch):
    """The other half of the previous test: when the seam DOES return a result
    it has to reach the block's output, not be computed and dropped."""
    _sparse_attn_recorder(monkeypatch, result=lambda q: torch.zeros_like(q))
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(34)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    base = _fbcache_forward(m, layout, inputs, ts)
    sparse = _fbcache_forward(m, layout, inputs, ts, sparse_attn_ctx=SolAttnContext())
    assert not any(torch.equal(a, b) for a, b in zip(base, sparse))


def test_sol_attn_real_seam_disables_itself_on_cpu(caplog):
    """End to end through the REAL seam (no monkeypatching): a CPU tiny model
    asking for Sol-Attn logs one warning and produces the dense result."""
    from src.platform.runtime.native.sol_attn import reset_sol_attn_state, sol_attn_disabled_reason

    reset_sol_attn_state()
    try:
        m = _build_ready(TINY_FULL)
        # Long enough to clear the seam's short-sequence skip, which fires
        # before the machine check and would otherwise return early.
        layout = _tiny_layout(text_n=2, video_n=300, audio_n=2)
        torch.manual_seed(35)
        inputs = _fbcache_inputs(TINY_FULL, layout)
        ts = torch.tensor([0.2, 0.9])
        base = _fbcache_forward(m, layout, inputs, ts)
        with caplog.at_level(logging.WARNING, logger="src.platform.runtime.native.sol_attn"):
            asked = _fbcache_forward(m, layout, inputs, ts, sparse_attn_ctx=SolAttnContext(sink_tokens=2))
        for a, b in zip(base, asked):
            assert torch.equal(a, b)
        assert sol_attn_disabled_reason() is not None
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    finally:
        reset_sol_attn_state()


def test_sol_attn_composes_with_the_step_cache(monkeypatch):
    """Independent mechanisms: a cached skip returns after block 0, so it never
    reaches the blocks the sparse method would have accelerated, and asking
    for both changes neither one's bookkeeping."""
    seen = _sparse_attn_recorder(monkeypatch)
    m = _build_ready(TINY_FULL)
    layout = _tiny_layout()
    torch.manual_seed(36)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    ts = torch.tensor([0.2, 0.9])
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    ctx = SolAttnContext()

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache, sparse_attn_ctx=ctx)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    first_pass = len([c for c in seen if c is ctx])

    _fbcache_forward(m, layout, inputs, ts, step_cache=cache, sparse_attn_ctx=ctx)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    # The skipped step ran block 0 only, so exactly one more sparse-eligible
    # attention happened, not another full stack.
    assert len([c for c in seen if c is ctx]) == first_pass + 1
