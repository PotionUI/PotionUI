"""Regression tests for config detection: it must not read a nvfp4-packed
Linear's stored (halved) in-features as if it were the true model width.

nvfp4 stores two 4-bit codes per byte along a Linear weight's in-features axis
(``[out, in // 2]`` on disk, marked by a sibling ``weight_scale_2`` key). Every
family detector that reads ``.shape[1]`` off a weight which could plausibly be
quantised now goes through ``linear_in_features`` instead of a bare shape read.
Krea-2 is the confirmed real-world case (a real nvfp4 Krea-2 Turbo checkpoint
packs ``blocks.0.attn.wq.weight``, halving the detected ``features`` and
building a model at half width -- see the module docstring on
``_detect_krea2``); the rest are defensive fixes for the same class of bug,
verified here with synthetic packed fixtures since no real nvfp4 file was
available for every family.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.io.state_dict_utils import linear_in_features

from .conftest import flux1_sd, flux2_sd, seedvr2_dit_sd
from .test_wan_detect import wan_sd


def _set_linear(sd: dict, key: str, out_features: int, in_features: int, *, packed: bool = False) -> None:
    """Write a Linear weight, optionally as nvfp4-packed (``[out, in // 2]`` +
    a ``weight_scale_2`` sibling) so ``linear_in_features`` must double it back."""
    if packed:
        assert in_features % 2 == 0, "packed in_features must be even"
        sd[key] = torch.zeros(out_features, in_features // 2, dtype=torch.uint8)
        sd[key[: -len(".weight")] + ".weight_scale_2"] = torch.tensor(0.0)
    else:
        sd[key] = torch.zeros(out_features, in_features)


def test_pack_then_unpack_roundtrips():
    """Sanity check on the test helper itself: the packed fixture really does
    store half the width on disk, so the assertions below exercise the
    unpacking path rather than accidentally passing on an unpacked tensor."""
    sd: dict = {}
    _set_linear(sd, "lin.weight", 8, 16, packed=True)
    assert sd["lin.weight"].shape[1] == 8
    assert linear_in_features(sd, "lin.weight") == 16


# ---------------------------------------------------------------------------
# Krea-2 -- the confirmed real-world bug (repro)
# ---------------------------------------------------------------------------

def krea2_sd(
    features: int = 64, heads: int = 4, kvheads: int = 2, channels: int = 4,
    txtdim: int = 32, txtheads: int = 4, txtkvheads: int = 2, txtlayers: int = 4,
    mlpdim: int = 96, tdim: int = 16,
    *, pack_wq: bool = False, pack_first: bool = False, pack_txtmlp: bool = False,
    pack_projector: bool = False, pack_tmlp: bool = False,
) -> dict[str, torch.Tensor]:
    headdim = features // heads
    txt_headdim = txtdim // txtheads
    patch = 2
    sd: dict[str, torch.Tensor] = {
        "blocks.0.attn.qknorm.qnorm.scale": torch.zeros(headdim),
        "txtfusion.layerwise_blocks.0.attn.qknorm.qnorm.scale": torch.zeros(txt_headdim),
        "blocks.0.mlp.gate.weight": torch.zeros(mlpdim, features),
    }
    _set_linear(sd, "blocks.0.attn.wq.weight", features, features, packed=pack_wq)
    _set_linear(sd, "blocks.0.attn.wk.weight", kvheads * headdim, features)
    _set_linear(sd, "txtfusion.layerwise_blocks.0.attn.wk.weight", txtkvheads * txt_headdim, txtdim)
    _set_linear(sd, "first.weight", features, patch * patch * channels, packed=pack_first)
    _set_linear(sd, "txtmlp.1.weight", features, txtdim, packed=pack_txtmlp)
    _set_linear(sd, "txtfusion.projector.weight", 1, txtlayers, packed=pack_projector)
    _set_linear(sd, "tmlp.0.weight", features, tdim, packed=pack_tmlp)
    return sd


def test_krea2_baseline_unpacked():
    c = detect_unet_config(krea2_sd())
    assert c["image_model"] == "krea2"
    assert (c["features"], c["heads"], c["kvheads"]) == (64, 4, 2)
    assert (c["channels"], c["txtdim"], c["txtlayers"], c["tdim"]) == (4, 32, 4, 16)


def test_krea2_features_survives_packed_wq():
    """The real-world repro: wq packed to half-width must not halve features."""
    c = detect_unet_config(krea2_sd(pack_wq=True))
    assert c["features"] == 64
    assert c["heads"] == 4
    assert c["kvheads"] == 2


def test_krea2_channels_survives_packed_first():
    c = detect_unet_config(krea2_sd(pack_first=True))
    assert c["channels"] == 4
    assert c["features"] == 64  # read from first.weight's OUT-features, never packed


def test_krea2_txtdim_survives_packed_txtmlp():
    c = detect_unet_config(krea2_sd(pack_txtmlp=True))
    assert c["txtdim"] == 32


def test_krea2_txtlayers_survives_packed_projector():
    c = detect_unet_config(krea2_sd(pack_projector=True))
    assert c["txtlayers"] == 4


def test_krea2_tdim_survives_packed_tmlp():
    c = detect_unet_config(krea2_sd(pack_tmlp=True))
    assert c["tdim"] == 16


# ---------------------------------------------------------------------------
# Flux1 / Flux2
# ---------------------------------------------------------------------------

def test_flux2_in_channels_survives_packed_img_in():
    sd = flux2_sd(hidden=256)
    out_f, true_in = sd["img_in.weight"].shape
    _set_linear(sd, "img_in.weight", out_f, true_in, packed=True)
    c = detect_unet_config(sd)
    assert c["in_channels"] == true_in  # patch_size 1 -> no division


def test_flux1_context_in_dim_survives_packed_txt_in():
    sd = flux1_sd(hidden=384)
    out_f, true_in = sd["txt_in.weight"].shape
    _set_linear(sd, "txt_in.weight", out_f, true_in, packed=True)
    c = detect_unet_config(sd)
    assert c["context_in_dim"] == true_in


# ---------------------------------------------------------------------------
# Qwen-Image
# ---------------------------------------------------------------------------

def qwen_image_sd(
    inner_dim: int = 64, in_channels: int = 16, head_dim: int = 16, joint_dim: int = 32,
    *, pack_img_in: bool = False, pack_txt_in: bool = False,
) -> dict[str, torch.Tensor]:
    # Unlike Flux/Krea2/Anima/Z-Image, qwen-image's img_in.weight in-features
    # IS in_channels directly -- no patch**2 folding at this layer.
    patch = 2
    sd: dict[str, torch.Tensor] = {
        "transformer_blocks.0.attn.add_q_proj.weight": torch.zeros(inner_dim, inner_dim),
        "txt_norm.weight": torch.zeros(inner_dim),
        "transformer_blocks.0.attn.norm_q.weight": torch.zeros(head_dim),
        "proj_out.weight": torch.zeros(16 * patch * patch, inner_dim),
    }
    _set_linear(sd, "img_in.weight", inner_dim, in_channels, packed=pack_img_in)
    _set_linear(sd, "txt_in.weight", inner_dim, joint_dim, packed=pack_txt_in)
    return sd


def test_qwen_image_baseline_unpacked():
    c = detect_unet_config(qwen_image_sd(inner_dim=64, in_channels=16, joint_dim=32))
    assert c["image_model"] == "qwen_image"
    assert c["in_channels"] == 16
    assert c["joint_attention_dim"] == 32


def test_qwen_image_in_channels_survives_packed_img_in():
    c = detect_unet_config(qwen_image_sd(inner_dim=64, in_channels=16, pack_img_in=True))
    assert c["in_channels"] == 16


def test_qwen_image_joint_attention_dim_survives_packed_txt_in():
    c = detect_unet_config(qwen_image_sd(inner_dim=64, joint_dim=32, pack_txt_in=True))
    assert c["joint_attention_dim"] == 32


# ---------------------------------------------------------------------------
# Wan -- only text_embedding.0 is a genuine Linear; patch_embedding/ref_conv
# are Conv weights and head.modulation/img_emb.emb_pos are raw Parameters, none
# of which ComfyUI's nvfp4 scheme packs (Linear weights only), so they are not
# exercised here (see the audit report).
# ---------------------------------------------------------------------------

def test_wan_text_dim_survives_packed_text_embedding():
    sd = wan_sd(5120, 16, 16, text_dim=4096)
    _set_linear(sd, "text_embedding.0.weight", 5120, 4096, packed=True)
    c = detect_unet_config(sd)
    assert c["text_dim"] == 4096


# ---------------------------------------------------------------------------
# LTX -- "demonstrably loads" with a real nvfp4 file today only because that
# file's patchify_proj/attn2.to_k happen not to be quantised, not because of
# any existing packing-aware idiom (there wasn't one before this fix).
# ---------------------------------------------------------------------------

def ltx_sd(
    inner_dim: int = 128, in_channels: int = 32, cross_dim: int = 64,
    *, pack_patchify: bool = False, pack_to_k: bool = False,
) -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {
        "adaln_single.emb.timestep_embedder.linear_1.weight": torch.zeros(1, 1),
        "proj_out.weight": torch.zeros(8, inner_dim),
    }
    _set_linear(sd, "patchify_proj.weight", inner_dim, in_channels, packed=pack_patchify)
    _set_linear(sd, "transformer_blocks.0.attn2.to_k.weight", inner_dim, cross_dim, packed=pack_to_k)
    return sd


def test_ltx_baseline_unpacked():
    c = detect_unet_config(ltx_sd(in_channels=32, cross_dim=64))
    assert c["image_model"] == "ltxv"
    assert c["in_channels"] == 32
    assert c["cross_attention_dim"] == 64


def test_ltx_in_channels_survives_packed_patchify_proj():
    c = detect_unet_config(ltx_sd(in_channels=32, pack_patchify=True))
    assert c["in_channels"] == 32


def test_ltx_cross_attention_dim_survives_packed_to_k():
    c = detect_unet_config(ltx_sd(cross_dim=64, pack_to_k=True))
    assert c["cross_attention_dim"] == 64


# ---------------------------------------------------------------------------
# Anima
# ---------------------------------------------------------------------------

def anima_sd(
    model_channels: int = 64, x_in: int = 17, head_dim: int = 16, crossattn_dim: int = 32,
    llm_head_dim: int = 16, llm_model_dim: int = 32, llm_source_dim: int = 24,
    llm_vocab: int = 100, llm_target_dim: int = 8,
    *, pack_x_embedder: bool = False, pack_cross_k: bool = False, pack_llm_k: bool = False,
) -> dict[str, torch.Tensor]:
    pack = 4  # patch_spatial(2)**2 * patch_temporal(1)
    sd: dict[str, torch.Tensor] = {
        "final_layer.linear.weight": torch.zeros(8, model_channels),
        "blocks.0.self_attn.q_norm.weight": torch.zeros(head_dim),
        "blocks.0.adaln_modulation_self_attn.1.weight": torch.zeros(16, model_channels),
        "blocks.0.mlp.layer1.weight": torch.zeros(model_channels * 4, model_channels),
        "llm_adapter.blocks.0.cross_attn.q_norm.weight": torch.zeros(llm_head_dim),
        "llm_adapter.embed.weight": torch.zeros(llm_vocab, llm_target_dim),
    }
    _set_linear(sd, "x_embedder.proj.1.weight", model_channels, x_in * pack, packed=pack_x_embedder)
    _set_linear(sd, "blocks.0.cross_attn.k_proj.weight", model_channels, crossattn_dim, packed=pack_cross_k)
    _set_linear(sd, "llm_adapter.blocks.0.cross_attn.q_proj.weight", llm_model_dim, model_channels)
    _set_linear(sd, "llm_adapter.blocks.0.cross_attn.k_proj.weight", llm_model_dim, llm_source_dim, packed=pack_llm_k)
    return sd


def test_anima_baseline_unpacked():
    c = detect_unet_config(anima_sd(x_in=17, crossattn_dim=32, llm_source_dim=24))
    assert c["image_model"] == "anima"
    assert c["in_channels"] == 16  # x_in(17) - concat_padding_mask(1)
    assert c["crossattn_emb_channels"] == 32
    assert c["llm_source_dim"] == 24


def test_anima_in_channels_survives_packed_x_embedder():
    c = detect_unet_config(anima_sd(x_in=17, pack_x_embedder=True))
    assert c["in_channels"] == 16


def test_anima_crossattn_dim_survives_packed_cross_k():
    c = detect_unet_config(anima_sd(crossattn_dim=32, pack_cross_k=True))
    assert c["crossattn_emb_channels"] == 32


def test_anima_llm_source_dim_survives_packed_llm_k():
    c = detect_unet_config(anima_sd(llm_source_dim=24, pack_llm_k=True))
    assert c["llm_source_dim"] == 24


# ---------------------------------------------------------------------------
# Z-Image (Lumina2, dim must be exactly 3840 to be recognized)
# ---------------------------------------------------------------------------

def z_image_sd(
    cap_feat_dim: int = 64, in_channels: int = 16,
    *, pack_cap: bool = False, pack_x_embedder: bool = False,
) -> dict[str, torch.Tensor]:
    dim = 3840
    patch = 2
    sd: dict[str, torch.Tensor] = {
        "layers.0.feed_forward.w1.weight": torch.zeros(128, dim),
    }
    _set_linear(sd, "cap_embedder.1.weight", dim, cap_feat_dim, packed=pack_cap)
    _set_linear(sd, "x_embedder.weight", dim, in_channels * patch * patch, packed=pack_x_embedder)
    return sd


def test_z_image_baseline_unpacked():
    c = detect_unet_config(z_image_sd(cap_feat_dim=64, in_channels=16))
    assert c["image_model"] == "lumina2"
    assert c["cap_feat_dim"] == 64
    assert c["in_channels"] == 16


def test_z_image_cap_feat_dim_survives_packed_cap_embedder():
    c = detect_unet_config(z_image_sd(cap_feat_dim=64, pack_cap=True))
    assert c["cap_feat_dim"] == 64


def test_z_image_in_channels_survives_packed_x_embedder():
    c = detect_unet_config(z_image_sd(in_channels=16, pack_x_embedder=True))
    assert c["in_channels"] == 16


# ---------------------------------------------------------------------------
# SeedVR2
# ---------------------------------------------------------------------------

def test_seedvr2_txt_in_dim_survives_packed_txt_in():
    sd = seedvr2_dit_sd(vid_dim=64, txt_in_dim=5120)
    _set_linear(sd, "txt_in.weight", 64, 5120, packed=True)
    c = detect_unet_config(sd)
    assert c["txt_in_dim"] == 5120


def test_seedvr2_vid_in_channels_survives_packed_vid_in():
    sd = seedvr2_dit_sd(vid_dim=64, vid_in_channels=32)
    _set_linear(sd, "vid_in.proj.weight", 64, 32 * 4, packed=True)
    c = detect_unet_config(sd)
    assert c["vid_in_channels"] == 32
