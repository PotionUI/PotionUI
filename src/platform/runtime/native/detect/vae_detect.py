"""VAE architecture detection.

Seven unrelated VAE families are detected here (plus SeedVR2's, added later --
see ``detect_seedvr2_vae_config`` below, not renumbered into this list):

1. **Flux 2D AE family** (``detect_vae_config``) -- two distinct local checkpoints,
   verified by header dump:

    ae.sft            ldm-style keys (decoder.mid.attn_1.*), 16-channel latent,
                      encoder.conv_out [32,...] (mean+logvar), no quant_conv/bn.
    flux2-vae         diffusers-style keys (decoder.mid_block.attentions.0.*),
                      32-channel latent, encoder.conv_out [64,...], plus a
                      top-level batchnorm (`bn.*`) and quant_conv/post_quant_conv.

   Config dict schema:
    vae_type:        "flux_ae" | "flux2_ae"
    latent_channels: int              -- 16 (flux) or 32 (flux2)
    in_channels:     int              -- image channels (3)
    out_channels:    int              -- image channels (3)
    key_layout:      "ldm" | "diffusers"
    has_quant_conv:  bool
    has_batchnorm:   bool

2. **LTX-2/2.3 causal video VAE** (``detect_ltx_video_vae_config``) -- unlike
   every other detector here, this one reads the checkpoint's OWN embedded
   config rather than sniffing keys/shapes: LTX safetensors carry a full JSON
   config in ``__metadata__["config"]`` (verified: identical ``config["vae"]``
   block present in both standalone VAE files and the all-in-one DiT
   checkpoint). Detection is "does ``config.vae._class_name ==
   CausalVideoAutoencoder`` exist", operating on the *metadata* dict, not the
   state dict -- see ``vae/ltx_causal_video.py`` module docstring.

   Config dict schema: the raw embedded ``config["vae"]`` dict, passed through
   to ``LTXCausalVideoVAE.from_config`` unmodified.

2b. **LTX-2.5 diffusion-decoder video VAE**
   (``detect_ltx_diffusion_vae_config``) -- the same embedded-config pattern,
   distinguished only by ``config.vae._class_name == "CausalDiffusionVAE"``.
   The 2.3-shaped causal conv encoder paired with a denoising
   ``NADiffusionDecoder`` (``det_stages``/``diff_blocks``), see
   ``vae/ltx_diffusion_video.py``. The two LTX video detectors are mutually
   exclusive by ``_class_name``.

   Config dict schema: the raw embedded ``config["vae"]`` dict -- NESTED
   (``encoder``/``decoder`` sub-dicts) unlike #2's flat one -- passed through to
   ``LTXDiffusionVideoVAE.from_config`` unmodified.

3. **LTX-2/2.3 audio VAE** (``detect_ltx_audio_vae_config``) -- same
   embedded-metadata-JSON pattern as #2, but ``config["audio_vae"]`` has no
   ``_class_name`` marker (verified: absent from both local audio VAE files'
   metadata). Detection instead anchors on the ``model.params.ddconfig``
   (or ``.encoder``) shape being present with the audio-specific
   ``causality_axis``/``mel_bins`` fields -- see ``vae/ltx_audio.py``.

   Config dict schema: the raw embedded ``config["audio_vae"]`` dict, passed
   through to ``LTXAudioAutoencoder.from_config`` unmodified.

4. **LTX-2/2.3 vocoder** (``detect_ltx_vocoder_config``) -- same pattern,
   ``config["vocoder"]``. LTX2's real config is a flat HiFi-GAN-v1 shape
   (``upsample_rates`` at the top level); LTX23's is a DIFFERENT nested shape
   (``{"vocoder": {...}, "bwe": {...}}``, ``resblock: "AMP1"``,
   ``activation: "snakebeta"``) that is NOT vendored (see ``vae/ltx_audio.py``
   module docstring) -- detection still returns whatever dict is embedded so
   the loader/arch module can raise a precise "unsupported shape" error
   rather than a generic "no config found" one.

5. **LTX-2.3 spatial latent upsampler** (``detect_ltx_latent_upsampler_config``)
   -- a standalone checkpoint (e.g.
   ``ltx-2.3-spatial-upscaler-x1.5-1.0.safetensors``), NOT a slice of the
   all-in-one DiT checkpoint (verified: the all-in-one checkpoint's top-level
   prefixes are exactly ``{model, vae, audio_vae, vocoder,
   text_embedding_projection}`` -- no ``latent_upsampler.*``/``upsampler.*``
   anywhere, see ``vae/ltx_latent_upsampler.py``'s module docstring). Same
   embedded-metadata-JSON pattern as the video VAE, but the config is the
   TOP-LEVEL ``__metadata__["config"]`` dict itself (no ``vae``/``upsampler``
   nesting) -- verified against ComfyUI's own loader
   (``comfy_extras/nodes_hunyuan.py``'s ``LatentUpscaleModelLoader``, which
   does exactly ``config = json.loads(metadata["config"])`` for this
   checkpoint shape). Detection anchors on ``config["_class_name"] ==
   "LatentUpsampler"`` (the marker ComfyUI's own ``LatentUpsampler.config()``
   writes back out).

   Config dict schema: the raw embedded config dict (``in_channels``,
   ``mid_channels``, ``num_blocks_per_stage``, ``dims``, ``spatial_upsample``,
   ``temporal_upsample``, ``spatial_scale``, ``rational_resampler``), passed
   through to ``LTXLatentUpsampler.from_config`` unmodified.

6. **Causal 3D (Wan-shaped) VAE family** (``detect_causal3d_vae_config``) --
   ``models/vae/qwen_image_vae.safetensors`` is architecturally the Wan 2.1 VAE
   verbatim (see ``vae/causal_3d.py`` module docstring for the full story: there
   is no dedicated "Qwen-Image VAE" class in ComfyUI either, Qwen-Image's
   checkpoint just *is* a Wan-2.1-shaped one). Signature: ``decoder.middle.0.
   residual.0.gamma`` present (the RMS_norm gamma inside the bottleneck
   ResidualBlock -- unique to this family, absent from the Flux 2D AE). The
   Wan 2.2 variant additionally has a nested
   ``decoder.upsamples.0.upsamples.0.residual.2.weight`` key (ComfyUI's own
   signature for it) -- detected separately by ``detect_causal3d_v2_vae_config``
   below, not this function.

   Config dict schema:
    vae_type:        "qwen_image" (Wan 2.1 shape; the same checkpoint shape
                      Qwen-Image and Krea-2 both consume)
    latent_channels: int              -- 16
    in_channels:     int              -- image channels (3)
    out_channels:    int              -- image channels (3)

7. **Causal 3D Wan 2.2 VAE** (``detect_causal3d_v2_vae_config``) -- verified
   against ``models/vae/wan2.2_vae.safetensors``: same bottleneck signature as
   Wan 2.1 plus the nested ``decoder.upsamples.0.upsamples.0.*`` key (see
   ``vae/causal_3d_v2.py`` module docstring for the architecture). 48-channel
   latent, patchified (2x2 space-to-depth) input/output.

   Config dict schema:
    vae_type:        "wan2.2"
    latent_channels: int              -- 48
    in_channels:     int              -- image channels (3)
    out_channels:    int              -- image channels (3)

8. **MiniMax-H3 video VAE** (``detect_minimax_h3_video_vae_config``) -- the
   Comfy-Org single-file repack (``minimax_h3_video_vae_fp16.safetensors``).
   Signature: ``decoder.mask_token`` + ``decoder.register_tokens`` present
   (unique to this family's ViT decoder) and a 5D ``encoder.conv_in.weight``
   (causal 3D conv, distinguishing it from the Flux 2D AE's 4D conv of the
   same key name). See ``vae/minimax_h3_video.py`` for the full architecture
   and the repack-vs-diffusers naming discrepancies. Operates on ``sd``
   (structural signature) with an optional ``metadata`` fallback for the
   embedded ``__metadata__["minimax_h3_video_vae"]`` JSON (``vae_clip_length``/
   ``vae_token_drop``) -- shape-derived values (latent width, decoder dim,
   decoder layer count) take priority when present in ``sd``; everything else
   is this family's one fixed geometry (block_out_channels, decoder heads,
   rope theta/ratio, ...), since only one variant has ever shipped.

   Config dict schema: matches ``MiniMaxH3VideoVAE.from_config``'s kwargs
   (``latent_channels``, ``block_out_channels``, ``layers_per_block``,
   ``spatial_downsample_factors``, ``temporal_downsample_factors``,
   ``decoder_num_layers``, ``decoder_num_attention_heads``,
   ``decoder_attention_head_dim``, ``decoder_num_register_tokens``,
   ``decoder_ffn_mult``, ``decoder_rope_theta``, ``decoder_rope_dim_ratio``,
   ``decoder_norm_eps``, ``clip_length``, ``token_drop``).

9. **MiniMax-H3 audio VAE** (``detect_minimax_h3_audio_vae_config``) -- the
   Comfy-Org single-file repack (``minimax_h3_audio_vae_fp32.safetensors``).
   Signature: ``pre_block.attn.qkv.weight`` + ``dec_in_proj.weight`` +
   ``decoder.conv_pre.weight`` present (unique to this family's DAC-encoder +
   BigVGAN-decoder shape). See ``vae/minimax_h3_audio.py`` for the full
   architecture and the weight_norm-fused-at-export discrepancy.

   Config dict schema: matches ``MiniMaxH3AudioVAE.from_config``'s kwargs
   (``encoder_dim``, ``encoder_rates``, ``latent_dim``, ``latent_channels``,
   ``num_attention_heads``, ``decoder_dim``, ``decoder_rates``,
   ``decoder_kernel_sizes``, ``resblock_kernel_sizes``,
   ``resblock_dilation_sizes``, ``sample_rate``).

9b. **MiniMax-Music3 DAV vocoder** (``detect_minimax_music3_dav_config``) --
   the Comfy-Org single-file repack (``minimax_music3_dav.safetensors``, 121
   keys, all F32). Signature: ``dec_in_proj.weight`` (plain, no weight_norm)
   plus both ends of the flat ``decoder.model.{0..6}`` stack
   (``decoder.model.0.weight_v`` / ``decoder.model.6.weight_v``) -- unique to
   this family (H3's audio VAE above uses attribute-named
   ``decoder.conv_pre.*``/``decoder.conv_post.*``, not a numeric ``.model.N``
   ``nn.Sequential``). Reads the RAW state dict, still carrying
   ``weight_g``/``weight_v`` -- the loader folds them into plain ``weight``
   via ``fold_weight_norm_conv`` before ``load_into_module``. See
   ``vae/minimax_music3_dav.py`` for the full architecture and the
   fold-not-plain discrepancy (opposite direction from H3's audio VAE: THAT
   repack ships plain weights already, THIS one still carries weight_norm).

   Config dict schema: matches ``MiniMaxMusic3DAV.from_config``'s kwargs
   (``latent_channels``, ``decoder_input_dim``, ``decoder_hidden_dim``,
   ``upsampling_ratios``, ``sample_rate``).

10. **LTX-2.5 duration head** (``detect_ltx_duration_head_config``) -- not a
   VAE at all: a few-MB regressor over the prompt connector outputs
   (``arch/ltx/duration_head.py``). It lives here for the same reason #5 does
   -- an optional standalone LTX component whose detection is one small
   function, kept beside its siblings rather than in a module of its own.
   Unlike every other LTX detector it does NOT read an embedded config:
   ``ltx-2.5-duration-head-bf16.safetensors`` carries no hyperparameters, so
   the dims come from weight shapes (matching diffusers' own converter, which
   does the same) and ``num_pooler_heads`` -- the one value shapes cannot
   recover -- falls back to the trained value, 4. Signature:
   ``attention_pooler.query_tokens``, with or without a ``duration_head.``
   prefix.

   Config dict schema: matches ``LTXDurationHead.from_config``'s kwargs
   (``video_cross_attention_dim``, ``audio_cross_attention_dim``,
   ``pooler_hidden_dim``, ``num_queries``, ``num_pooler_heads``,
   ``mlp_hidden_dim``).
"""

from __future__ import annotations

import json
import logging

import torch

logger = logging.getLogger(__name__)

# MiniMax-H3's one released variant's fixed geometry (mirrored from
# ``vae/minimax_h3_video.py``/``vae/minimax_h3_audio.py`` as plain literals,
# NOT imported from there: this module is imported by
# ``detect/__init__.py`` at package-init time, and ``vae/__init__.py``
# imports ``vae/loader.py``, which imports back from this module -- a
# top-level cross-import here would be circular. Every other detector in
# this file follows the same self-contained convention (e.g.
# ``detect_causal3d_v2_vae_config``'s hardcoded ``else 48``).
_H3_VIDEO_LATENT_CHANNELS = 24
_H3_VIDEO_BLOCK_OUT_CHANNELS: tuple[int, ...] = (128, 256, 256, 512, 512, 1024)
_H3_VIDEO_LAYERS_PER_BLOCK = 2
_H3_VIDEO_SPATIAL_DOWNSAMPLE_FACTORS: tuple[int, ...] = (2, 2, 2, 2, 1, 1)
_H3_VIDEO_TEMPORAL_DOWNSAMPLE_FACTORS: tuple[int, ...] = (1, 2, 2, 1, 1, 1)
_H3_VIDEO_DECODER_NUM_LAYERS = 36
_H3_VIDEO_DECODER_NUM_ATTENTION_HEADS = 32
_H3_VIDEO_DECODER_ATTENTION_HEAD_DIM = 64
_H3_VIDEO_DECODER_NUM_REGISTER_TOKENS = 4
_H3_VIDEO_DECODER_FFN_MULT = 4
_H3_VIDEO_DECODER_ROPE_THETA = 100.0
_H3_VIDEO_DECODER_ROPE_DIM_RATIO = 0.75
_H3_VIDEO_DECODER_NORM_EPS = 1e-5
_H3_VIDEO_CLIP_LENGTH = 17
_H3_VIDEO_TOKEN_DROP = 3

_H3_AUDIO_ENCODER_DIM = 64
_H3_AUDIO_ENCODER_RATES: tuple[int, ...] = (2, 4, 4, 5, 5)
_H3_AUDIO_LATENT_DIM = 2048
_H3_AUDIO_LATENT_CHANNELS = 32
_H3_AUDIO_NUM_ATTENTION_HEADS = 8
_H3_AUDIO_DECODER_DIM = 1024
_H3_AUDIO_DECODER_RATES: tuple[int, ...] = (5, 5, 2, 2, 2, 2, 2)
_H3_AUDIO_DECODER_KERNEL_SIZES: tuple[int, ...] = (9, 9, 4, 4, 4, 4, 4)
_H3_AUDIO_RESBLOCK_KERNEL_SIZES: tuple[int, ...] = (3, 7, 11)
_H3_AUDIO_RESBLOCK_DILATION_SIZES: tuple[tuple[int, ...], ...] = ((1, 3, 5), (1, 3, 5), (1, 3, 5))
_H3_AUDIO_SAMPLE_RATE = 32000

# The duration head's own constants, mirrored here as plain literals for the
# same no-cross-import reason as the MiniMax-H3 block above.
_LTX_DURATION_HEAD_PREFIX = "duration_head."
_LTX_DURATION_QUERY_TOKENS = "attention_pooler.query_tokens"
_LTX_DURATION_POOLER_HEADS = 4


def detect_vae_config(sd: dict[str, torch.Tensor]) -> dict | None:
    """Return a VAE config dict for a Flux 2D AE state dict, else ``None``."""
    if "encoder.conv_in.weight" not in sd or "decoder.conv_out.weight" not in sd:
        return None

    conv_in = sd["encoder.conv_in.weight"]      # [128, image_ch, 3, 3]
    # The Flux 2D AE convs are 4D; a 5D conv_in is a causal-3D VAE that also uses
    # the ``encoder.conv_in``/``decoder.conv_out`` key names (SeedVR2's inflated
    # diffusers AE — detected by ``detect_seedvr2_vae_config``), not this family.
    if conv_in.ndim != 4:
        return None
    conv_out = sd["decoder.conv_out.weight"]    # [image_ch, 128, 3, 3]
    in_channels = int(conv_in.shape[1])
    out_channels = int(conv_out.shape[0])

    has_quant_conv = "quant_conv.weight" in sd
    has_batchnorm = any(k.startswith("bn.") for k in sd)
    is_diffusers = any(k.startswith("decoder.mid_block.") for k in sd)

    # latent channels come from the decoder input convolution.
    latent_channels = int(sd["decoder.conv_in.weight"].shape[1])

    if is_diffusers or has_quant_conv or has_batchnorm:
        vae_type = "flux2_ae"
        key_layout = "diffusers"
    else:
        vae_type = "flux_ae"
        key_layout = "ldm"

    config = {
        "vae_type": vae_type,
        "latent_channels": latent_channels,
        "in_channels": in_channels,
        "out_channels": out_channels,
        "key_layout": key_layout,
        "has_quant_conv": has_quant_conv,
        "has_batchnorm": has_batchnorm,
    }
    logger.debug(
        "detected %s VAE: latent=%d layout=%s quant_conv=%s bn=%s",
        vae_type, latent_channels, key_layout, has_quant_conv, has_batchnorm,
    )
    return config


def detect_ltx_video_vae_config(metadata: dict[str, str]) -> dict | None:
    """Return the embedded ``config["vae"]`` dict from an LTX-2/2.3 checkpoint's
    safetensors metadata, else ``None``. Operates on ``metadata`` (the
    ``__metadata__`` header ``load_torch_file`` returns alongside the state
    dict), not the state dict itself -- see module docstring for why.
    """
    raw = metadata.get("config")
    if raw is None:
        return None
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    vae_config = config.get("vae")
    if not isinstance(vae_config, dict):
        return None
    if vae_config.get("_class_name") != "CausalVideoAutoencoder":
        return None

    logger.debug(
        "detected LTX CausalVideoAutoencoder VAE: latent=%s patch_size=%s timestep_conditioning=%s",
        vae_config.get("latent_channels"), vae_config.get("patch_size"),
        vae_config.get("timestep_conditioning"),
    )
    return vae_config


def detect_ltx_diffusion_vae_config(metadata: dict[str, str]) -> dict | None:
    """Return the embedded ``config["vae"]`` dict from an LTX-2.5
    ``CausalDiffusionVAE`` checkpoint's safetensors metadata, else ``None``.

    Same embedded-metadata pattern as ``detect_ltx_video_vae_config``, and
    deliberately disjoint from it: the two differ only by ``_class_name``, so a
    2.5 diffusion-decoder file is invisible to the conv-decode detector and
    vice versa. The returned dict is NESTED
    (``{"encoder": {...}, "decoder": {...}}``) where the conv-decode config is
    flat -- ``LTXDiffusionVideoVAE.from_config`` flattens it.
    """
    config = _parse_embedded_config(metadata)
    if not isinstance(config, dict):
        return None
    vae_config = config.get("vae")
    if not isinstance(vae_config, dict):
        return None
    if vae_config.get("_class_name") != "CausalDiffusionVAE":
        return None

    decoder = vae_config.get("decoder") or {}
    logger.debug(
        "detected LTX CausalDiffusionVAE: latent=%s decoder=%s steps=%s output=%s",
        decoder.get("in_channels"), decoder.get("_class_name"),
        decoder.get("default_num_inference_steps"), vae_config.get("model_output_type"),
    )
    return vae_config


def _parse_embedded_config(metadata: dict[str, str]) -> dict | None:
    raw = metadata.get("config")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def detect_ltx_latent_upsampler_config(metadata: dict[str, str]) -> dict | None:
    """Return the embedded ``LatentUpsampler`` config from an LTX-2.3 spatial
    latent-upscaler checkpoint's safetensors metadata, else ``None``. Unlike
    the video/audio VAE configs (nested under ``config["vae"]``/
    ``config["audio_vae"]``), this standalone checkpoint's top-level
    ``__metadata__["config"]`` dict IS the ``LatentUpsampler`` config
    directly -- see module docstring #5.
    """
    config = _parse_embedded_config(metadata)
    if config is None:
        return None
    if config.get("_class_name") != "LatentUpsampler":
        return None

    logger.debug(
        "detected LTX LatentUpsampler: in_channels=%s spatial_scale=%s rational_resampler=%s",
        config.get("in_channels"), config.get("spatial_scale"), config.get("rational_resampler"),
    )
    return config


def detect_ltx_duration_head_config(
    sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None
) -> dict | None:
    """Return an ``LTXDurationHead.from_config`` config for a duration-head
    state dict, else ``None`` -- see module docstring #10.

    Shape-derived, because the checkpoint carries no hyperparameters.
    ``num_pooler_heads`` is the one value shapes cannot recover (``to_q`` is
    square whatever the head count), so it takes the trained value unless an
    embedded config overrides it -- the same fallback diffusers' converter
    makes.
    """
    prefix = _LTX_DURATION_HEAD_PREFIX if f"{_LTX_DURATION_HEAD_PREFIX}{_LTX_DURATION_QUERY_TOKENS}" in sd else ""
    query_tokens = sd.get(f"{prefix}{_LTX_DURATION_QUERY_TOKENS}")
    if query_tokens is None:
        return None

    video_proj = sd.get(f"{prefix}video_input_proj.weight")
    audio_proj = sd.get(f"{prefix}audio_input_proj.weight")
    mlp_hidden = sd.get(f"{prefix}mlp_hidden.weight")
    if video_proj is None or audio_proj is None or mlp_hidden is None:
        return None

    num_pooler_heads = _LTX_DURATION_POOLER_HEADS
    embedded = _parse_embedded_config(metadata or {}) or {}
    if isinstance(embedded, dict) and "num_pooler_heads" in embedded:
        num_pooler_heads = int(embedded["num_pooler_heads"])

    config = {
        "video_cross_attention_dim": int(video_proj.shape[1]),
        "audio_cross_attention_dim": int(audio_proj.shape[1]),
        "pooler_hidden_dim": int(query_tokens.shape[1]),
        "num_queries": int(query_tokens.shape[0]),
        "num_pooler_heads": num_pooler_heads,
        "mlp_hidden_dim": int(mlp_hidden.shape[0]),
    }
    logger.debug(
        "detected LTX duration head: video_dim=%d audio_dim=%d pooler=%d num_queries=%d",
        config["video_cross_attention_dim"], config["audio_cross_attention_dim"],
        config["pooler_hidden_dim"], config["num_queries"],
    )
    return config


def detect_ltx_audio_vae_config(metadata: dict[str, str]) -> dict | None:
    """Return the embedded ``config["audio_vae"]`` dict from an LTX-2/2.3
    checkpoint's safetensors metadata, else ``None``. No ``_class_name``
    marker exists for this family (unlike the video VAE) -- anchored instead
    on ``model.params.ddconfig``/``.encoder`` being present.
    """
    config = _parse_embedded_config(metadata)
    if config is None:
        return None

    audio_config = config.get("audio_vae")
    if not isinstance(audio_config, dict):
        return None
    model_params = audio_config.get("model", {}).get("params", {})
    ddconfig = model_params.get("encoder", model_params.get("ddconfig"))
    if not isinstance(ddconfig, dict):
        return None

    logger.debug(
        "detected LTX audio VAE: z_channels=%s mel_bins=%s causality_axis=%s",
        ddconfig.get("z_channels"), ddconfig.get("mel_bins"), ddconfig.get("causality_axis"),
    )
    return audio_config


def detect_ltx_vocoder_config(metadata: dict[str, str]) -> dict | None:
    """Return the embedded ``config["vocoder"]`` dict from an LTX-2/2.3
    checkpoint's safetensors metadata, else ``None``. Returns whatever shape
    is embedded (flat LTX2 HiFi-GAN or nested LTX23 AMP1/bwe) -- callers
    (``LTXVocoder.from_config``) are responsible for rejecting unsupported
    shapes with a precise error; this function only answers "is a vocoder
    config present at all".
    """
    config = _parse_embedded_config(metadata)
    if config is None:
        return None

    vocoder_config = config.get("vocoder")
    if not isinstance(vocoder_config, dict):
        return None

    logger.debug("detected LTX vocoder config: keys=%s", sorted(vocoder_config.keys()))
    return vocoder_config


def detect_causal3d_vae_config(sd: dict[str, torch.Tensor]) -> dict | None:
    """Return a config dict for a Wan-2.1-shaped causal 3D VAE state dict
    (Qwen-Image's checkpoint, and Krea-2's -- see module docstring), else
    ``None``. The Wan 2.2 variant (nested ``upsamples.0.upsamples.0.*`` keys)
    is deliberately excluded -- not handled by this detector yet.
    """
    if "decoder.middle.0.residual.0.gamma" not in sd:
        return None
    if "decoder.upsamples.0.upsamples.0.residual.2.weight" in sd:
        return None  # Wan 2.2 shape (ComfyUI's own signature key); not this detector's job (yet).

    in_channels = int(sd["encoder.conv1.weight"].shape[1]) if "encoder.conv1.weight" in sd else 3
    out_channels = int(sd["decoder.head.2.weight"].shape[0]) if "decoder.head.2.weight" in sd else 3
    # z_dim from conv2 (the post-sampling 1x1x1 conv the decoder path starts from).
    latent_channels = int(sd["conv2.weight"].shape[0]) if "conv2.weight" in sd else 16

    config = {
        "vae_type": "qwen_image",
        "latent_channels": latent_channels,
        "in_channels": in_channels,
        "out_channels": out_channels,
    }
    logger.debug(
        "detected qwen_image (Wan-2.1-shaped causal 3D) VAE: latent=%d in=%d out=%d",
        latent_channels, in_channels, out_channels,
    )
    return config


def detect_causal3d_v2_vae_config(sd: dict[str, torch.Tensor]) -> dict | None:
    """Return a config dict for a Wan-2.2-shaped causal 3D VAE state dict
    (``vae/causal_3d_v2.py``), else ``None``. Signature: the same bottleneck
    ``decoder.middle.0.residual.0.gamma`` marker as Wan 2.1, PLUS the nested
    ``decoder.upsamples.0.upsamples.0.residual.2.weight`` key that only the
    2.2 shape's ``Up_ResidualBlock``-inside-``Sequential`` layout produces.
    """
    if "decoder.middle.0.residual.0.gamma" not in sd:
        return None
    if "decoder.upsamples.0.upsamples.0.residual.2.weight" not in sd:
        return None

    # encoder.conv1/decoder.head.2 operate on patchified (image_ch * patch_size**2)
    # channels, not raw image channels (patch_size=2 -> divide by 4).
    patchified_in = int(sd["encoder.conv1.weight"].shape[1]) if "encoder.conv1.weight" in sd else 12
    patchified_out = int(sd["decoder.head.2.weight"].shape[0]) if "decoder.head.2.weight" in sd else 12
    in_channels = patchified_in // 4
    out_channels = patchified_out // 4
    latent_channels = int(sd["conv2.weight"].shape[0]) if "conv2.weight" in sd else 48

    config = {
        "vae_type": "wan2.2",
        "latent_channels": latent_channels,
        "in_channels": in_channels,
        "out_channels": out_channels,
    }
    logger.debug(
        "detected wan2.2 (causal 3D, patchified) VAE: latent=%d in=%d out=%d",
        latent_channels, in_channels, out_channels,
    )
    return config


def detect_seedvr2_vae_config(sd: dict[str, torch.Tensor]) -> dict | None:
    """Return a config dict for a SeedVR2 causal-video VAE state dict (an
    already-inflated diffusers ``AutoencoderKL`` — see
    ``vae/seedvr2_causal_video.py``), else ``None``.

    Signature: a **5D** ``encoder.conv_in.weight`` (``[128,3,3,3,3]`` — the
    inflated 3D conv) with a matching ``decoder.conv_out.weight``, distinguished
    from the neighbouring families it shares key names with:

      * the Flux 2D AE (``detect_vae_config``) has a **4D** ``encoder.conv_in``;
      * this checkpoint has **no** ``quant_conv``/``post_quant_conv`` (``use_quant_conv
        False``), unlike the flux2 diffusers AE;
      * the Wan-shaped causal-3D VAEs key on ``encoder.conv1`` +
        ``decoder.middle.0.residual.0.gamma`` (absent here);
      * the LTX video VAE nests its conv weights under ``encoder.conv_in.conv.*``
        and is metadata-detected (this file carries no embedded metadata).

    Config is minimal — ``SeedVR2CausalVideoVAE.from_config`` fills the fixed
    ``s8_c16_t4`` architecture (block_out_channels, layers_per_block, …) from its
    own defaults; only the shape-derivable channel counts are passed through.
    """
    conv_in = sd.get("encoder.conv_in.weight")
    if conv_in is None or conv_in.ndim != 5:
        return None
    if "decoder.conv_out.weight" not in sd or "decoder.conv_in.weight" not in sd:
        return None
    if "quant_conv.weight" in sd or "post_quant_conv.weight" in sd:
        return None
    if "decoder.middle.0.residual.0.gamma" in sd:
        return None  # Wan-shaped causal 3D (different key layout), not this family.

    config = {
        "vae_type": "seedvr2",
        "latent_channels": int(sd["decoder.conv_in.weight"].shape[1]),
        "in_channels": int(conv_in.shape[1]),
        "out_channels": int(sd["decoder.conv_out.weight"].shape[0]),
    }
    logger.debug(
        "detected seedvr2 causal-video VAE: latent=%d in=%d out=%d",
        config["latent_channels"], config["in_channels"], config["out_channels"],
    )
    return config


def detect_minimax_h3_video_vae_config(
    sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None,
) -> dict | None:
    """Return a config dict for a MiniMax-H3 video VAE state dict (the
    Comfy-Org repack -- see ``vae/minimax_h3_video.py``), else ``None``.
    """
    if "decoder.mask_token" not in sd or "decoder.register_tokens" not in sd:
        return None
    conv_in = sd.get("encoder.conv_in.weight")
    if conv_in is None or conv_in.ndim != 5:
        return None

    latent_channels = (
        int(sd["post_quant_conv.weight"].shape[0]) if "post_quant_conv.weight" in sd else _H3_VIDEO_LATENT_CHANNELS
    )
    decoder_dim = int(sd["decoder.x_embedder.weight"].shape[0]) if "decoder.x_embedder.weight" in sd else None
    decoder_num_layers = len({
        k.split(".")[2] for k in sd if k.startswith("decoder.transformer_blocks.")
    }) or _H3_VIDEO_DECODER_NUM_LAYERS
    decoder_num_attention_heads = _H3_VIDEO_DECODER_NUM_ATTENTION_HEADS
    decoder_attention_head_dim = _H3_VIDEO_DECODER_ATTENTION_HEAD_DIM
    if decoder_dim is not None and decoder_dim != decoder_num_attention_heads * decoder_attention_head_dim:
        # Shape drift from the one known variant -- keep the head count fixed
        # (not derivable from a single dim value) and recompute head_dim.
        decoder_attention_head_dim = decoder_dim // decoder_num_attention_heads

    clip_length = _H3_VIDEO_CLIP_LENGTH
    token_drop = _H3_VIDEO_TOKEN_DROP
    embedded = metadata.get("minimax_h3_video_vae") if metadata else None
    if embedded:
        try:
            embedded_config = json.loads(embedded)
        except (json.JSONDecodeError, TypeError):
            embedded_config = {}
        clip_length = int(embedded_config.get("vae_clip_length", clip_length))
        token_drop = int(embedded_config.get("vae_token_drop", token_drop))

    config = {
        "latent_channels": latent_channels,
        "in_channels": 3,
        "out_channels": 3,
        "block_out_channels": _H3_VIDEO_BLOCK_OUT_CHANNELS,
        "layers_per_block": _H3_VIDEO_LAYERS_PER_BLOCK,
        "spatial_downsample_factors": _H3_VIDEO_SPATIAL_DOWNSAMPLE_FACTORS,
        "temporal_downsample_factors": _H3_VIDEO_TEMPORAL_DOWNSAMPLE_FACTORS,
        "decoder_num_layers": decoder_num_layers,
        "decoder_num_attention_heads": decoder_num_attention_heads,
        "decoder_attention_head_dim": decoder_attention_head_dim,
        "decoder_num_register_tokens": _H3_VIDEO_DECODER_NUM_REGISTER_TOKENS,
        "decoder_ffn_mult": _H3_VIDEO_DECODER_FFN_MULT,
        "decoder_rope_theta": _H3_VIDEO_DECODER_ROPE_THETA,
        "decoder_rope_dim_ratio": _H3_VIDEO_DECODER_ROPE_DIM_RATIO,
        "decoder_norm_eps": _H3_VIDEO_DECODER_NORM_EPS,
        "clip_length": clip_length,
        "token_drop": token_drop,
    }
    logger.debug(
        "detected minimax_h3 video VAE: latent=%d decoder_layers=%d clip_length=%d token_drop=%d",
        latent_channels, decoder_num_layers, clip_length, token_drop,
    )
    return config


def detect_minimax_h3_audio_vae_config(
    sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None,
) -> dict | None:
    """Return a config dict for a MiniMax-H3 audio VAE state dict (the
    Comfy-Org repack -- see ``vae/minimax_h3_audio.py``), else ``None``.
    """
    if "pre_block.attn.qkv.weight" not in sd:
        return None
    if "dec_in_proj.weight" not in sd or "decoder.conv_pre.weight" not in sd:
        return None

    latent_channels = int(sd["mean_proj.weight"].shape[0]) if "mean_proj.weight" in sd else _H3_AUDIO_LATENT_CHANNELS
    latent_dim = int(sd["dec_in_proj.weight"].shape[0]) if "dec_in_proj.weight" in sd else _H3_AUDIO_LATENT_DIM
    encoder_dim = int(sd["encoder.block.0.weight"].shape[0]) if "encoder.block.0.weight" in sd else _H3_AUDIO_ENCODER_DIM

    sample_rate = _H3_AUDIO_SAMPLE_RATE
    embedded = metadata.get("minimax_h3_audio_vae") if metadata else None
    if embedded:
        try:
            embedded_config = json.loads(embedded)
        except (json.JSONDecodeError, TypeError):
            embedded_config = {}
        sample_rate = int(embedded_config.get("sample_rate", sample_rate))

    config = {
        "encoder_dim": encoder_dim,
        "encoder_rates": _H3_AUDIO_ENCODER_RATES,
        "latent_dim": latent_dim,
        "latent_channels": latent_channels,
        "num_attention_heads": _H3_AUDIO_NUM_ATTENTION_HEADS,
        "decoder_dim": _H3_AUDIO_DECODER_DIM,
        "decoder_rates": _H3_AUDIO_DECODER_RATES,
        "decoder_kernel_sizes": _H3_AUDIO_DECODER_KERNEL_SIZES,
        "resblock_kernel_sizes": _H3_AUDIO_RESBLOCK_KERNEL_SIZES,
        "resblock_dilation_sizes": _H3_AUDIO_RESBLOCK_DILATION_SIZES,
        "sample_rate": sample_rate,
    }
    logger.debug(
        "detected minimax_h3 audio VAE: latent_channels=%d latent_dim=%d sample_rate=%d",
        latent_channels, latent_dim, sample_rate,
    )
    return config


def detect_minimax_music3_dav_config(
    sd: dict[str, torch.Tensor], metadata: dict[str, str] | None = None,
) -> dict | None:
    """Return a config dict for a MiniMax-Music3 DAV state dict (the
    Comfy-Org repack, RAW -- i.e. still carrying `weight_g`/`weight_v`,
    before `fold_weight_norm_conv` runs), else `None`.
    """
    if "dec_in_proj.weight" not in sd:
        return None
    if "decoder.model.0.weight_v" not in sd or "decoder.model.6.weight_v" not in sd:
        return None

    dec_in_proj_weight = sd["dec_in_proj.weight"]
    latent_channels = int(dec_in_proj_weight.shape[1]) * 2
    decoder_input_dim = int(dec_in_proj_weight.shape[0])
    decoder_hidden_dim = int(sd["decoder.model.0.weight_v"].shape[0])

    config = {
        "latent_channels": latent_channels,
        "decoder_input_dim": decoder_input_dim,
        "decoder_hidden_dim": decoder_hidden_dim,
        "upsampling_ratios": (8, 8, 4, 2),  # not shape-derived -- single released variant
        "sample_rate": 44100,
    }
    logger.debug(
        "detected minimax_music3 DAV vocoder: latent_channels=%d decoder_hidden_dim=%d",
        latent_channels, decoder_hidden_dim,
    )
    return config
