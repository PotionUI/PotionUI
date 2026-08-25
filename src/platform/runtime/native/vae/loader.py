"""``load_vae`` entry point: file -> detected config -> loaded ``AutoEncoder2D``.

Key-layout handling: ``flux2-vae.safetensors`` is diffusers-keyed
(``decoder.mid_block.attentions.0.*`` etc.) while ``AutoEncoder2D`` is built in
ldm layout (matching ``ae.sft`` natively). Rather than building a second arch
variant for the diffusers layout, the diffusers-style keys are renamed to ldm
layout before loading -- via ``convert_vae_state_dict``, vendored verbatim
from ComfyUI's ``comfy/diffusers_convert.py``. This is not a guess: ComfyUI's
own ``VAE.__init__`` takes exactly this path for this checkpoint, since it
contains the signature key ``decoder.up_blocks.0.resnets.0.norm1.weight`` that
triggers diffusers conversion in ComfyUI too. The result is **exact key-set
parity** against the single ldm-layout module -- verified in
``tests/core/native/vae/test_ae_2d.py`` (real-file load, zero missing/unexpected
keys beyond the quant-sidecar allowlist).

Top-level keys (``bn.*``, ``quant_conv.*``, ``post_quant_conv.*``) are already
flat/ldm-shaped in both checkpoints and need no renaming.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

import torch

from ..base import load_into_module
from ..detect.vae_detect import (
    detect_causal3d_v2_vae_config,
    detect_causal3d_vae_config,
    detect_ltx_audio_vae_config,
    detect_ltx_diffusion_vae_config,
    detect_ltx_latent_upsampler_config,
    detect_ltx_video_vae_config,
    detect_ltx_vocoder_config,
    detect_minimax_h3_audio_vae_config,
    detect_minimax_h3_latent_upsampler_config,
    detect_minimax_h3_video_vae_config,
    detect_minimax_music3_dav_config,
    detect_seedvr2_vae_config,
    detect_vae_config,
)
from ..errors import NativeEngineUnsupportedError
from ..io.safetensors_loader import load_torch_file
from ..io.state_dict_utils import strip_prefix
from .ae_2d import AutoEncoder2D
from .causal_3d import AutoEncoderCausal3D
from .causal_3d_v2 import AutoEncoderCausal3D_2_2
from .key_convert import convert_vae_state_dict
from .ltx_audio import LTXAudioAutoencoder, LTXVocoder, LTXVocoderAMP
from .ltx_causal_video import LTXCausalVideoVAE
from .ltx_diffusion_video import LTXDiffusionVideoVAE
from .ltx_latent_upsampler import LTXLatentUpsampler
from .minimax_h3_audio import MiniMaxH3AudioVAE
from .minimax_h3_latent_upsampler import MiniMaxH3LatentUpsampler
from .minimax_h3_video import MiniMaxH3VideoVAE
from .minimax_music3_dav import MiniMaxMusic3DAV, fold_weight_norm_conv
from .seedvr2_causal_video import SeedVR2CausalVideoVAE

logger = logging.getLogger(__name__)


class _VaeSpec:
    """Minimal duck-typed ``ModelSpec`` for ``load_into_module``'s allowlist gate.

    The VAE isn't in ``detect/registry.py`` (that registry binds *DiT* specs);
    it needs its own tiny allowlist because ``quant_conv``/``post_quant_conv``/
    ``bn`` are conditionally present per variant and ``load_state_dict`` must
    not fail when they're legitimately absent (flux_ae has none of the three).
    ``expected_missing`` is a set of fnmatch globs for keys some real
    checkpoints legitimately omit (e.g. LTX's diagnostic-only
    ``per_channel_statistics`` buffers -- verified some checkpoints ship only
    ``mean-of-means``/``std-of-means``, the two actually read by normalize/
    un_normalize, and drop the rest).
    """

    def __init__(self, family: str, variant: str, *, expected_missing: set[str] = frozenset()) -> None:
        self.family = family
        self.variant = variant
        self._expected_missing = expected_missing

    def key_is_expected_missing(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self._expected_missing)

    def key_is_expected_unexpected(self, key: str) -> bool:
        return False


def load_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
) -> AutoEncoder2D:
    """Load a Flux-family 2D VAE from a single safetensors file.

    Detects the variant (``flux_ae``/``flux2_ae``), renames diffusers-layout
    keys to ldm layout when needed, builds the module via ``operations``, and
    runs it through the standard load-integrity gate (missing/unexpected key
    allowlist, ``post_load``, meta/NaN sanity).
    """
    path = Path(path)
    sd, _metadata = load_torch_file(path, device=device)

    config = detect_vae_config(sd)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a Flux-family 2D VAE "
            "(missing encoder.conv_in.weight / decoder.conv_out.weight)"
        )

    if config["key_layout"] == "diffusers":
        sd = convert_vae_state_dict(sd)

    module = AutoEncoder2D.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant=config["vae_type"])
    load_into_module(module, sd, spec)

    logger.debug("loaded %s VAE from %s (latent_channels=%d)", config["vae_type"], path.name, config["latent_channels"])
    return module


def load_causal3d_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
) -> AutoEncoderCausal3D:
    """Load the Wan-2.1-shaped causal 3D VAE (Qwen-Image's/Krea-2's checkpoint --
    see ``vae/causal_3d.py``) from a single safetensors file. Unlike
    :func:`load_vae`, no key renaming is needed: the checkpoint's key layout
    matches the module's own naming exactly (see ``causal_3d.py``'s "Key
    parity" note for how that's arranged).
    """
    path = Path(path)
    sd, _metadata = load_torch_file(path, device=device)

    config = detect_causal3d_vae_config(sd)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a Wan-2.1-shaped causal 3D VAE "
            "(missing decoder.middle.0.residual.0.gamma, or it's the Wan 2.2 shape)"
        )

    module = AutoEncoderCausal3D.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant=config["vae_type"])
    load_into_module(module, sd, spec)

    logger.debug("loaded %s causal-3D VAE from %s (latent_channels=%d)", config["vae_type"], path.name, config["latent_channels"])
    return module


def load_ltx_video_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXCausalVideoVAE:
    """Load an LTX-2/2.3 ``CausalVideoAutoencoder`` from either a standalone
    VAE file (``LTX2_video_vae_bf16.safetensors``, no prefix) or the ``vae.*``
    slice of an all-in-one checkpoint (auto-detected: tries the bare keys
    first, falls back to stripping a ``vae.`` prefix). Config comes from the
    checkpoint's own embedded metadata, not shape sniffing (see
    ``detect_ltx_video_vae_config``).

    ``sd``/``metadata`` let a caller that already read the file (e.g. the
    engine's ``_load_vae``, which slices the all-in-one checkpoint down to its
    ``vae.*`` keys before this is called) pass them straight through instead
    of paying for a second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_video_vae_config(metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' has no embedded CausalVideoAutoencoder config "
            "(__metadata__['config']['vae'])"
        )

    if "encoder.conv_in.conv.weight" not in sd:
        sd = strip_prefix(sd, "vae.")
    if "encoder.conv_in.conv.weight" not in sd:
        raise NativeEngineUnsupportedError(
            f"'{path.name}': found an embedded VAE config but no matching "
            "encoder.conv_in.conv.weight key (bare or 'vae.'-prefixed)"
        )

    module = LTXCausalVideoVAE.from_config(config, operations)
    spec = _VaeSpec(
        family="vae", variant="ltx_causal_video",
        # Diagnostic-only per_channel_statistics buffers: some real checkpoints
        # (verified: LTX23_video_vae_bf16.safetensors) only ship the two
        # buffers actually read by normalize/un_normalize.
        expected_missing={
            "per_channel_statistics.mean-of-stds",
            "per_channel_statistics.mean-of-stds_over_std-of-means",
            "per_channel_statistics.channel",
        },
    )
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded LTX CausalVideoAutoencoder from %s (latent_channels=%d)",
        path.name, config["latent_channels"],
    )
    return module


def load_ltx_diffusion_video_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXDiffusionVideoVAE:
    """Load an LTX-2.5 ``CausalDiffusionVAE`` -- the 2.3-shaped conv encoder
    plus the denoising ``NADiffusionDecoder`` (see
    ``vae/ltx_diffusion_video.py``). Same standalone-or-``vae.``-prefixed key
    handling and same ``per_channel_statistics`` allowlist as
    :func:`load_ltx_video_vae`; the config likewise comes from the checkpoint's
    own embedded metadata.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_diffusion_vae_config(metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' has no embedded CausalDiffusionVAE config "
            "(__metadata__['config']['vae'])"
        )

    if "encoder.conv_in.conv.weight" not in sd:
        sd = strip_prefix(sd, "vae.")
    if "encoder.conv_in.conv.weight" not in sd:
        raise NativeEngineUnsupportedError(
            f"'{path.name}': found an embedded CausalDiffusionVAE config but no "
            "matching encoder.conv_in.conv.weight key (bare or 'vae.'-prefixed)"
        )

    module = LTXDiffusionVideoVAE.from_config(config, operations)
    spec = _VaeSpec(
        family="vae", variant="ltx_diffusion_video",
        expected_missing={
            "per_channel_statistics.mean-of-stds",
            "per_channel_statistics.mean-of-stds_over_std-of-means",
            "per_channel_statistics.channel",
        },
    )
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded LTX CausalDiffusionVAE from %s (latent_channels=%d, steps=%d)",
        path.name, module.latent_channels, module.decoder.default_num_inference_steps,
    )
    return module


def load_ltx_audio_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXAudioAutoencoder:
    """Load an LTX-2/2.3 audio VAE (``LTXAudioAutoencoder``, decode-only) from
    either a standalone file (``LTX2_audio_vae_bf16.safetensors``) or an
    all-in-one checkpoint -- both use an ``audio_vae.`` key prefix (unlike the
    video VAE, the standalone audio file is ALSO prefixed, verified via header
    dump), so the prefix is stripped unconditionally.

    ``sd``/``metadata`` let a caller that already read the file (e.g. the
    engine's ``_load_audio_vae``, which slices the all-in-one checkpoint down
    to its ``audio_vae.*`` keys before this is called) pass them straight
    through instead of paying for a second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_audio_vae_config(metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' has no embedded LTX audio VAE config (__metadata__['config']['audio_vae'])"
        )

    sd = strip_prefix(sd, "audio_vae.")
    if "decoder.conv_in.conv.weight" not in sd:
        raise NativeEngineUnsupportedError(
            f"'{path.name}': found an embedded audio VAE config but no matching "
            "decoder.conv_in.conv.weight key after stripping the 'audio_vae.' prefix"
        )

    module = LTXAudioAutoencoder.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="ltx_audio")
    load_into_module(module, sd, spec)

    logger.debug("loaded LTX audio VAE from %s (mel_bins=%d)", path.name, module.mel_bins)
    return module


def load_ltx_vocoder(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXVocoder | LTXVocoderAMP:
    """Load an LTX-2/2.3 vocoder from either a standalone audio VAE file or an
    all-in-one checkpoint, both ``vocoder.``-prefixed. Dispatches on the
    embedded config shape: LTX2's flat HiFi-GAN shape builds ``LTXVocoder``;
    LTX23's nested ``{'vocoder': {...}, 'bwe': {...}}`` AMP1/SnakeBeta shape
    builds ``LTXVocoderAMP`` (main stage only -- see ``vae/ltx_audio.py``).

    ``sd``/``metadata`` let a caller that already read the file (e.g. the
    engine's ``_load_vocoder``, which slices the all-in-one checkpoint down to
    its ``vocoder.*`` keys before this is called) pass them straight through
    instead of paying for a second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_vocoder_config(metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' has no embedded LTX vocoder config (__metadata__['config']['vocoder'])"
        )

    # Build the module first (before touching the state dict) so an
    # unsupported shape raises a precise error rather than a generic
    # "key not found" one.
    is_amp = "upsample_rates" not in config
    module = LTXVocoderAMP.from_config(config, operations) if is_amp else LTXVocoder.from_config(config, operations)

    sd = strip_prefix(sd, "vocoder.")
    probe_key = "vocoder.conv_pre.weight" if is_amp else "conv_pre.weight"
    if probe_key not in sd:
        raise NativeEngineUnsupportedError(
            f"'{path.name}': found an embedded vocoder config but no matching "
            f"{probe_key} key after stripping the 'vocoder.' prefix"
        )

    spec = _VaeSpec(family="vae", variant="ltx_vocoder_amp" if is_amp else "ltx_vocoder")
    load_into_module(module, sd, spec)

    if is_amp:
        logger.debug("loaded LTX vocoder (AMP1/SnakeBeta) from %s (main stage only)", path.name)
    else:
        logger.debug("loaded LTX vocoder from %s (upsample_factor=%d)", path.name, module.upsample_factor)
    return module


def load_ltx_latent_upsampler(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> LTXLatentUpsampler:
    """Load an LTX-2.3 spatial latent upsampler from its own
    standalone checkpoint (e.g. ``ltx-2.3-spatial-upscaler-x1.5-1.0.safetensors``
    / the x2 variant) -- NOT a slice of the all-in-one DiT checkpoint (see
    ``vae/ltx_latent_upsampler.py``'s module docstring for why). Config comes
    from the checkpoint's own embedded metadata (flat, top-level -- see
    ``detect_ltx_latent_upsampler_config``), same pattern as the other LTX
    components.

    ``sd``/``metadata`` let a caller that already read the file pass them
    straight through instead of paying for a second full-checkpoint read (this
    checkpoint is small -- a few hundred MB -- so that's a minor optimisation
    here, unlike the all-in-one checkpoint's other components).
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_ltx_latent_upsampler_config(metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' has no embedded LatentUpsampler config "
            "(__metadata__['config']['_class_name'] != 'LatentUpsampler')"
        )

    if "post_upsample_res_blocks.0.conv2.bias" not in sd:
        raise NativeEngineUnsupportedError(
            f"'{path.name}': found an embedded LatentUpsampler config but no matching "
            "post_upsample_res_blocks.0.conv2.bias key"
        )

    module = LTXLatentUpsampler.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="ltx_latent_upsampler")
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded LTX latent upsampler from %s (spatial_scale=%s, rational_resampler=%s)",
        path.name, module.spatial_scale, module.rational_resampler,
    )
    return module


def load_minimax_h3_latent_upsampler(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> MiniMaxH3LatentUpsampler:
    """Load the MiniMax-H3 3D latent upsampler from its standalone checkpoint
    (bare/unprefixed keys, no embedded ``__metadata__`` -- see
    ``vae/minimax_h3_latent_upsampler.py``'s module docstring), shape-detected
    the same way as the other MiniMax-H3 components.

    ``sd``/``metadata`` let a caller that already read the file pass them
    straight through instead of paying for a second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_minimax_h3_latent_upsampler_config(sd)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a MiniMax-H3 latent upsampler "
            "(missing conv_in.weight / embed.0.weight / norm_out.weight)"
        )

    module = MiniMaxH3LatentUpsampler.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="minimax_h3_latent_upsampler")
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded MiniMax-H3 latent upsampler from %s (channels=%d, num_res_blocks=%d)",
        path.name, config["channels"], config["num_res_blocks"],
    )
    return module


def load_causal3d_v2_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
) -> AutoEncoderCausal3D_2_2:
    """Load the Wan-2.2-shaped causal 3D VAE (48ch, patchified -- see
    ``vae/causal_3d_v2.py``) from a single safetensors file. Same no-rename
    key parity as :func:`load_causal3d_vae`.
    """
    path = Path(path)
    sd, _metadata = load_torch_file(path, device=device)

    config = detect_causal3d_v2_vae_config(sd)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a Wan-2.2-shaped causal 3D VAE "
            "(missing decoder.middle.0.residual.0.gamma + the nested "
            "decoder.upsamples.0.upsamples.0.* signature)"
        )

    module = AutoEncoderCausal3D_2_2.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant=config["vae_type"])
    load_into_module(module, sd, spec)

    logger.debug("loaded %s causal-3D VAE from %s (latent_channels=%d)", config["vae_type"], path.name, config["latent_channels"])
    return module


def load_seedvr2_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
) -> SeedVR2CausalVideoVAE:
    """Load the SeedVR2 causal-video VAE (``vae/seedvr2_causal_video.py``) from a
    single safetensors file. Like :func:`load_causal3d_vae`, no key renaming is
    needed: the checkpoint stores the already-inflated 5D conv weights at the
    exact module paths (diffusers ``AutoencoderKL`` layout, verbatim). The
    module carries no computed buffers, and its fixed latent scaling (``0.9152``)
    lives inside ``encode``/``decode`` — the engine must not layer the wan21
    per-channel transform on top (see ``NativeGenerator._is_self_normalizing_vae``).
    """
    path = Path(path)
    sd, _metadata = load_torch_file(path, device=device)

    config = detect_seedvr2_vae_config(sd)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a SeedVR2 causal-video VAE "
            "(missing a 5D encoder.conv_in.weight / decoder.conv_out.weight, or it "
            "carries quant_conv / the Wan bottleneck gamma)"
        )

    module = SeedVR2CausalVideoVAE.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant=config["vae_type"])
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded seedvr2 causal-video VAE from %s (latent_channels=%d)",
        path.name, config["latent_channels"],
    )
    return module


def load_minimax_h3_video_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> MiniMaxH3VideoVAE:
    """Load the MiniMax-H3 video VAE (causal-3D-conv encoder + ViT decoder --
    see ``vae/minimax_h3_video.py``) from its standalone Comfy-Org repack file
    (``minimax_h3_video_vae_fp16.safetensors``, bare/unprefixed keys).

    ``sd``/``metadata`` let a caller that already read the file (the engine's
    ``_load_vae``, which reads once for all VAE-family dispatch) pass them
    straight through instead of paying for a second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_minimax_h3_video_vae_config(sd, metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a MiniMax-H3 video VAE "
            "(missing decoder.mask_token / decoder.register_tokens, or "
            "encoder.conv_in.weight isn't a 5D causal-3D conv)"
        )

    module = MiniMaxH3VideoVAE.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="minimax_h3_video")
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded MiniMax-H3 video VAE from %s (latent_channels=%d, decoder_layers=%d)",
        path.name, config["latent_channels"], config["decoder_num_layers"],
    )
    return module


def load_minimax_h3_audio_vae(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> MiniMaxH3AudioVAE:
    """Load the MiniMax-H3 audio VAE (DAC encoder + BigVGAN decoder -- see
    ``vae/minimax_h3_audio.py``) from its standalone Comfy-Org repack file
    (``minimax_h3_audio_vae_fp32.safetensors``, bare/unprefixed keys).

    ``sd``/``metadata`` let a caller that already read the file (the engine's
    ``_load_audio_vae``) pass them straight through instead of paying for a
    second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_minimax_h3_audio_vae_config(sd, metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a MiniMax-H3 audio VAE "
            "(missing pre_block.attn.qkv.weight / dec_in_proj.weight / "
            "decoder.conv_pre.weight)"
        )

    module = MiniMaxH3AudioVAE.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="minimax_h3_audio")
    load_into_module(module, sd, spec)

    logger.debug(
        "loaded MiniMax-H3 audio VAE from %s (latent_channels=%d, sample_rate=%d)",
        path.name, config["latent_channels"], config["sample_rate"],
    )
    return module


def load_minimax_music3_dav(
    path: str | Path,
    operations: Any,
    device: str | torch.device = "cpu",
    sd: dict[str, torch.Tensor] | None = None,
    metadata: dict[str, str] | None = None,
) -> MiniMaxMusic3DAV:
    """Load the MiniMax-Music3 DAV vocoder (decode-only DAC decoder -- see
    ``vae/minimax_music3_dav.py``) from its standalone Comfy-Org repack file
    (``minimax_music3_dav.safetensors``, bare/unprefixed keys).

    Detection reads the RAW state dict (still carrying `weight_g`/`weight_v`);
    :func:`fold_weight_norm_conv` then folds every such pair into a plain
    `weight` tensor -- once, here, before the state dict reaches
    ``load_into_module`` -- so the module itself never sees a weight_norm key
    (see ``minimax_music3_dav.py``'s module docstring for why this repack
    needs the fold at all, unlike the H3 audio sibling above).

    ``sd``/``metadata`` let a caller that already read the file (the engine's
    ``_load_audio_vae``) pass them straight through instead of paying for a
    second full-checkpoint read.
    """
    path = Path(path)
    if sd is None or metadata is None:
        sd, metadata = load_torch_file(path, device=device)

    config = detect_minimax_music3_dav_config(sd, metadata)
    if config is None:
        raise NativeEngineUnsupportedError(
            f"'{path.name}' does not look like a MiniMax-Music3 DAV vocoder "
            "(missing dec_in_proj.weight / decoder.model.0.weight_v / "
            "decoder.model.6.weight_v)"
        )

    module = MiniMaxMusic3DAV.from_config(config, operations)
    spec = _VaeSpec(family="vae", variant="minimax_music3_dav")
    load_into_module(module, fold_weight_norm_conv(sd), spec)

    logger.debug(
        "loaded MiniMax-Music3 DAV vocoder from %s (latent_channels=%d, sample_rate=%d)",
        path.name, config["latent_channels"], config["sample_rate"],
    )
    return module
