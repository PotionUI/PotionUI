# Reference math in this file (`_ref_*` functions) is transcribed FRESH,
# for TEST-ONLY comparison, from ComfyUI's `comfy/ldm/minimax/model.py`
# (GPL-3.0, comfyanonymous and contributors) -- the real, working consumer of
# the exact checkpoint this test loads. Transcribed independently of, and
# never calling into, this repo's own `arch/minimax_h3/model.py` -- the point
# of this file is an independent second implementation to catch a bug the
# first implementation's own tests structurally cannot see (see
# `tests_that_cannot_fail` project convention: a test that re-derives the
# thing it's checking from the implementation itself can never fail).
# RoPE is the one exception forced by circumstance: ComfyUI's real rotary
# application is a compiled kernel (`comfy.quant_ops.ck.rms_rope_split_half_`,
# not visible Python), so `_ref_apply_rope` instead re-derives the
# mathematically equivalent rotate-half form directly from
# `rope_rotation_table`'s own (visible, Python) 2x2-matrix construction --
# see that function's docstring for the derivation, not a copy of this
# repo's `_apply_rotary_emb`.
"""Real-weights numerical parity: our MiniMax-H3 port vs. an independent
ComfyUI-transcribed reference, at REAL checkpoint scale (hidden 5376, 56
heads x 128, ffn 14336, real fp8 scales) -- not the synthetic tiny configs
every other MiniMax-H3 arch test uses.

Every real tensor is fetched by exact key via `safetensors.safe_open` slice
reads (never the whole ~20 GB file) from the actual pruned-fp8-scaled
checkpoint on disk -- block 0, both refiner blocks, the shared embedders,
`adaln_t_table`, and `final_layer`. Our own module is built with
`num_layers=1` (block 0's real weights only; block count doesn't change any
single block's own math) and loaded through the REAL production
`load_into_module` path, so this exercises the real `Fp8ScaledLinear` fp8
dequant path with the checkpoint's REAL scales -- not zero-filled placeholder
weights (c.f. `test_minimax_h3_loader.py`, which validates load-integrity
plumbing with shrunk, zero-filled tensors, not real trained values).

Skips cleanly (needs-model pattern) when the checkpoint isn't present.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from vendor.gpl.comfyui.ops import pick_operations

_REPO_ROOT = Path(__file__).resolve().parents[5]
_DIT_PATH = _REPO_ROOT / "models" / "diffusion_models" / "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"

pytestmark = [
    pytest.mark.requires_models,
    pytest.mark.skipif(
        not _DIT_PATH.exists(),
        reason="needs the real MiniMax-H3 pruned-fp8 DiT checkpoint on disk (models/diffusion_models/)",
    ),
]

HIDDEN = 5376
HEADS = 56
HEAD_DIM = 128
INNER = HEADS * HEAD_DIM      # 7168 -- wider than HIDDEN, dossier trap
FFN = 14336
ROPE_FREQ_DIM = 16
ROPE_THETA = 10000.0
NORM_EPS = 1e-5
TIME_EMBED_DIM = 8            # pruned rank
ADALN_GRID = 1025

_KEYS = [
    "video_patch_proj.weight", "video_patch_proj.bias",
    "audio_patch_proj.weight", "audio_patch_proj.bias",
    "condition_proj.weight", "condition_proj.bias",
    "adaln_t_table",
    "final_layer.norm.weight",
    "final_layer.adaln_proj.linear.weight", "final_layer.adaln_proj.linear.bias",
    "final_layer.video_out.weight", "final_layer.video_out.bias",
    "final_layer.audio_out.weight", "final_layer.audio_out.bias",
    "token_refiner.final_norm.weight",
    "blocks.0.norm1.weight", "blocks.0.norm2.weight",
    "blocks.0.attn.q_norm.weight", "blocks.0.attn.k_norm.weight",
    "blocks.0.attn.qkv_proj.weight", "blocks.0.attn.qkv_proj.weight_scale", "blocks.0.attn.qkv_proj.input_scale",
    "blocks.0.attn.out_proj.weight", "blocks.0.attn.out_proj.weight_scale", "blocks.0.attn.out_proj.input_scale",
    "blocks.0.mlp.fc1.weight", "blocks.0.mlp.fc1.weight_scale", "blocks.0.mlp.fc1.input_scale",
    "blocks.0.mlp.fc2.weight", "blocks.0.mlp.fc2.weight_scale",
    "blocks.0.adaln_proj.linear.weight", "blocks.0.adaln_proj.linear.bias",
]
for _i in range(2):
    _p = f"token_refiner.blocks.{_i}."
    _KEYS += [
        f"{_p}norm1.weight", f"{_p}norm2.weight",
        f"{_p}attn.q_norm.weight", f"{_p}attn.k_norm.weight",
        f"{_p}attn.qkv_proj.weight", f"{_p}attn.out_proj.weight",
        f"{_p}mlp.fc1.weight", f"{_p}mlp.fc2.weight",
    ]
# comfy_quant marker tensors -- not consumed for math, but the real loader
# pops them and needs them present to detect the quant format at all.
for _p in ("blocks.0.attn.qkv_proj", "blocks.0.attn.out_proj", "blocks.0.mlp.fc1", "blocks.0.mlp.fc2"):
    _KEYS.append(f"{_p}.comfy_quant")

REAL_1BLOCK_CONFIG = {
    "image_model": "minimax_h3", "hidden_size": HIDDEN, "num_layers": 1, "num_refiner_layers": 2,
    "num_attention_heads": HEADS, "attention_head_dim": HEAD_DIM, "ffn_dim": FFN,
    "in_channels": 24, "audio_in_channels": 32, "patch_size": (1, 2, 2), "text_dim": 5120,
    "rope_freq_dim": ROPE_FREQ_DIM, "rope_theta": ROPE_THETA,
    "norm_eps": NORM_EPS, "qk_norm_eps": NORM_EPS, "final_norm_eps": NORM_EPS,
    "pruned": True, "time_embed_dim": TIME_EMBED_DIM, "adaln_curve_grid": ADALN_GRID,
}


@pytest.fixture(scope="module")
def real_sd() -> dict[str, torch.Tensor]:
    from safetensors import safe_open
    sd: dict[str, torch.Tensor] = {}
    with safe_open(str(_DIT_PATH), framework="pt", device="cpu") as f:
        for k in _KEYS:
            sd[k] = f.get_tensor(k)
    # Non-persistent in the real checkpoint (rope.inv_freq is recomputed by
    # post_load() regardless -- this engine's standing rotary-buffer rule);
    # a placeholder just satisfies load_state_dict's missing-key check.
    sd["rope.inv_freq"] = torch.zeros(ROPE_FREQ_DIM, dtype=torch.float32)
    return sd


@pytest.fixture(scope="module")
def loaded_module(real_sd) -> MiniMaxH3Model:
    ops = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    m = MiniMaxH3Model.from_config(REAL_1BLOCK_CONFIG, ops)
    load_into_module(m, dict(real_sd), match_model_spec(REAL_1BLOCK_CONFIG))
    m.eval()
    return m


def _small_layout(seed: int = 0):
    """64 video rows, 8 audio rows, 16 text rows, 2 distinct timesteps, real
    (t,h,w) rotary coordinates on a plausible small grid -- large enough for
    RoPE's 3 axes and attention to be non-degenerate, small enough to stay
    CPU-fast."""
    g = torch.Generator().manual_seed(seed)
    text_n, video_n, audio_n = 16, 64, 8
    seq_len = text_n + video_n + audio_n
    text_indices = torch.arange(0, text_n)
    video_indices = torch.arange(text_n, text_n + video_n)
    audio_indices = torch.arange(text_n + video_n, seq_len)
    token_tags = torch.empty(seq_len, dtype=torch.long)
    token_tags[text_indices] = 1
    token_tags[video_indices] = 0
    token_tags[audio_indices] = 2
    # two distinct timesteps: audio rows get index 1, everything else index 0
    # (mirrors the real t2va layout: text/video share the video schedule).
    timestep_indices = torch.zeros(seq_len, dtype=torch.long)
    timestep_indices[audio_indices] = 1
    position_ids = (torch.rand(seq_len, 3, generator=g, dtype=torch.float64) * 20.0) - 10.0
    return dict(
        text_indices=text_indices, video_indices=video_indices, audio_indices=audio_indices,
        token_tags=token_tags, timestep_indices=timestep_indices, position_ids=position_ids,
        seq_len=seq_len, text_n=text_n, video_n=video_n, audio_n=audio_n,
    )


# --- independent ComfyUI-transcribed reference (see file header) -----------

def _ref_dequant_fp8(weight_fp8: torch.Tensor, weight_scale: torch.Tensor) -> torch.Tensor:
    """`dequant = code * scale` -- comfy/quant_ops.py's own `quantize` stores
    `scale = amax/fp8_max` and does `qdata = tensor / scale`, so the inverse
    is multiply, not divide (checked directly against the real quant_ops.py,
    not assumed)."""
    return weight_fp8.to(torch.float32) * weight_scale.to(torch.float32)


def _ref_linear_fp8(x: torch.Tensor, w_fp8: torch.Tensor, w_scale: torch.Tensor, bias=None) -> torch.Tensor:
    w = _ref_dequant_fp8(w_fp8, w_scale)
    return F.linear(x.to(torch.float32), w, bias.to(torch.float32) if bias is not None else None)


def _ref_rmsnorm(x: torch.Tensor, weight: torch.Tensor, eps: float) -> torch.Tensor:
    x32 = x.to(torch.float32)
    rms = torch.rsqrt(x32.pow(2).mean(dim=-1, keepdim=True) + eps)
    return (x32 * rms) * weight.to(torch.float32)


def _ref_adaln_curve_lookup(t: torch.Tensor, table: torch.Tensor) -> torch.Tensor:
    """Transcribed from `MiniMaxH3Model._forward`'s inline pruned-table branch
    (comfy/ldm/minimax/model.py): clamp-then-lerp over a uniform [0,1] grid,
    floor-clamped to `grid-2` so `t==1.0` still interpolates the last interval."""
    table = table.to(torch.float32)
    grid = table.shape[0]
    pos = t.to(torch.float32).clamp(0.0, 1.0) * (grid - 1)
    i0 = pos.floor().long().clamp(max=grid - 2)
    frac = (pos - i0).unsqueeze(-1)
    return torch.lerp(table[i0], table[i0 + 1], frac)


def _ref_adaln_proj(t_emb: torch.Tensor, weight: torch.Tensor, bias: torch.Tensor,
                     expand: int, modalities: int, apply_silu: bool) -> tuple[torch.Tensor, ...]:
    """Transcribed from comfy's `AdalnProj.forward`: `[M, t_dim] -> expand
    tensors of [M*modalities, hidden]`."""
    x = F.silu(t_emb) if apply_silu else t_emb
    x = F.linear(x.to(torch.float32), weight.to(torch.float32), bias.to(torch.float32))
    hidden = weight.shape[0] // (expand * modalities)
    x = x.view(x.shape[0] * modalities, expand * hidden)
    return x.chunk(expand, dim=-1)


def _ref_rope_angles(position_ids: torch.Tensor, inv_freq: torch.Tensor) -> torch.Tensor:
    """(S,3) position_ids -> (S,96) angle values (pre cos/sin), transcribed
    from comfy's `MiniMaxH3Model.rope_freqs`."""
    pos = position_ids.to(torch.float32)
    inv = inv_freq.to(torch.float32)
    per_axis = pos.unsqueeze(-1) * inv.view(1, 1, -1)   # (S,3,16)
    t_f, h_f, w_f = per_axis.unbind(dim=1)
    half = torch.cat((t_f, h_f, w_f), dim=-1)           # (S,48)
    return torch.cat((half, half), dim=-1)              # (S,96)


def _ref_apply_rope(x: torch.Tensor, angles: torch.Tensor) -> torch.Tensor:
    """Re-derived from `rope_rotation_table`'s own 2x2-matrix construction
    (comfy/ldm/minimax/model.py): ``half = angles.shape[-1]//2; ang =
    angles[:, :half]; table = [[cos(ang),-sin(ang)],[sin(ang),cos(ang)]]``
    applied to consecutive (x1,x2) PAIRS split at ``half`` -- algebraically
    ``(x1,x2) -> (x1*cos - x2*sin, x1*sin + x2*cos)``, i.e. the rotate-half
    convention with cos/sin duplicated across both halves. ``x``: (S,H,D)."""
    rot_dim = angles.shape[-1]
    half = rot_dim // 2
    x_rot, x_pass = x[..., :rot_dim], x[..., rot_dim:]
    x1, x2 = x_rot[..., :half], x_rot[..., half:]
    ang = angles[:, :half].to(x.dtype)[:, None, :]
    cos, sin = torch.cos(ang), torch.sin(ang)
    x1_new = x1 * cos - x2 * sin
    x2_new = x1 * sin + x2 * cos
    return torch.cat((x1_new, x2_new, x_pass), dim=-1)


def _ref_attention(x: torch.Tensor, w: dict, angles: torch.Tensor) -> torch.Tensor:
    """x: (S, hidden). Transcribed from comfy's `Attention.forward` (the
    non-fused-kernel/eager branch: separate q_norm/k_norm then rope)."""
    s = x.shape[0]
    qkv = _ref_linear_fp8(x, w["qkv_w"], w["qkv_scale"])
    q, k, v = qkv.split(INNER, dim=-1)
    q = q.view(s, HEADS, HEAD_DIM)
    k = k.view(s, HEADS, HEAD_DIM)
    v = v.view(s, HEADS, HEAD_DIM)
    q = _ref_rmsnorm(q, w["q_norm_w"], NORM_EPS)
    k = _ref_rmsnorm(k, w["k_norm_w"], NORM_EPS)
    q = _ref_apply_rope(q, angles)
    k = _ref_apply_rope(k, angles)
    # (1, heads, S, head_dim) for scaled_dot_product_attention, no mask.
    qT = q.transpose(0, 1).unsqueeze(0).to(torch.float32)
    kT = k.transpose(0, 1).unsqueeze(0).to(torch.float32)
    vT = v.transpose(0, 1).unsqueeze(0).to(torch.float32)
    out = F.scaled_dot_product_attention(qT, kT, vT, attn_mask=None)
    out = out.squeeze(0).transpose(0, 1).reshape(s, INNER)
    return _ref_linear_fp8(out, w["out_w"], w["out_scale"])


def _ref_mlp(x: torch.Tensor, w: dict) -> torch.Tensor:
    """Transcribed from comfy's `MLP.forward` / `_swiglu_eager`: gate is the
    FIRST half, up (value) the second."""
    h = _ref_linear_fp8(x, w["fc1_w"], w["fc1_scale"])
    gate, up = h.chunk(2, dim=-1)
    prod = F.silu(gate) * up
    return _ref_linear_fp8(prod, w["fc2_w"], w["fc2_scale"])


def _ref_block_forward(x: torch.Tensor, t_emb: torch.Tensor, adaln_indices: torch.Tensor,
                        angles: torch.Tensor, bw: dict) -> torch.Tensor:
    """Transcribed from comfy's `DiTBlock.forward` (+ `_mod_scale_shift`/
    `_mod_gate`, re-expressed with per-row `index_select` instead of comfy's
    per-segment loop -- provably equivalent since every row of one segment
    shares one (timestep, modality) row by construction, verified separately
    against comfy's own segment-table addressing convention
    `timestep_row*3 + modality_tag`)."""
    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = _ref_adaln_proj(
        t_emb, bw["adaln_w"], bw["adaln_b"], expand=6, modalities=3, apply_silu=False,
    )
    residual = x
    h = _ref_rmsnorm(x, bw["norm1_w"], NORM_EPS)
    h = h * (1.0 + scale_msa.index_select(0, adaln_indices)) + shift_msa.index_select(0, adaln_indices)
    h = _ref_attention(h, bw, angles)
    x = residual + gate_msa.index_select(0, adaln_indices) * h

    residual = x
    h = _ref_rmsnorm(x, bw["norm2_w"], NORM_EPS)
    h = h * (1.0 + scale_mlp.index_select(0, adaln_indices)) + shift_mlp.index_select(0, adaln_indices)
    h = _ref_mlp(h, bw)
    x = residual + gate_mlp.index_select(0, adaln_indices) * h
    return x


def _ref_final_layer(x: torch.Tensor, t_emb: torch.Tensor, timestep_indices: torch.Tensor, w: dict):
    shift, scale = _ref_adaln_proj(t_emb, w["fl_adaln_w"], w["fl_adaln_b"], expand=2, modalities=1, apply_silu=False)
    shift = shift.index_select(0, timestep_indices)
    scale = scale.index_select(0, timestep_indices)
    h = _ref_rmsnorm(x, w["fl_norm_w"], NORM_EPS) * (1.0 + scale) + shift
    video = F.linear(h, w["video_out_w"].to(torch.float32), w["video_out_b"].to(torch.float32))
    audio = F.linear(h, w["audio_out_w"].to(torch.float32), w["audio_out_b"].to(torch.float32))
    return video, audio


# --- tests -------------------------------------------------------------------

def test_adaln_curve_lookup_matches_reference(real_sd, loaded_module):
    ts = torch.tensor([0.0, 0.001, 0.3, 0.5, 0.85, 0.999, 1.0])
    ref = _ref_adaln_curve_lookup(ts, real_sd["adaln_t_table"])
    assert torch.isfinite(ref).all()
    assert ref.shape == (7, TIME_EMBED_DIM)

    ours = loaded_module._prepare_timestep(ts)
    torch.testing.assert_close(ours.float(), ref.float(), atol=1e-4, rtol=1e-4)


def test_patch_proj_matches_reference(real_sd, loaded_module):
    torch.manual_seed(1)
    x_video = torch.randn(1, 5, 96)
    x_audio = torch.randn(1, 3, 32)
    ours_v, ours_a = loaded_module._process_input(x_video, x_audio)

    ref_v = F.linear(x_video.float(), real_sd["video_patch_proj.weight"].float(),
                      real_sd["video_patch_proj.bias"].float())
    ref_a = F.linear(x_audio.float(), real_sd["audio_patch_proj.weight"].float(),
                      real_sd["audio_patch_proj.bias"].float())
    torch.testing.assert_close(ours_v.float(), ref_v, atol=1e-3, rtol=1e-3)
    torch.testing.assert_close(ours_a.float(), ref_a, atol=1e-3, rtol=1e-3)


def test_fc2_fp8_dequant_matches_naive_reference(real_sd):
    """S2: fc2 is the one Linear with `full_precision_matrix_mult: true` and
    NO `input_scale`. Confirms our vendored `Fp8ScaledLinear`'s dequant
    forward (the path always taken unless `$NATIVE_FP8_MATMUL` is set --
    verified off by default) reproduces a naive weight-dequant + float
    matmul reference on the REAL fc2 tensor, and that the missing
    `input_scale` doesn't silently perturb it (the dequant path never reads
    `input_scale` at all -- see `Fp8ScaledLinear.forward_comfy_cast_weights`)."""
    from vendor.gpl.comfyui.ops import fp8_ops

    w = real_sd["blocks.0.mlp.fc2.weight"]
    scale = real_sd["blocks.0.mlp.fc2.weight_scale"]
    layer = fp8_ops.Linear(FFN, HIDDEN, bias=False, dtype=torch.bfloat16, device="cpu")
    sd = {"weight": w.clone(), "weight_scale": scale.clone(),
          "comfy_quant": real_sd["blocks.0.mlp.fc2.comfy_quant"].clone()}
    layer.load_state_dict(sd, strict=False, assign=True)
    assert layer.weight_scale is not None
    assert layer.input_scale is None  # real file has none -- must stay None, not defaulted

    torch.manual_seed(2)
    x = torch.randn(4, FFN, dtype=torch.bfloat16)
    ours = layer.forward_comfy_cast_weights(x)
    ref = _ref_linear_fp8(x, w, scale)
    torch.testing.assert_close(ours.float(), ref.float(), atol=2e-2, rtol=2e-2)


def test_full_block0_forward_matches_independent_reference(real_sd, loaded_module):
    """The definitive check: our loaded block 0 (real fp8 weights, real
    scales, real adaln table) vs. the independent ComfyUI-transcribed
    reference, stage by stage, at two distinct timesteps on a small packed
    sequence."""
    torch.manual_seed(3)
    layout = _small_layout()
    seq_len = layout["seq_len"]

    x = torch.randn(seq_len, HIDDEN, dtype=torch.bfloat16) * 0.1
    t_vals = torch.tensor([0.12, 0.83])  # two distinct real-range timesteps
    adaln_indices = layout["timestep_indices"] * 3 + layout["token_tags"]

    bw = {
        "qkv_w": real_sd["blocks.0.attn.qkv_proj.weight"], "qkv_scale": real_sd["blocks.0.attn.qkv_proj.weight_scale"],
        "out_w": real_sd["blocks.0.attn.out_proj.weight"], "out_scale": real_sd["blocks.0.attn.out_proj.weight_scale"],
        "q_norm_w": real_sd["blocks.0.attn.q_norm.weight"], "k_norm_w": real_sd["blocks.0.attn.k_norm.weight"],
        "fc1_w": real_sd["blocks.0.mlp.fc1.weight"], "fc1_scale": real_sd["blocks.0.mlp.fc1.weight_scale"],
        "fc2_w": real_sd["blocks.0.mlp.fc2.weight"], "fc2_scale": real_sd["blocks.0.mlp.fc2.weight_scale"],
        "norm1_w": real_sd["blocks.0.norm1.weight"], "norm2_w": real_sd["blocks.0.norm2.weight"],
        "adaln_w": real_sd["blocks.0.adaln_proj.linear.weight"], "adaln_b": real_sd["blocks.0.adaln_proj.linear.bias"],
    }

    # --- reference side ---
    t_emb_ref = _ref_adaln_curve_lookup(t_vals, real_sd["adaln_t_table"])
    inv_freq = 1.0 / (ROPE_THETA ** (torch.arange(0, 2 * ROPE_FREQ_DIM, 2, dtype=torch.float32) / (2 * ROPE_FREQ_DIM)))
    angles_ref = _ref_rope_angles(layout["position_ids"], inv_freq)
    ref_out = _ref_block_forward(x, t_emb_ref, adaln_indices, angles_ref, bw)

    # --- our side (through the real production module) ---
    m = loaded_module
    temb_ours = m._prepare_timestep(t_vals)
    torch.testing.assert_close(temb_ours.float(), t_emb_ref.float(), atol=1e-4, rtol=1e-4)  # table-lookup stage

    cos_ours, sin_ours = m._prepare_positional_embeddings(layout["position_ids"])
    angle_check = torch.atan2(sin_ours[:, :ROPE_FREQ_DIM * 3].float(), cos_ours[:, :ROPE_FREQ_DIM * 3].float())
    ref_angle_wrapped = torch.atan2(torch.sin(angles_ref[:, :ROPE_FREQ_DIM * 3]), torch.cos(angles_ref[:, :ROPE_FREQ_DIM * 3]))
    torch.testing.assert_close(angle_check, ref_angle_wrapped, atol=1e-4, rtol=1e-4)  # rope-angle stage

    ours_out = m.blocks[0](x[None], temb_ours, adaln_indices, (cos_ours, sin_ours))[0]

    # Real block 0 has legitimate "massive activation" outlier channels
    # (single feature reaching |x| ~ 8000 -- a well-documented transformer
    # phenomenon, not a bug), where bf16's ~0.4% relative rounding granularity
    # dwarfs a flat atol/rtol tuned for order-1 values while still being
    # numerically tiny relative error. Use relative L2 error + correlation
    # instead of a blanket elementwise atol/rtol, which is what actually
    # discriminates "same algorithm, bf16 vs fp32 rounding" from "different
    # algorithm" (a real divergence like the SwiGLU bug drove correlation to
    # near zero and relative L2 error to >100%, not this).
    ours_f, ref_f = ours_out.float(), ref_out.float()
    rel_l2 = (ours_f - ref_f).norm() / ref_f.norm()
    corr = torch.corrcoef(torch.stack([ours_f.flatten(), ref_f.flatten()]))[0, 1]
    assert rel_l2 < 1e-2, f"relative L2 error {rel_l2.item():.4%} too large for bf16 rounding alone"
    assert corr > 0.9999, f"correlation {corr.item():.6f} too low -- looks like a real divergence"
    # elementwise check too, but scaled to this block's actual dynamic range
    # (not a fixed order-1 tolerance) so it still catches a LOCALIZED bug
    # (e.g. one wrong row/channel) that a global L2/correlation check could
    # average away.
    scale = ref_f.abs().amax(dim=-1, keepdim=True).clamp(min=1.0)
    torch.testing.assert_close(ours_f / scale, ref_f / scale, atol=5e-3, rtol=5e-3)


def test_final_layer_matches_independent_reference(real_sd, loaded_module):
    torch.manual_seed(4)
    seq_len = 12
    x = torch.randn(1, seq_len, HIDDEN, dtype=torch.bfloat16) * 0.1
    t_vals = torch.tensor([0.2, 0.77])
    timestep_indices = torch.tensor([0] * 6 + [1] * 6)

    fw = {
        "fl_adaln_w": real_sd["final_layer.adaln_proj.linear.weight"],
        "fl_adaln_b": real_sd["final_layer.adaln_proj.linear.bias"],
        "fl_norm_w": real_sd["final_layer.norm.weight"],
        "video_out_w": real_sd["final_layer.video_out.weight"], "video_out_b": real_sd["final_layer.video_out.bias"],
        "audio_out_w": real_sd["final_layer.audio_out.weight"], "audio_out_b": real_sd["final_layer.audio_out.bias"],
    }
    t_emb_ref = _ref_adaln_curve_lookup(t_vals, real_sd["adaln_t_table"])
    ref_video, ref_audio = _ref_final_layer(x[0], t_emb_ref, timestep_indices, fw)

    m = loaded_module
    temb_ours = m._prepare_timestep(t_vals)
    ours_video, ours_audio = m.final_layer(x, temb_ours, timestep_indices)

    torch.testing.assert_close(ours_video[0].float(), ref_video.float(), atol=5e-2, rtol=5e-2)
    torch.testing.assert_close(ours_audio[0].float(), ref_audio.float(), atol=5e-2, rtol=5e-2)
