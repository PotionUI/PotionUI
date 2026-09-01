"""Diffusion-model (DiT) architecture detection from a raw state dict.

Structural state-dict sniffing in the ComfyUI style: a signature key selects
the family, tensor shapes derive the hyper-parameters. No config.json.

v1 covers Flux1 and Flux2/Klein only. The returned config dict uses ComfyUI
field names so a vendored arch module can consume it directly.

Config dict schema (keys always present for a matched model):
    image_model:        "flux" | "flux2"      -- variant discriminator
    in_channels:        int                    -- latent channels the DiT expects
    out_channels:       int
    hidden_size:        int
    context_in_dim:     int                    -- text-encoder embedding width
    num_heads:          int
    depth:              int                    -- number of double-stream blocks
    depth_single_blocks: int
    axes_dim:           list[int]              -- RoPE axis dims (sum == head_dim)
    mlp_ratio:          float
    theta:              int                    -- RoPE base
    patch_size:         int
    qkv_bias:           bool
    guidance_embed:     bool                   -- distilled guidance modulation present
"""

from __future__ import annotations

import json
import logging
import math

import torch

from ..errors import NativeEngineUnsupportedError
from ..io.state_dict_utils import count_blocks, linear_in_features

logger = logging.getLogger(__name__)

# Signature keys (ComfyUI convention).
_FLUX_SIG = "double_blocks.0.img_attn.norm.key_norm.scale"
_FLUX2_SIG = "double_stream_modulation_img.lin.weight"
# Krea-2 (novel arch, see arch/krea2's headers): the text-fusion projector is unique
# to Krea-2 and shares no key space with flux/flux2 (double_blocks) or the
# future wan/qwen/ltx families.
_KREA2_SIG = "txtfusion.projector.weight"
# Qwen-Image MMDiT: the joint-attention text projection (`add_q_proj`) inside
# `transformer_blocks` is unique among the image families (flux uses double_blocks,
# krea2 uses blocks/txtfusion). Paired with `txt_norm.weight` (ComfyUI signature).
_QWEN_IMAGE_SIG = "transformer_blocks.0.attn.add_q_proj.weight"
# Wan 2.1 / 2.2: the per-token final-layer modulation is unique to Wan among all
# the DiT families (ComfyUI's ``head.modulation`` signature). ``image_model`` is
# "wan2.1" for BOTH Wan 2.1 and 2.2 — the 2.2 14B dual-expert split is a
# weights/sampling difference, not a structural one, so it is NOT detectable
# from a single checkpoint (handled at the loader/generator via the expert router).
_WAN_SIG = "head.modulation"
# LTX-2 / LTX-Video (PixArt-style adaLN-single + patchify_proj). ltxv (video) vs
# ltxav (audio-video) is distinguished by the audio_adaln_single key. No collision
# with flux (double_blocks) / qwen (add_q_proj) / wan (head.modulation).
_LTX_SIG = "adaln_single.emb.timestep_embedder.linear_1.weight"
# Anima (NVIDIA Cosmos-Predict2 MiniTrainDIT + an in-model LLMAdapter text-fusion
# head). The ``llm_adapter`` cross-attention projection is unique to Anima — the
# plain Cosmos-Predict2 backbone (``blocks.0.mlp.layer1.weight``) has no adapter,
# and no other native family carries an ``llm_adapter.*`` namespace.
_ANIMA_SIG = "llm_adapter.blocks.0.cross_attn.q_proj.weight"
# Z-Image / Lumina-Image-2.0 NextDiT: the ``cap_embedder.1`` caption projection is
# unique to the NextDiT family (flux uses double_blocks, qwen add_q_proj, wan
# head.modulation). Only the ``dim==3840`` z_image variant is supported.
_LUMINA2_SIG = "cap_embedder.1.weight"
# SeedVR2 (ByteDance native-resolution restoration NaDiT). ``vid_in.proj`` (the
# native-resolution video patch-embed) plus the per-layer ``AdaSingle`` shift
# parameter ``blocks.0.ada.vid.attn_shift`` are both unique to SeedVR2 — no other
# native family carries a ``vid_in`` embed or an ``.ada.vid`` modulation namespace
# (flux double_blocks, qwen add_q_proj, wan head.modulation, ltx patchify_proj).
_SEEDVR2_SIG = "vid_in.proj.weight"
_SEEDVR2_SIG2 = "blocks.0.ada.vid.attn_shift"
# MiniMax-H3 (packed-sequence audio-video DiT). The two patch projections plus
# the text-condition projection plus the block-level fused-qkv attention are a
# combination unique to H3 among every family here (no other family names a
# ``video_patch_proj``/``audio_patch_proj`` pair, and no other family fuses
# q/k/v into one ``qkv_proj`` at the block level -- SeedVR2 fuses qkv too, but
# under ``proj_qkv``, and behind its own ``vid_in``/``ada.vid`` signature).
_MINIMAX_H3_SIG = "video_patch_proj.weight"
_MINIMAX_H3_SIG2 = "audio_patch_proj.weight"
# MiniMax-Music3 (text-to-music flow-matching DiT + fused condition encoder in one
# file). ``# Derived from: ComfyUI model_detection.py:47-52`` — the three-key
# conjunction is ComfyUI's own signature (it returns ``{"audio_model":
# "minimax_music3"}`` there; this registry always keys on ``image_model``
# regardless of modality, matching every other family here including the also
# audio-video MiniMax-H3). No other family names a ``cond_layer_logits`` buffer,
# a ``latent_conditioners`` module, or a ``diffusion_transformer.transformer.*``
# namespace. The discriminator value is "minimax_music3_dit" (not plain
# "minimax_music3") because that name is shared across three independent
# checkpoint files here — the DiT, the text encoder, and the DAV vocoder — and
# the DiT arch module (``arch/minimax_music3/model.py``) guards on the "_dit"
# suffix specifically.
_MINIMAX_MUSIC3_SIG = "cond_layer_logits"
_MINIMAX_MUSIC3_SIG2 = "latent_conditioners.0.weight"
_MINIMAX_MUSIC3_SIG3 = "diffusion_transformer.transformer.layers.0.self_attn.to_qkv.weight"
# TRELLIS.2 (Comfy-Org unified file). Unlike every other family here this is not
# one DiT: four flow models sit side by side under four prefixes, so the config
# returned below describes the BUNDLE, and ``arch/trellis2/load.py`` — not
# ``NativeEngineLoader._load_dit`` — is what builds them. Detection still lives
# here so a user who points a plain diffusion-model slot at this file gets a
# named family instead of "not a recognised native DiT".
_TRELLIS2_STRUCTURE_PREFIX = "model.structure_model."
_TRELLIS2_SHAPE_PREFIX = "model.img2shape."
_TRELLIS2_SHAPE_512_PREFIX = "model.img2shape_512."
_TRELLIS2_TEXTURE_PREFIX = "model.shape2txt."
# Extra-module signature key -> the Wan variant it marks. These carry modules the
# native engine does not vendor (base t2v/i2v only), so they are rejected up front.
_WAN_REJECT: dict[str, str] = {
    "vace_patch_embedding.weight": "vace",
    "control_adapter.conv.weight": "camera / control-adapter",
    "casual_audio_encoder.encoder.final_linear.weight": "s2v (audio)",
    "audio_proj.audio_proj_glob_1.layer.bias": "humo (audio)",
    "face_adapter.fuser_blocks.0.k_norm.weight": "animate",
}


def detect_unet_config(sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None) -> dict | None:
    """Return a DiT config dict for a Flux1/Flux2/Krea-2 state dict, else ``None``.

    The state dict must already be prefix-stripped (keys begin at
    ``double_blocks.0...`` / ``blocks.0...``, not ``model.diffusion_model....``).

    ``metadata`` (the safetensors ``__metadata__`` header, when the caller has
    it) is consulted only by the LTX branch today, which prefers the
    checkpoint's own embedded ``config.transformer`` JSON for the fields it
    covers over shape-sniffing — see ``_detect_ltx``.
    """
    trellis2 = _detect_trellis2(sd)
    if trellis2 is not None:
        return trellis2

    if _KREA2_SIG in sd:
        return _detect_krea2(sd)

    if _QWEN_IMAGE_SIG in sd and "txt_norm.weight" in sd:
        return _detect_qwen_image(sd)

    if _WAN_SIG in sd:
        return _detect_wan(sd)

    if _LTX_SIG in sd and "patchify_proj.weight" in sd:
        return _detect_ltx(sd, metadata)

    if _LUMINA2_SIG in sd:
        return _detect_lumina2(sd)

    if _ANIMA_SIG in sd:
        return _detect_anima(sd)

    if _SEEDVR2_SIG in sd and _SEEDVR2_SIG2 in sd:
        return _detect_seedvr2(sd)

    if _MINIMAX_H3_SIG in sd and _MINIMAX_H3_SIG2 in sd:
        return _detect_minimax_h3(sd)

    if _MINIMAX_MUSIC3_SIG in sd and _MINIMAX_MUSIC3_SIG2 in sd and _MINIMAX_MUSIC3_SIG3 in sd:
        return _detect_minimax_music3(sd)

    if _FLUX_SIG not in sd:
        return None
    # Flux family also requires the input embedder (guards against Chroma, which
    # reuses the double-block layout but has no img_in.weight).
    if "img_in.weight" not in sd:
        return None

    config: dict = {}
    is_flux2 = _FLUX2_SIG in sd

    if is_flux2:
        config["image_model"] = "flux2"
        config["axes_dim"] = [32, 32, 32, 32]
        config["mlp_ratio"] = 3.0
        config["theta"] = 2000
        config["out_channels"] = 128
        config["qkv_bias"] = False
        config["patch_size"] = 1
    else:
        config["image_model"] = "flux"
        config["axes_dim"] = [16, 56, 56]
        config["mlp_ratio"] = 4.0
        config["theta"] = 10000
        config["out_channels"] = 16
        config["qkv_bias"] = True
        config["patch_size"] = 2

    patch_size = config["patch_size"]

    # img_in.weight: [hidden_size, in_channels * patch_size**2]
    img_in = sd["img_in.weight"]
    config["hidden_size"] = int(img_in.shape[0])
    config["in_channels"] = linear_in_features(sd, "img_in.weight") // (patch_size * patch_size)

    # txt_in.weight: [hidden_size, context_in_dim]
    if "txt_in.weight" in sd:
        config["context_in_dim"] = linear_in_features(sd, "txt_in.weight")
    else:
        config["context_in_dim"] = 4096

    # heads follow the flux convention head_dim == sum(axes_dim) == 128.
    config["num_heads"] = config["hidden_size"] // sum(config["axes_dim"])

    config["depth"] = count_blocks(sd, "double_blocks.{}.")
    config["depth_single_blocks"] = count_blocks(sd, "single_blocks.{}.")
    config["guidance_embed"] = "guidance_in.in_layer.weight" in sd
    return _log_flux(config)


def _log_flux(config: dict) -> dict:
    logger.debug(
        "detected %s DiT: hidden=%d heads=%d depth=%d/%d in_ch=%d ctx=%d guidance=%s",
        config["image_model"],
        config["hidden_size"],
        config["num_heads"],
        config["depth"],
        config["depth_single_blocks"],
        config["in_channels"],
        config["context_in_dim"],
        config["guidance_embed"],
    )
    return config


def _detect_trellis2(sd: dict[str, torch.Tensor]) -> dict | None:
    """Config for the Comfy-Org TRELLIS.2 unified flow file, else ``None``.

    All four prefixes are required. A file carrying only some of them is a
    truncated or repacked bundle, and claiming it would lose a cascade stage
    silently — better to fall through and be reported as unrecognised.

    ``resolution`` is the one field no tensor shape carries (the flow models are
    shape-identical at 32 and 64 SLat resolution), so the shape/texture latent
    grids come from ``arch/trellis2/config.py``, not from here.
    """
    prefixes = (
        _TRELLIS2_STRUCTURE_PREFIX,
        _TRELLIS2_SHAPE_PREFIX,
        _TRELLIS2_SHAPE_512_PREFIX,
        _TRELLIS2_TEXTURE_PREFIX,
    )
    if not all(any(k.startswith(p) for k in sd) for p in prefixes):
        return None

    config: dict = {"image_model": "trellis2"}
    for name, prefix in (
        ("structure", _TRELLIS2_STRUCTURE_PREFIX),
        ("shape_512", _TRELLIS2_SHAPE_512_PREFIX),
        ("shape_1024", _TRELLIS2_SHAPE_PREFIX),
        ("texture", _TRELLIS2_TEXTURE_PREFIX),
    ):
        config[name] = {
            "model_channels": int(sd[f"{prefix}input_layer.weight"].shape[0]),
            "in_channels": linear_in_features(sd, f"{prefix}input_layer.weight"),
            "out_channels": int(sd[f"{prefix}out_layer.weight"].shape[0]),
            "cond_channels": linear_in_features(sd, f"{prefix}blocks.0.cross_attn.to_kv.weight"),
            "num_blocks": count_blocks(sd, prefix + "blocks.{}."),
            # The QK RMS-norm gain is per-head: [num_heads, head_dim].
            "num_heads": int(sd[f"{prefix}blocks.0.self_attn.q_rms_norm.gamma"].shape[0]),
        }

    logger.debug(
        "detected trellis2 bundle: structure in=%d blocks=%d, shape_1024 in=%d, texture in=%d",
        config["structure"]["in_channels"], config["structure"]["num_blocks"],
        config["shape_1024"]["in_channels"], config["texture"]["in_channels"],
    )
    return config


def _detect_krea2(sd: dict[str, torch.Tensor]) -> dict:
    """Config for a Krea-2 SingleStream MMDiT state dict (shape-sniffed).

    All hyper-parameters are read from tensor shapes; ``patch`` and ``theta`` are
    arch constants (patch_size 2, RoPE theta 1000). See
    ``src/platform/runtime/native/arch/krea2/config.py`` for the field consumer.

    ``features`` (the model width) is read from ``first.weight``'s OUT-features
    rather than any block's ``wq``/``wk`` IN-features: ``first`` is Krea-2's
    patch embed and, unlike the attention/MLP Linears, is never nvfp4-quantised
    in the wild, so its shape is always the true width (an nvfp4
    Krea-2 Turbo checkpoint packs ``wq``'s in-features to half-width on disk,
    and reading THAT would silently halve every derived dim). The remaining
    in-features reads go through :func:`linear_in_features` for the same
    packing-awareness on layers that could plausibly be quantised too.
    """
    features = int(sd["first.weight"].shape[0])
    headdim = int(sd["blocks.0.attn.qknorm.qnorm.scale"].shape[0])
    heads = features // headdim
    kvheads = int(sd["blocks.0.attn.wk.weight"].shape[0]) // headdim
    patch = 2
    channels = linear_in_features(sd, "first.weight") // (patch * patch)

    txtdim = linear_in_features(sd, "txtmlp.1.weight")
    txt_headdim = int(sd["txtfusion.layerwise_blocks.0.attn.qknorm.qnorm.scale"].shape[0])
    txtheads = txtdim // txt_headdim
    txtkvheads = int(sd["txtfusion.layerwise_blocks.0.attn.wk.weight"].shape[0]) // txt_headdim
    txtlayers = linear_in_features(sd, "txtfusion.projector.weight")

    mlpdim = int(sd["blocks.0.mlp.gate.weight"].shape[0])
    base_mlp = int(2 * features / 3)
    multiplier = max(1, round(mlpdim / base_mlp))

    config = {
        "image_model": "krea2",
        "features": features,
        "heads": heads,
        "kvheads": kvheads,
        "channels": channels,
        "layers": count_blocks(sd, "blocks.{}."),
        "multiplier": multiplier,
        "tdim": linear_in_features(sd, "tmlp.0.weight"),
        "txtdim": txtdim,
        "txtheads": txtheads,
        "txtkvheads": txtkvheads,
        "txtlayers": txtlayers,
        "patch": patch,
        "theta": 1000.0,
    }
    logger.debug(
        "detected krea2 DiT: features=%d heads=%d/%d layers=%d channels=%d txtdim=%d txtlayers=%d mult=%d",
        features, heads, kvheads, config["layers"], channels, txtdim, txtlayers, multiplier,
    )
    return config


def _detect_qwen_image(sd: dict[str, torch.Tensor]) -> dict:
    """Config for a Qwen-Image MMDiT state dict (shape-sniffed).

    ``patch_size``/``axes_dims_rope``/``theta`` are arch constants. The variant
    flags come from optional keys: ``__index_timestep_zero__`` marks the edit /
    2511 checkpoint (``default_ref_method='index_timestep_zero'``), and
    ``time_text_embed.addition_t_embedding.weight`` marks the extra 2-way t
    embedding. The plain 2512 (t2i) checkpoint has neither.
    """
    inner_dim = int(sd["img_in.weight"].shape[0])
    in_channels = linear_in_features(sd, "img_in.weight")
    attention_head_dim = int(sd["transformer_blocks.0.attn.norm_q.weight"].shape[0])
    patch = 2
    config = {
        "image_model": "qwen_image",
        "in_channels": in_channels,
        "out_channels": int(sd["proj_out.weight"].shape[0]) // (patch * patch),
        "inner_dim": inner_dim,
        "num_layers": count_blocks(sd, "transformer_blocks.{}."),
        "num_attention_heads": inner_dim // attention_head_dim,
        "attention_head_dim": attention_head_dim,
        "joint_attention_dim": linear_in_features(sd, "txt_in.weight"),
        "patch_size": patch,
        "axes_dims_rope": (16, 56, 56),
        "theta": 10000,
        "default_ref_method": (
            "index_timestep_zero" if "__index_timestep_zero__" in sd else "index"
        ),
        "use_additional_t_cond": "time_text_embed.addition_t_embedding.weight" in sd,
    }
    logger.debug(
        "detected qwen_image DiT: inner=%d heads=%d headdim=%d layers=%d in=%d out=%d ctx=%d ref=%s",
        inner_dim, config["num_attention_heads"], attention_head_dim, config["num_layers"],
        in_channels, config["out_channels"], config["joint_attention_dim"], config["default_ref_method"],
    )
    return config


def _detect_wan(sd: dict[str, torch.Tensor]) -> dict:
    """Config for a base Wan 2.1 / 2.2 DiT (shape-sniffed, mirrors ComfyUI).

    Rejects the vace / camera / s2v / humo / animate extension checkpoints (the
    native engine vendors the base t2v/i2v backbone only). ``patch_size`` /
    ``freq_dim`` / ``qk_norm`` / ``cross_attn_norm`` / ``eps`` are arch constants;
    ``num_heads`` follows Wan's fixed head_dim == 128 convention.

    The 5B ti2v model is distinguished by ``out_dim == 48`` (the 48-channel Wan22
    VAE); the 14B t2v/i2v models are ``out_dim == 16`` (Wan21 VAE). i2v carries
    the CLIP-vision projector (``img_emb``).
    """
    for key, variant in _WAN_REJECT.items():
        if key in sd:
            raise NativeEngineUnsupportedError(
                f"Wan '{variant}' checkpoints are not supported by the native engine "
                f"(base text-to-video / image-to-video only). Signature key: '{key}'."
            )

    patch_size = (1, 2, 2)
    dim = int(sd["head.modulation"].shape[-1])
    config: dict = {
        "image_model": "wan2.1",
        "dim": dim,
        "out_dim": int(sd["head.head.weight"].shape[0]) // math.prod(patch_size),
        "in_dim": int(sd["patch_embedding.weight"].shape[1]),
        "num_heads": dim // 128,                      # Wan head_dim == 128 for every release
        "ffn_dim": int(sd["blocks.0.ffn.0.weight"].shape[0]),
        "num_layers": count_blocks(sd, "blocks.{}."),
        "text_dim": linear_in_features(sd, "text_embedding.0.weight"),
        "patch_size": patch_size,
        "freq_dim": 256,
        "window_size": (-1, -1),
        "qk_norm": True,
        "cross_attn_norm": True,
        "eps": 1e-6,
    }
    # i2v carries the CLIP-vision projector (img_emb); t2v/ti2v-5B do not.
    config["model_type"] = "i2v" if "img_emb.proj.0.bias" in sd else "t2v"
    if "img_emb.emb_pos" in sd:  # first-last-frame (FLF2V) positional embed
        config["flf_pos_embed_token_number"] = int(sd["img_emb.emb_pos"].shape[1])
    if "ref_conv.weight" in sd:
        config["in_dim_ref_conv"] = int(sd["ref_conv.weight"].shape[1])

    logger.debug(
        "detected wan DiT: type=%s dim=%d heads=%d layers=%d in=%d out=%d ffn=%d text=%d",
        config["model_type"], dim, config["num_heads"], config["num_layers"],
        config["in_dim"], config["out_dim"], config["ffn_dim"], config["text_dim"],
    )
    return config


# LTX head_dims are arch constants (not shape-derivable from the inner_dim norms).
_LTX_VIDEO_HEAD_DIM = 128
_LTX_AUDIO_HEAD_DIM = 64
_LTX_CONNECTOR_HEAD_DIM = 128
_LTX_CAPTION_CHANNELS = 3840


def _parse_ltx_embedded_transformer_config(metadata: dict[str, str] | None) -> dict | None:
    """Return the embedded ``config["transformer"]`` dict from an LTX checkpoint's
    safetensors metadata, else ``None``. Same convention as the VAE detectors
    (``detect/vae_detect.py``'s ``_parse_embedded_config``) — the full config JSON
    lives under ``__metadata__["config"]``; the DiT's own slice is nested under
    the ``transformer`` key (verified against Lightricks' own
    ``model_configurator.py``: ``metadata["config"]["transformer"]``).
    """
    if not metadata:
        return None
    raw = metadata.get("config")
    if raw is None:
        return None
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    transformer = config.get("transformer") if isinstance(config, dict) else None
    return transformer if isinstance(transformer, dict) else None


def _parse_ltx_model_version(metadata: dict[str, str] | None) -> tuple[int, ...] | str | None:
    """Parse the top-level ``__metadata__["model_version"]`` field (e.g. "2.5")
    to a tuple of ints for easy ``>=`` comparisons downstream, falling back to
    the raw string when it doesn't look like a dotted version number.
    """
    raw = metadata.get("model_version") if metadata else None
    if not raw:
        return None
    parts = str(raw).split(".")
    if parts and all(p.isdigit() for p in parts):
        return tuple(int(p) for p in parts)
    return str(raw)


def _detect_ltx(sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None) -> dict:
    """Config for an LTX-2 / 2.3 / 2.5 DiT.

    ltxv (video-only) vs ltxav (audio-video) is set by ``audio_adaln_single``.
    head dims (video 128 / audio 64 / connector 128) and caption_channels (3840)
    are LTX arch constants; the core dims (inner sizes, layer count, cross-attn
    widths) are shape-sniffed and unchanged across 2.0/2.3/2.5. The connector
    diverges between LTX-2 (shared 3840, ungated, 2 blocks + caption_projection)
    and LTX-2.3/2.5 (per-stream, gated ``to_gate_logits``, 8 blocks, no
    caption_projection).

    ``has_prompt_adaln`` (diffusers ``cross_attention_adaln``) is read from the
    per-BLOCK ``prompt_scale_shift_table`` key, not the top-level
    ``prompt_adaln_single`` MLP — LTX-2.5's KV-cacheable cross-attention
    (``use_prompt_adaln_single=False``) can drop that MLP while keeping the
    per-block table (a static, timestep-independent fallback), so the two are
    detected as SEPARATE flags. ``ff_bias``/``audio_ff_bias`` (2.5: the video
    FFN can drop its bias) and ``use_keyframes_abs_pos_embedding`` (2.5.1+
    generated-keyframe checkpoints) are new shape-sniffed flags with no LTX-2/
    2.3 equivalent (all three default True/True/False, matching every earlier
    checkpoint's shape).

    When ``metadata`` carries the checkpoint's own embedded
    ``config["transformer"]`` JSON (all 2.5 checkpoints; not present pre-2.5),
    it TAKES PRIORITY over shape-sniffing for the fields it declares — shape-
    sniffing alone cannot tell "audio_ff_bias defaults True" from "audio_ff_bias
    is explicitly True", so a checkpoint that sets it False must be read from
    metadata to be detected correctly; shape-sniffing remains the sole source
    when metadata is absent (older checkpoints, or a split file without a
    compatible metadata block). ``model_version`` (the checkpoint's own
    version string, separate from this config JSON) is surfaced verbatim
    for the sampling layer to branch on later.
    """
    is_av = "audio_adaln_single.linear.weight" in sd
    inner_dim = int(sd["patchify_proj.weight"].shape[0])
    has_prompt_adaln = "transformer_blocks.0.prompt_scale_shift_table" in sd
    config: dict = {
        "image_model": "ltxav" if is_av else "ltxv",
        "in_channels": linear_in_features(sd, "patchify_proj.weight"),
        "out_channels": int(sd["proj_out.weight"].shape[0]),
        "num_attention_heads": inner_dim // _LTX_VIDEO_HEAD_DIM,
        "attention_head_dim": _LTX_VIDEO_HEAD_DIM,
        "cross_attention_dim": linear_in_features(sd, "transformer_blocks.0.attn2.to_k.weight"),
        "caption_channels": _LTX_CAPTION_CHANNELS,
        "num_layers": count_blocks(sd, "transformer_blocks.{}."),
        "has_caption_projection": "caption_projection.linear_1.weight" in sd,
        # LTX-2.3 additions (absent in LTX-2 19b).
        "blocks_gated": "transformer_blocks.0.attn1.to_gate_logits.weight" in sd,
        "has_prompt_adaln": has_prompt_adaln,
        # LTX-2.5 additions (absent pre-2.5; defaults preserve every earlier shape).
        "use_prompt_adaln_single": (
            "prompt_adaln_single.linear.weight" in sd if has_prompt_adaln else True
        ),
        "ff_bias": "transformer_blocks.0.ff.net.2.bias" in sd,
        "use_keyframes_abs_pos_embedding": "keyframes_abs_pos_embedding" in sd,
    }
    # 2.3 drives AV-cross-attention adaLN with the opposite modality's sigma
    # (diffusers ``use_cross_timestep``: "True is the newer (e.g. LTX-2.3)
    # behavior"). No state-dict signal exists, so it rides the has_prompt_adaln
    # marker (unaffected by 2.5's use_prompt_adaln_single split).
    config["use_cross_timestep"] = config["has_prompt_adaln"]
    if config["blocks_gated"]:
        config["block_gate_dim"] = int(sd["transformer_blocks.0.attn1.to_gate_logits.weight"].shape[0])
    if is_av:
        audio_inner = int(sd["audio_patchify_proj.weight"].shape[0])
        config.update(
            audio_in_channels=linear_in_features(sd, "audio_patchify_proj.weight"),
            audio_num_attention_heads=audio_inner // _LTX_AUDIO_HEAD_DIM,
            audio_attention_head_dim=_LTX_AUDIO_HEAD_DIM,
            audio_cross_attention_dim=linear_in_features(sd, "transformer_blocks.0.audio_attn2.to_k.weight"),
            audio_ff_bias="transformer_blocks.0.audio_ff.net.2.bias" in sd,
        )
        vconn = "video_embeddings_connector.learnable_registers"
        if vconn in sd:
            gate_key = "video_embeddings_connector.transformer_1d_blocks.0.attn1.to_gate_logits.weight"
            gated = gate_key in sd
            audio_conn_inner = int(sd["audio_embeddings_connector.learnable_registers"].shape[1])
            config.update(
                use_embeddings_connector=True,
                video_connector_inner=int(sd[vconn].shape[1]),
                audio_connector_inner=audio_conn_inner,
                connector_attention_head_dim=_LTX_CONNECTOR_HEAD_DIM,
                # 2.3's per-stream audio connector runs at the audio stream's own
                # width AND head dim (embedded config: audio_connector_attention_
                # head_dim=64 -> 32 heads at inner 2048); the 19b shared 3840
                # connector uses 128. Per-stream <=> connector inner == audio inner.
                audio_connector_attention_head_dim=(
                    _LTX_AUDIO_HEAD_DIM if audio_conn_inner == audio_inner else _LTX_CONNECTOR_HEAD_DIM
                ),
                connector_num_layers=count_blocks(sd, "video_embeddings_connector.transformer_1d_blocks.{}."),
                connector_gated=gated,
                connector_gate_dim=int(sd[gate_key].shape[0]) if gated else 32,
            )
        else:
            config["use_embeddings_connector"] = False

    embedded = _parse_ltx_embedded_transformer_config(metadata)
    if embedded is not None:
        if "ff_bias" in embedded:
            config["ff_bias"] = bool(embedded["ff_bias"])
        if is_av and "audio_ff_bias" in embedded:
            config["audio_ff_bias"] = bool(embedded["audio_ff_bias"])
        if "use_prompt_adaln_single" in embedded:
            config["use_prompt_adaln_single"] = bool(embedded["use_prompt_adaln_single"])
        if "use_keyframes_abs_pos_embedding" in embedded:
            config["use_keyframes_abs_pos_embedding"] = bool(embedded["use_keyframes_abs_pos_embedding"])
    config["model_version"] = _parse_ltx_model_version(metadata)

    logger.debug(
        "detected %s DiT: inner=%d layers=%d in=%d av=%s caption_proj=%s prompt_adaln=%s/%s "
        "ff_bias=%s/%s version=%s",
        config["image_model"], inner_dim, config["num_layers"], config["in_channels"],
        is_av, config["has_caption_projection"], config["has_prompt_adaln"],
        config["use_prompt_adaln_single"], config["ff_bias"], config.get("audio_ff_bias"),
        config["model_version"],
    )
    return config


def _detect_anima(sd: dict[str, torch.Tensor]) -> dict:
    """Config for an Anima state dict (shape-sniffed).

    Anima is Cosmos-Predict2's ``MiniTrainDIT`` (adaLN-modulated 3D DiT) with an
    in-model ``LLMAdapter`` text-fusion head. Every hyper-parameter is read from
    tensor shapes; ``patch_spatial``/``patch_temporal``, the positional-embedding
    bounds (``max_img_h/w``, ``max_frames``) and the fps range are arch constants
    (ComfyUI ``model_detection`` Anima branch). The RoPE extrapolation ratios are
    keyed off ``in_channels`` (16 = t2i, the local ``anima_aestheticV10b``).
    """
    patch_spatial = 2
    patch_temporal = 1
    pack = patch_spatial * patch_spatial * patch_temporal  # 4

    model_channels = int(sd["x_embedder.proj.1.weight"].shape[0])
    concat_padding_mask = True
    x_in = linear_in_features(sd, "x_embedder.proj.1.weight") // pack
    in_channels = x_in - int(concat_padding_mask)
    out_channels = int(sd["final_layer.linear.weight"].shape[0]) // pack

    head_dim = int(sd["blocks.0.self_attn.q_norm.weight"].shape[0])
    num_heads = model_channels // head_dim
    crossattn_emb_channels = linear_in_features(sd, "blocks.0.cross_attn.k_proj.weight")
    adaln_lora_dim = int(sd["blocks.0.adaln_modulation_self_attn.1.weight"].shape[0])
    mlp_ratio = int(sd["blocks.0.mlp.layer1.weight"].shape[0]) / model_channels

    # LLMAdapter (source = Qwen3-0.6B hidden, target = T5 token embedding).
    llm_head_dim = int(sd["llm_adapter.blocks.0.cross_attn.q_norm.weight"].shape[0])
    llm_model_dim = int(sd["llm_adapter.blocks.0.cross_attn.q_proj.weight"].shape[0])
    llm_vocab_size = int(sd["llm_adapter.embed.weight"].shape[0])
    llm_target_dim = int(sd["llm_adapter.embed.weight"].shape[1])
    llm_source_dim = linear_in_features(sd, "llm_adapter.blocks.0.cross_attn.k_proj.weight")

    # in_channels 16 -> t2i (4x spatial rope extrapolation); 17 -> i2v (3x).
    if in_channels <= 16:
        rope_h = rope_w = 4.0
        rope_t = 1.0
    else:
        rope_h = rope_w = 3.0
        rope_t = 1.0

    config = {
        "image_model": "anima",
        "in_channels": in_channels,
        "out_channels": out_channels,
        "model_channels": model_channels,
        "num_blocks": count_blocks(sd, "blocks.{}."),
        "num_heads": num_heads,
        "crossattn_emb_channels": crossattn_emb_channels,
        "patch_spatial": patch_spatial,
        "patch_temporal": patch_temporal,
        "concat_padding_mask": concat_padding_mask,
        "mlp_ratio": mlp_ratio,
        "use_adaln_lora": True,
        "adaln_lora_dim": adaln_lora_dim,
        "max_img_h": 240,
        "max_img_w": 240,
        "max_frames": 128,
        "min_fps": 1,
        "max_fps": 30,
        "rope_h_extrapolation_ratio": rope_h,
        "rope_w_extrapolation_ratio": rope_w,
        "rope_t_extrapolation_ratio": rope_t,
        "rope_enable_fps_modulation": False,
        "llm_source_dim": llm_source_dim,
        "llm_target_dim": llm_target_dim,
        "llm_model_dim": llm_model_dim,
        "llm_num_layers": count_blocks(sd, "llm_adapter.blocks.{}."),
        "llm_num_heads": llm_model_dim // llm_head_dim,
        "llm_vocab_size": llm_vocab_size,
    }
    logger.debug(
        "detected anima DiT: mc=%d heads=%d blocks=%d in=%d out=%d ctx=%d llm(dim=%d layers=%d vocab=%d)",
        model_channels, num_heads, config["num_blocks"], in_channels, out_channels,
        crossattn_emb_channels, llm_model_dim, config["llm_num_layers"], llm_vocab_size,
    )
    return config


def _detect_lumina2(sd: dict[str, torch.Tensor]) -> dict | None:
    """Z-Image (Lumina-Image-2.0 NextDiT). Only the ``dim==3840`` variant is
    supported; the original ``dim==2304`` Lumina2 falls through to ``None`` so it
    surfaces as an unsupported model rather than mis-loading as Z-Image.

    Mirrors ComfyUI ``model_detection`` (the Lumina2 branch): ``dim`` /
    ``cap_feat_dim`` / layer count / FFN width come from shapes; the head split
    and RoPE constants are the ComfyUI ``ZImage`` values (not shape-recoverable --
    qkv is fused and Z-Image has no GQA).
    """
    cap = sd[_LUMINA2_SIG]                       # cap_embedder.1.weight [dim, cap_feat_dim]
    dim = int(cap.shape[0])
    if dim != 3840:
        return None                             # only Z-Image is vendored
    patch_size = 2
    in_channels = linear_in_features(sd, "x_embedder.weight") // (patch_size * patch_size)
    config: dict = {
        "image_model": "lumina2",
        "z_image_modulation": True,
        "patch_size": patch_size,
        "in_channels": in_channels,
        "dim": dim,
        "cap_feat_dim": linear_in_features(sd, _LUMINA2_SIG),
        "n_layers": count_blocks(sd, "layers.{}."),
        "n_refiner_layers": count_blocks(sd, "noise_refiner.{}."),
        "intermediate_size": int(sd["layers.0.feed_forward.w1.weight"].shape[0]),
        # ComfyUI ZImage (dim 3840): no GQA, head_dim 128, 3-axis RoPE.
        "n_heads": 30,
        "n_kv_heads": 30,
        "axes_dims": [32, 48, 48],
        "axes_lens": [1536, 512, 512],
        "rope_theta": 256.0,
        "time_scale": 1000.0,
        "pad_tokens_multiple": 32 if "cap_pad_token" in sd else 0,
    }
    logger.debug(
        "detected Z-Image (lumina2) DiT: dim=%d cap=%d layers=%d ffn=%d in=%d",
        dim, config["cap_feat_dim"], config["n_layers"], config["intermediate_size"], in_channels,
    )
    return config


# SeedVR2 patchify is fixed at (t,h,w) = (1,2,2) -> 4 latent voxels per token; the
# proj_qkv fuses q/k/v (x3) and RoPE head_dim is derived from the norm_q length.
_SEEDVR2_PATCH_PACK = 1 * 2 * 2


def _detect_seedvr2(sd: dict[str, torch.Tensor]) -> dict:
    """Config for a SeedVR2 NaDiT state dict (shape-sniffed), 3B or 7B.

    Every load-bearing hyper-parameter is derivable from tensor shapes (see the
    ``arch/seedvr2*/config.py`` ``from_detect_config`` field consumers); the patch
    size ``(1,2,2)``, window, RoPE dim etc. are arch constants defaulted by the
    config. ``vid_in.proj``/``vid_out.proj`` are native-resolution patch Linears
    folding ``patch_t*patch_h*patch_w == 4`` voxels into the channel dim, so the raw
    channel counts divide by 4.

    The 3B and 7B are structurally distinct backbones (separate arch modules), so a
    ``seedvr2_variant`` discriminator is emitted for the registry to split on. The
    load-bearing fork is the MLP: the 3B is SwiGLU (a ``mlp...proj_in_gate`` weight
    + a ``vid_out_norm`` head), the 7B is a plain GELU MLP with no output-norm head.
    ``mm_layers`` counts blocks that keep split ``.vid``/``.txt`` attention weights
    (== num_layers for the 7B, where every block is multimodal; a prefix for the 3B,
    whose later blocks share one ``.all`` set).
    """
    pack = _SEEDVR2_PATCH_PACK
    vid_in = sd["vid_in.proj.weight"]                  # [vid_dim, vid_in_channels * pack]
    vid_dim = int(vid_in.shape[0])
    head_dim = int(sd["blocks.0.attn.norm_q.vid.weight"].shape[0])
    num_layers = count_blocks(sd, "blocks.{}.")
    mm_layers = sum(
        1 for i in range(num_layers) if f"blocks.{i}.attn.proj_qkv.vid.weight" in sd
    )
    # 3B = SwiGLU MLP (proj_in_gate) + vid_out_norm head; 7B = plain GELU MLP, no head.
    is_3b = "blocks.0.mlp.vid.proj_in_gate.weight" in sd
    variant = "3b" if is_3b else "7b"
    config = {
        "image_model": "seedvr2",
        "seedvr2_variant": variant,
        "vid_in_channels": linear_in_features(sd, "vid_in.proj.weight") // pack,
        "vid_out_channels": int(sd["vid_out.proj.weight"].shape[0]) // pack,
        "vid_dim": vid_dim,
        "txt_in_dim": linear_in_features(sd, "txt_in.weight"),
        "emb_dim": int(sd["emb_in.proj_out.weight"].shape[0]),
        "num_layers": num_layers,
        "mm_layers": mm_layers,
        # proj_qkv rows = heads * head_dim * 3 (fused q/k/v).
        "heads": int(sd["blocks.0.attn.proj_qkv.vid.weight"].shape[0]) // (3 * head_dim),
        "head_dim": head_dim,
        "mlp_hidden": int(sd["blocks.0.mlp.vid.proj_in.weight"].shape[0]),
    }
    logger.debug(
        "detected seedvr2 %s DiT: vid_dim=%d heads=%d headdim=%d layers=%d mm=%d "
        "in=%d out=%d txt=%d emb=%d mlp=%d",
        variant, vid_dim, config["heads"], head_dim, num_layers, mm_layers,
        config["vid_in_channels"], config["vid_out_channels"], config["txt_in_dim"],
        config["emb_dim"], config["mlp_hidden"],
    )
    return config


# MiniMax-H3 patch is fixed at (t,h,w) = (1,2,2) -> 4 latent voxels per video
# token; not shape-recoverable from a Linear's flat in-features alone.
_MINIMAX_H3_PATCH = (1, 2, 2)


def _detect_minimax_h3(sd: dict[str, torch.Tensor]) -> dict:
    """Config for a MiniMax-H3 packed-sequence DiT state dict (shape-sniffed).

    Covers both checkpoint shapes fl2va/ref2va ship in (structurally
    identical -- see ``ai/minimax_h3/h3_architecture_dossier.md`` §B): full
    (``time_embedder`` present) and pruned (``adaln_t_table`` present instead,
    ~13B of AdaLN-only weight the pruned checkpoint drops entirely). Every
    field is read from tensor shapes; ``patch_size`` / ``rope_theta`` / the
    norm epsilons are arch constants MiniMax-H3 does not vary.

    ``blocks.0.attn.qkv_proj.weight`` is ``[3*inner_dim, hidden_size]`` (fused
    q|k|v on the OUTPUT axis, never nvfp4-packed in the released checkpoints —
    only ``float8_e4m3fn`` scaled-fp8 -- so a plain ``.shape`` read is safe;
    ``linear_in_features`` is used anyway wherever the IN dimension is read,
    for the same nvfp4-awareness every other detector here keeps).
    """
    hidden_size = int(sd[_MINIMAX_H3_SIG].shape[0])
    num_layers = count_blocks(sd, "blocks.{}.")
    num_refiner_layers = count_blocks(sd, "token_refiner.blocks.{}.")
    attention_head_dim = int(sd["blocks.0.attn.q_norm.weight"].shape[0])
    qkv_out = int(sd["blocks.0.attn.qkv_proj.weight"].shape[0])
    num_attention_heads = qkv_out // (3 * attention_head_dim)
    ffn_dim = int(sd["blocks.0.mlp.fc1.weight"].shape[0]) // 2  # fused value+gate
    patch_voxels = math.prod(_MINIMAX_H3_PATCH)
    in_channels = int(sd["final_layer.video_out.weight"].shape[0]) // patch_voxels
    audio_in_channels = int(sd["final_layer.audio_out.weight"].shape[0])
    text_dim = linear_in_features(sd, "condition_proj.weight")
    rope_freq_dim = int(sd["rope.inv_freq"].shape[0])

    config: dict = {
        "image_model": "minimax_h3",
        "hidden_size": hidden_size,
        "num_layers": num_layers,
        "num_refiner_layers": num_refiner_layers,
        "num_attention_heads": num_attention_heads,
        "attention_head_dim": attention_head_dim,
        "ffn_dim": ffn_dim,
        "in_channels": in_channels,
        "audio_in_channels": audio_in_channels,
        "patch_size": _MINIMAX_H3_PATCH,
        "text_dim": text_dim,
        "rope_freq_dim": rope_freq_dim,
    }

    if "adaln_t_table" in sd:
        grid, width = sd["adaln_t_table"].shape
        config["pruned"] = True
        config["adaln_curve_grid"] = int(grid)
        config["time_embed_dim"] = int(width)
    else:
        config["pruned"] = False
        config["freq_dim"] = linear_in_features(sd, "time_embedder.proj_in.weight")
        config["time_embed_hidden_dim"] = int(sd["time_embedder.proj_in.weight"].shape[0])
        config["time_embed_dim"] = int(sd["time_embedder.proj_out.weight"].shape[0])

    logger.debug(
        "detected minimax_h3 DiT: hidden=%d heads=%d headdim=%d layers=%d refiner=%d "
        "ffn=%d in=%d audio_in=%d text=%d pruned=%s time_embed=%d",
        hidden_size, num_attention_heads, attention_head_dim, num_layers, num_refiner_layers,
        ffn_dim, in_channels, audio_in_channels, text_dim, config["pruned"], config["time_embed_dim"],
    )
    return config


# MiniMax-Music3's DiT has no per-head weight (only the fused ``to_qkv`` output
# width and a partial-RoPE frequency count), so the head count/head-dim split
# and RoPE theta are architecture constants -- not shape-recoverable from any
# single tensor -- exactly like `_MINIMAX_H3_PATCH` above.
_MINIMAX_MUSIC3_NUM_ATTENTION_HEADS = 32
_MINIMAX_MUSIC3_ROPE_THETA = 10000.0


def _detect_minimax_music3(sd: dict[str, torch.Tensor]) -> dict:
    """Config for the MiniMax-Music3 DiT + fused condition-encoder state dict.

    One file holds the flow-matching transformer AND the condition encoder that
    mixes the AR stage's per-frame hidden slots into it (``cond_layer_logits``,
    ``cond_layer_scale``, ``latent_conditioners.0``) -- see the arch package's
    module docstring for the forward-pass shape. Every field below is read from
    a tensor shape actually present in both released precisions (fp16/fp32);
    only ``num_attention_heads``/``rope_theta`` are constants (see above). Field
    names match ``arch.minimax_music3.model.MiniMaxMusic3DitConfig.from_detect_config``
    exactly -- that classmethod reads every field through ``config.get(key,
    default)``, so a name that doesn't match silently falls back to the default
    instead of raising, which would have hidden a real detection bug behind a
    coincidentally-correct default (the released checkpoint's real shapes ARE
    every one of those defaults).
    """
    hidden_size = linear_in_features(sd, "diffusion_transformer.transformer.layers.0.self_attn.to_qkv.weight")
    num_layers = count_blocks(sd, "diffusion_transformer.transformer.layers.{}.")
    ffn_inner_dim = linear_in_features(sd, "diffusion_transformer.transformer.layers.0.ff.ff.2.weight")
    in_channels = int(sd["diffusion_transformer.transformer.project_out.weight"].shape[0])
    condition_dim = int(sd[_MINIMAX_MUSIC3_SIG2].shape[0])
    condition_hidden_dim = linear_in_features(sd, _MINIMAX_MUSIC3_SIG2)
    num_condition_layers = int(sd[_MINIMAX_MUSIC3_SIG].shape[0])
    # `inv_freq`'s row count is the number of rotated frequency PAIRS; the
    # arch config's `rotary_dim` counts the rotated head DIMENSIONS (2 per
    # pair, cos+sin), and `fourier_dim` is the Fourier embedding's concatenated
    # sin+cos width -- both are the raw shape's `weight.shape[0]` doubled.
    rotary_dim = int(sd["diffusion_transformer.transformer.rotary_pos_emb.inv_freq"].shape[0]) * 2
    fourier_dim = int(sd["diffusion_transformer.timestep_features.weight"].shape[0]) * 2

    config: dict = {
        # Matches arch/minimax_music3/model.py's MINIMAX_MUSIC3_DIT constant --
        # "_dit" distinguishes the diffusion-transformer checkpoint from the
        # text-encoder (te_type="minimax_music3", a separate config namespace)
        # and the DAV vocoder, since Music3 ships as three independent files.
        "image_model": "minimax_music3_dit",
        "hidden_size": hidden_size,
        "num_layers": num_layers,
        "num_attention_heads": _MINIMAX_MUSIC3_NUM_ATTENTION_HEADS,
        "attention_head_dim": hidden_size // _MINIMAX_MUSIC3_NUM_ATTENTION_HEADS,
        "ffn_inner_dim": ffn_inner_dim,
        "in_channels": in_channels,
        "condition_dim": condition_dim,
        "condition_hidden_dim": condition_hidden_dim,
        "num_condition_layers": num_condition_layers,
        "rotary_dim": rotary_dim,
        "rope_theta": _MINIMAX_MUSIC3_ROPE_THETA,
        "fourier_dim": fourier_dim,
    }
    logger.debug(
        "detected minimax_music3 DiT: hidden=%d heads=%d headdim=%d layers=%d ffn=%d "
        "in_ch=%d cond_dim=%d cond_hidden=%d cond_layers=%d",
        hidden_size, config["num_attention_heads"], config["attention_head_dim"], num_layers,
        ffn_inner_dim, in_channels, condition_dim, condition_hidden_dim, num_condition_layers,
    )
    return config
