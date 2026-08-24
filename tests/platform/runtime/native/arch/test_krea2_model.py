"""Tests for the vendored Krea-2 SingleStream MMDiT architecture.

Coverage:
  (a) tiny-config forward smoke over the full generator call sequence (CPU fp32);
  (b) detection derives the exact real config from real tensor shapes (meta);
  (c) meta-device key-set parity vs the REAL 430-key Krea-2 header fixture;
  (d) detect -> match_model_spec -> from_config roundtrip on a tiny state dict;
  (e) post_load no-op / NativeArchModule contract / config validation.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.krea2.config import Krea2Config
from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from vendor.gpl.comfyui.ops import pick_operations

_FIXTURES = Path(__file__).parent / "fixtures"
_REAL_CKPT = Path("models/diffusion_models/krea2TurboOfficialComfy_krea2TurboBf16.safetensors")

# Exact config detect_unet_config must derive from the real Krea-2 turbo header.
REAL_CONFIG = {
    "image_model": "krea2", "features": 6144, "heads": 48, "kvheads": 12,
    "channels": 16, "layers": 28, "multiplier": 4, "tdim": 256, "txtdim": 2560,
    "txtheads": 20, "txtkvheads": 20, "txtlayers": 12, "patch": 2, "theta": 1000.0,
}

# Tiny Krea-2: headdim 16 -> rope_axes [4,6,6] (sum 16); GQA 2:1.
TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _randomised_state_dict(module) -> dict[str, torch.Tensor]:
    """Fill empty params: norm scales + modulation vectors -> 0, Linears -> small randn."""
    sd: dict[str, torch.Tensor] = {}
    for k, v in module.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    return sd


def _build_ready(config) -> Krea2:
    m = Krea2.from_config(config, _fp32_ops())
    load_into_module(m, _randomised_state_dict(m), match_model_spec(config))
    m.eval()
    return m


def _real_header_shapes() -> dict[str, tuple[int, ...]]:
    with open(_REAL_CKPT, "rb") as f:
        hl = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(hl))
    header.pop("__metadata__", None)
    return {k: tuple(v["shape"]) for k, v in header.items()}


# --- (a) forward smoke over the full call sequence -----------------------

def test_tiny_full_forward_sequence():
    m = _build_ready(TINY)
    b, c, h, w = 1, 4, 8, 8
    latent = torch.randn(b, c, h, w)
    txt_seq, txt_layers, txtdim = 5, 3, 16
    te_hidden = torch.randn(b, txt_seq, txt_layers, txtdim)  # (B, L_seq, txtlayers, txtdim)

    img_tokens, pos, mask = m.build_stream_inputs(latent, txt_len=txt_seq)
    t_emb, tvec = m.prepare_timestep(torch.tensor([0.5]), torch.float32)
    context = m.prepare_context(te_hidden, mask)
    out = m.run_blocks(img_tokens, context, t_emb, tvec, pos, mask)
    latent_out = m.unpatchify(out, h // 2, w // 2)

    assert latent_out.shape == (b, c, h, w)
    assert torch.isfinite(latent_out).all()


def test_tiny_forward_batch():
    m = _build_ready(TINY)
    latent = torch.randn(2, 4, 8, 12)
    te_hidden = torch.randn(2, 4, 3, 16)  # (B, L_seq=4, txtlayers=3, txtdim=16)
    img, pos, mask = m.build_stream_inputs(latent, txt_len=4)
    t_emb, tvec = m.prepare_timestep(torch.tensor([0.3, 0.7]), torch.float32)
    ctx = m.prepare_context(te_hidden, mask)
    out = m.unpatchify(m.run_blocks(img, ctx, t_emb, tvec, pos, mask), 4, 6)
    assert out.shape == (2, 4, 8, 12)


def test_prepare_timestep_fp8_stored_weights_compute_in_latent_dtype():
    """Fully-quantized checkpoints store the timestep MLP in fp8 too. The
    embedding must be built in the COMPUTE dtype (the latent's), so the
    cast-on-forward ops cast weights up - deriving it from ``weight.dtype``
    collapsed the whole path to fp8, where addmm has no kernel."""
    m = Krea2.from_config(TINY, pick_operations(torch.float8_e4m3fn, torch.float32))
    load_into_module(m, _randomised_state_dict(m), match_model_spec(TINY))
    m.eval()
    for mod in list(m.tmlp) + list(m.tproj):
        if hasattr(mod, "weight") and mod.weight is not None:
            mod.weight.data = mod.weight.data.to(torch.float8_e4m3fn)

    t_emb, tvec = m.prepare_timestep(torch.tensor([0.5]), torch.float32)
    assert t_emb.dtype == torch.float32
    assert tvec.dtype == torch.float32
    assert torch.isfinite(t_emb).all() and torch.isfinite(tvec).all()


def test_full_forward_naive_all_fp8_checkpoint():
    """selfora-style files cast EVERY tensor to fp8 - including raw params
    (norm scales, modulation tables) that no cast-on-forward op manages and
    that the forward does arithmetic on. post_load must upcast those back to
    their declared f32; ops-managed Linear weights stay fp8 (cast at forward)."""
    m = Krea2.from_config(TINY, pick_operations(torch.float8_e4m3fn, torch.float32))
    sd = {
        k: v.to(torch.float8_e4m3fn) if v.is_floating_point() else v
        for k, v in _randomised_state_dict(m).items()
    }
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()

    assert m.blocks[0].prenorm.scale.dtype == torch.float32
    assert m.blocks[0].mod.lin.dtype == torch.float32
    assert m.last.modulation.lin.dtype == torch.float32
    assert m.tmlp[0].weight.dtype == torch.float8_e4m3fn

    b, c, h, w = 1, 4, 8, 8
    latent = torch.randn(b, c, h, w)
    te_hidden = torch.randn(b, 5, 3, 16)
    img, pos, mask = m.build_stream_inputs(latent, txt_len=5)
    t_emb, tvec = m.prepare_timestep(torch.tensor([0.5]), torch.float32)
    ctx = m.prepare_context(te_hidden, mask)
    out = m.unpatchify(m.run_blocks(img, ctx, t_emb, tvec, pos, mask), h // 2, w // 2)
    assert out.shape == (b, c, h, w)
    assert torch.isfinite(out).all()


def test_flat_forward_adapter():
    """The flat forward(x, timestep, context, attention_mask) adapter that
    NativeGenerator.sample drives -- composes the helper contract internally."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)                      # (B, 16-equiv, H, W)
    te_hidden = torch.randn(1, 5, 3, 16)             # (B, L_txt, 12-equiv, txtdim)
    out = m(x, torch.tensor([0.5]), te_hidden, attention_mask=torch.ones(1, 5, dtype=torch.long))
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
    # scalar timestep is accepted and broadcast to the batch.
    out2 = m(torch.randn(2, 4, 8, 8), torch.tensor(0.5), torch.randn(2, 5, 3, 16))
    assert out2.shape == (2, 4, 8, 8)
    # 5D causal-VAE latent (B,16,1,H,W): the singleton T axis round-trips.
    x5 = torch.randn(1, 4, 1, 8, 8)
    out5 = m(x5, torch.tensor([0.5]), torch.randn(1, 5, 3, 16))
    assert out5.shape == x5.shape


# --- (a2) attention-mask short-circuit (fast-path preservation) ----------

def test_build_stream_inputs_mask_shortcircuit():
    """An all-valid (or absent) text mask yields mask=None so attention keeps
    its fast fused/accelerated kernel; genuine padding yields the joint mask."""
    m = _build_ready(TINY)
    latent = torch.randn(2, 4, 8, 8)  # 4*4 = 16 img tokens
    txt_len = 5

    # absent mask -> None
    _, _, mask = m.build_stream_inputs(latent, txt_len=txt_len)
    assert mask is None

    # all-valid mask (any truthy dtype) -> None
    _, _, mask = m.build_stream_inputs(
        latent, txt_len=txt_len, txt_mask=torch.ones(2, txt_len, dtype=torch.long))
    assert mask is None

    # genuine padding -> joint bool mask over [text; image]
    pad = torch.ones(2, txt_len, dtype=torch.long)
    pad[0, 3:] = 0
    _, _, mask = m.build_stream_inputs(latent, txt_len=txt_len, txt_mask=pad)
    assert mask is not None
    assert mask.dtype == torch.bool
    assert mask.shape == (2, txt_len + 16)
    assert mask[0].tolist() == [True, True, True, False, False] + [True] * 16
    assert mask[1].all()  # row 1 unpadded


def test_forward_allvalid_mask_matches_no_mask():
    """All-True attention_mask must be bit-identical to passing no mask -- the
    short-circuit must not alter numerics on the common path."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    out_none = m(x, torch.tensor([0.5]), te_hidden)
    out_ones = m(x, torch.tensor([0.5]), te_hidden,
                 attention_mask=torch.ones(1, 5, dtype=torch.long))
    assert torch.equal(out_none, out_ones)


def test_forward_padded_trim_matches_full_padded_mask():
    """Proves the equivalence the sage2-fallback fix depends on: dropping the
    masked-out tail of a padded [text] sequence (and its mask) is mathematically
    identical to keeping the full padded sequence with the tail masked out -
    softmax excludes a -inf-masked key/value entirely either way, in both the
    DiT's joint [text;image] attention AND txtfusion's cross-token refiner
    attention (both see the mask). This is what
    ``Qwen3VLTextEncoder.encode()``'s trim relies on."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    txt_seq, txt_layers, txtdim = 8, 3, 16
    keep = 5

    te_hidden = torch.randn(1, txt_seq, txt_layers, txtdim)
    pad = torch.ones(1, txt_seq, dtype=torch.long)
    pad[0, keep:] = 0

    out_padded = m(x, torch.tensor([0.5]), te_hidden, attention_mask=pad)
    out_trimmed = m(x, torch.tensor([0.5]), te_hidden[:, :keep], attention_mask=None)

    assert torch.allclose(out_padded, out_trimmed, atol=1e-5, rtol=1e-4)


def test_forward_padded_trim_matches_full_padded_mask_batched():
    """Same equivalence, batch > 1, each row padded to a different real length -
    trimming each row independently (as `_trim_padded_tail` does per-row via the
    batch max) must still match the fully-padded reference per row."""
    m = _build_ready(TINY)
    x = torch.randn(2, 4, 8, 8)
    txt_seq, txt_layers, txtdim = 8, 3, 16

    te_hidden = torch.randn(2, txt_seq, txt_layers, txtdim)
    pad = torch.ones(2, txt_seq, dtype=torch.long)
    pad[0, 4:] = 0  # row 0: 4 real tokens
    pad[1, 6:] = 0  # row 1: 6 real tokens (batch max)

    out_padded = m(x, torch.tensor([0.5, 0.5]), te_hidden, attention_mask=pad)

    keep = 6  # matches _trim_padded_tail: max real length across the batch
    trimmed_hidden = te_hidden[:, :keep]
    trimmed_mask = pad[:, :keep]
    out_trimmed = m(x, torch.tensor([0.5, 0.5]), trimmed_hidden, attention_mask=trimmed_mask)

    assert torch.allclose(out_padded, out_trimmed, atol=1e-5, rtol=1e-4)


def test_forward_padded_mask_changes_output():
    """A genuinely padded mask exercises the masked attention path and changes
    the result (padded text tokens are excluded from attention)."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 8, 8)
    te_hidden = torch.randn(1, 5, 3, 16)
    pad = torch.ones(1, 5, dtype=torch.long)
    pad[0, 3:] = 0
    out_full = m(x, torch.tensor([0.5]), te_hidden)
    out_pad = m(x, torch.tensor([0.5]), te_hidden, attention_mask=pad)
    assert out_pad.shape == x.shape
    assert torch.isfinite(out_pad).all()
    assert not torch.allclose(out_full, out_pad)


# --- (b) detection derives the exact real config -------------------------

@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_CKPT.is_file(), reason=f"real Krea-2 turbo bf16 checkpoint not present at {_REAL_CKPT}")
def test_detect_real_shapes_exact_config():
    shapes = _real_header_shapes()
    # meta tensors: shape-only, no allocation.
    sd = {k: torch.empty(s, device="meta") for k, s in shapes.items()}
    config = detect_unet_config(sd)
    assert config == REAL_CONFIG


def test_detect_ignores_flux_checkpoints():
    # a flux2 signature must NOT be detected as krea2.
    sd = {"double_stream_modulation_img.lin.weight": torch.empty(4, 4, device="meta"),
          "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(4, device="meta"),
          "img_in.weight": torch.empty(8, 8, device="meta"),
          "txt_in.weight": torch.empty(8, 8, device="meta")}
    config = detect_unet_config(sd)
    assert config["image_model"] == "flux2"


# --- (c) meta key-set parity vs the real 430-key header ------------------

def test_krea2_meta_keyset_parity():
    ckpt_keys = set((_FIXTURES / "krea2_turbo_bf16.keys.txt").read_text().split())
    assert len(ckpt_keys) == 430
    with torch.device("meta"):
        m = Krea2.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == ckpt_keys, (
        f"missing={sorted(ckpt_keys - built)[:20]} extra={sorted(built - ckpt_keys)[:20]}")


# --- (d) detect -> spec -> from_config roundtrip -------------------------

def test_detect_spec_from_config_roundtrip():
    # build a tiny module, snapshot its shapes as a synthetic sd, re-detect.
    with torch.device("meta"):
        seed = Krea2.from_config(TINY, _fp32_ops())
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in seed.state_dict().items()}
    config = detect_unet_config(sd)
    assert config["image_model"] == "krea2"
    spec = match_model_spec(config)
    assert spec.family == "krea2" and spec.variant == "krea2_turbo"
    with torch.device("meta"):
        rebuilt = Krea2.from_config(config, _fp32_ops())
    # shapes reconstruct identically (multiplier is shape-equivalent under the
    # SwiGLU 128-rounding even if its nominal value differs on tiny configs).
    assert set(rebuilt.state_dict().keys()) == set(seed.state_dict().keys())


# --- (e) contract --------------------------------------------------------

def test_post_load_noop_no_buffers():
    m = _build_ready(TINY)
    assert m.post_load() is None
    assert list(m.named_buffers()) == []


def test_is_native_arch_module():
    with torch.device("meta"):
        m = Krea2.from_config(TINY, _fp32_ops())
    assert isinstance(m, NativeArchModule)


def test_config_rejects_unknown_model():
    with pytest.raises(ValueError, match="image_model"):
        Krea2Config.from_detect_config(dict(TINY, image_model="flux"))


def test_config_rope_axes_sum_to_headdim():
    cfg = Krea2Config.from_detect_config(REAL_CONFIG)
    assert sum(cfg.rope_axes) == cfg.headdim == 128
    assert cfg.rope_axes == [32, 48, 48]


# --- (f) rope/apply_rope provenance (licensing audit) --------------
#
# The old (Wan2GP-attributed) file's `rope()` and `apply_rope_inplace()` are
# proven here to trace to `vendor/gpl/comfyui/flux/math_ops.py` (ComfyUI/Flux
# lineage), not to any Wan2GP-original expression: `rope()` is a thin wrapper
# over the vendored function (bit-exact once `ntk` is folded into `theta`),
# and `apply_rope_inplace()` implements the SAME rotation-matrix convention
# as the vendored `apply_rope1` but is proven NOT bit-exact against it (a
# genuine in-place perf variant, not a different convention).

from vendor.gpl.comfyui.flux.math_ops import apply_rope1 as _vendored_apply_rope1
from vendor.gpl.comfyui.flux.math_ops import rope as _vendored_flux_rope

from src.platform.runtime.native.arch.krea2.layers import apply_rope_inplace, rope


@pytest.mark.parametrize("dim,theta,ntk", [(128, 1e4, 1.0), (128, 100.0, 1.0), (64, 1e4, 2.5), (48, 1e4, 1.0)])
def test_krea2_rope_matches_vendored_flux_rope(dim, theta, ntk):
    pos = torch.randint(0, 100, (2, 17)).double()
    ours = rope(pos, dim, theta, ntk)
    vendored = _vendored_flux_rope(pos, dim, theta * ntk)
    assert torch.equal(ours, vendored)


@pytest.mark.parametrize("dim,dtype", [
    (128, torch.bfloat16), (128, torch.float16), (128, torch.float32), (64, torch.bfloat16),
])
def test_krea2_apply_rope_inplace_vs_vendored_flux_not_bitexact(dim, dtype):
    # Same rotation math, different cast order (ours casts cos/sin down to
    # the activation dtype; the vendored function upcasts the activation to
    # the freqs dtype) -- NOT expected to be bit-exact. If every trial below
    # comes out bit-equal, the in-place variant's perf rationale (the whole
    # point of the cast-order difference) may have silently regressed away.
    # Seeded multi-trial because a single low-precision draw (bf16, dim 64)
    # can legitimately coincide bit-exactly.
    gen = torch.Generator().manual_seed(0)
    diverged = False
    for _ in range(8):
        pos = torch.randint(0, 100, (2, 17), generator=gen).double()
        freqs = rope(pos, dim, 1e4, 1.0)             # (B, L, dim//2, 2, 2)
        freqs_b = freqs[:, None, :, :, :]            # broadcast over heads, like apply_rope_inplace does internally
        x = torch.randn(2, 4, 17, dim, dtype=dtype, generator=gen)

        ours = apply_rope_inplace(x.clone(), freqs)
        vendored = _vendored_apply_rope1(x.clone(), freqs_b)

        # rtol term: cast-order noise scales with |x| (bf16 ~0.4% relative),
        # so a pure atol calibrated on one draw fails on tail draws.
        assert torch.allclose(ours.float(), vendored.float(), atol=2e-2, rtol=2e-2)
        if not torch.equal(ours, vendored):
            diverged = True
            break
    assert diverged
