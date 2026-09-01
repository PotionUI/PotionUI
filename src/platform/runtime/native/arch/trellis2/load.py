"""Build every TRELLIS.2 component from the Comfy-Org depot files.

The family does not fit the one-file-one-model shape the generic native loader
(``engine.NativeEngineLoader._load_dit``) assumes: the diffusion file holds FOUR
flow DiTs under four prefixes, and the shape VAE file holds two decoders. So the
prefixed reads and the model construction live here, and the model_loader pipe
calls these functions directly.

Every loader reads only the tensors under its own prefix
(``load_torch_file_prefixed``), so building the texture decoder never
materialises the multi-GB flow bundle.

Weights keep the checkpoint's native dtype (bf16 for every released file) unless
``dtype`` is passed. Nothing here moves a model to the GPU — placement is the
caller's decision.
"""

from __future__ import annotations

import logging
from pathlib import Path

import torch
import torch.nn as nn

from vendor.gpl.comfyui.ops import pick_operations

from ...io.safetensors_loader import load_torch_file_prefixed
from ...ops.dtype import is_mixed_precision
from .config import (
    OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    SHAPE_SLAT_FLOW_512,
    SHAPE_SLAT_FLOW_1024,
    SS_FLOW_PRODUCTION,
    SS_VAE_DECODER_PRODUCTION,
    TEX_SLAT_FLOW_512,
    TEX_SLAT_FLOW_1024,
    OctreeVaeDecoderConfig,
    SLatFlowConfig,
    SSFlowConfig,
    SSVAEDecoderConfig,
)
from .conditioner import DinoV3ImageConditioner
from .detect import (
    FLOW_PREFIXES,
    SHAPE_DECODER_PREFIX,
    STRUCTURE_DECODER_PREFIX,
    TEXTURE_DECODER_PREFIX,
)
from .octree_vae import FlexiDualGridVaeDecoder, SparseUnetVaeDecoder
from .slat_flow import SLatFlowModel
from .ss_flow import SSFlowDiT
from .ss_vae import SSVAEDecoder

logger = logging.getLogger(__name__)

__all__ = [
    "SHAPE_DECODER_RESOLUTION",
    "load_dino_conditioner",
    "load_shape_slat_decoder",
    "load_shape_slat_flow",
    "load_ss_flow",
    "load_ss_vae_decoder",
    "load_tex_slat_decoder",
    "load_tex_slat_flow",
]

#: The dual grid the shape decoder resolves onto. Not derivable from any tensor
#: shape — the head's output width is the same at every resolution.
SHAPE_DECODER_RESOLUTION = 256

_SHAPE_FLOWS: dict[str, SLatFlowConfig] = {"512": SHAPE_SLAT_FLOW_512, "1024": SHAPE_SLAT_FLOW_1024}
_TEX_FLOWS: dict[str, SLatFlowConfig] = {"512": TEX_SLAT_FLOW_512, "1024": TEX_SLAT_FLOW_1024}


def _read(path: str | Path, prefix: str, dtype: torch.dtype | None) -> dict[str, torch.Tensor]:
    """The ``prefix`` slice of ``path``, prefix stripped, optionally recast.

    ``load_torch_file_prefixed`` falls back to a whole-file read when nothing
    matches, which for this family would mean a wrong-slot file loading its keys
    into the wrong model — so an empty slice is an error here, not a fallback.
    """
    sd, _ = load_torch_file_prefixed(path, prefix, device="cpu")
    sliced = {k[len(prefix):]: v for k, v in sd.items() if k.startswith(prefix)}
    if not sliced:
        available = sorted({k.split(".")[0] for k in sd})[:8]
        raise ValueError(
            f"{Path(path).name} has no weights under '{prefix}' — top-level keys "
            f"present: {available}. This is the wrong file for this component."
        )
    if dtype is not None:
        sliced = {k: v.to(dtype) for k, v in sliced.items()}
    return sliced


def _fill(module: nn.Module, sd: dict[str, torch.Tensor], what: str) -> nn.Module:
    """Assign-load ``sd`` and refuse to return a partly-random model.

    ``strict=True`` is not usable — these checkpoints legitimately omit
    non-persistent buffers — so every *parameter* is checked for a fill instead.
    Without that check a prefix that matched the wrong sub-model would yield a
    randomly-initialised network reporting success, and surface as bad geometry
    several stages later.
    """
    module.requires_grad_(False)
    result = module.load_state_dict(sd, strict=False, assign=True)

    unfilled = sorted({name for name, _ in module.named_parameters()}.intersection(result.missing_keys))
    if unfilled:
        raise ValueError(
            f"{what}: {len(unfilled)} weights left unfilled, first few {unfilled[:5]}. "
            f"The checkpoint layout does not match this component."
        )

    if hasattr(module, "post_load"):
        module.post_load()
    return module.eval()


def _ops_for(sd: dict[str, torch.Tensor], dtype: torch.dtype | None):
    """The ops namespace for a component built from ``sd``.

    Mirrors ``NativeEngineLoader._ops_for`` minus the fp8/streaming tiers, which
    no released TRELLIS.2 file needs: every one is plain bf16.
    """
    from vendor.gpl.comfyui.ops import manual_cast

    if is_mixed_precision(sd):
        return manual_cast
    storage = dtype or next(iter(sd.values())).dtype
    return pick_operations(storage, storage, None)


# -- flow models -----------------------------------------------------------


def load_ss_flow(
    path: str | Path,
    config: SSFlowConfig = SS_FLOW_PRODUCTION,
    *,
    dtype: torch.dtype | None = None,
) -> SSFlowDiT:
    """The sparse-structure flow DiT (``model.structure_model.``): the first
    stage, a dense DiT over a 16^3 voxel grid."""
    sd = _read(path, FLOW_PREFIXES["structure"], dtype)
    with torch.device("meta"):
        module = SSFlowDiT(config, _ops_for(sd, dtype))
    return _fill(module, sd, "sparse-structure flow")


def load_shape_slat_flow(
    path: str | Path,
    tier: str = "1024",
    config: SLatFlowConfig | None = None,
    *,
    dtype: torch.dtype | None = None,
) -> SLatFlowModel:
    """The image-to-shape SLat flow for ``tier`` (``"512"`` or ``"1024"``).

    The two tiers are separate weights in the bundle (``model.img2shape_512.``
    and ``model.img2shape.``) and separate latent resolutions (32 vs 64).
    """
    default = _tier(_SHAPE_FLOWS, tier, "shape")
    config = default if config is None else config
    sd = _read(path, FLOW_PREFIXES[f"shape_{tier}"], dtype)
    return _fill(SLatFlowModel(**config.as_kwargs()), sd, f"shape SLat flow ({tier})")


def load_tex_slat_flow(
    path: str | Path,
    tier: str = "1024",
    config: SLatFlowConfig | None = None,
    *,
    dtype: torch.dtype | None = None,
) -> SLatFlowModel:
    """The shape-to-texture SLat flow for ``tier``.

    Both tiers read the SAME weights (``model.shape2txt.`` — the bundle carries
    one texture flow where upstream ships two) and differ only in the latent
    resolution the config declares.
    """
    default = _tier(_TEX_FLOWS, tier, "texture")
    config = default if config is None else config
    sd = _read(path, FLOW_PREFIXES["texture"], dtype)
    return _fill(SLatFlowModel(**config.as_kwargs()), sd, f"texture SLat flow ({tier})")


def _tier(table: dict[str, SLatFlowConfig], tier: str, what: str) -> SLatFlowConfig:
    try:
        return table[tier]
    except KeyError:
        raise ValueError(
            f"unknown {what} flow tier {tier!r}; expected one of {sorted(table)}"
        ) from None


# -- VAE decoders ----------------------------------------------------------


def load_ss_vae_decoder(
    path: str | Path,
    config: SSVAEDecoderConfig = SS_VAE_DECODER_PRODUCTION,
    *,
    dtype: torch.dtype | None = None,
) -> SSVAEDecoder:
    """The sparse-structure decoder (``struct_dec.`` in the shape VAE file):
    16^3 latent -> 64^3 occupancy."""
    sd = _read(path, STRUCTURE_DECODER_PREFIX, dtype)
    with torch.device("meta"):
        module = SSVAEDecoder(config, _ops_for(sd, dtype))
    return _fill(module, sd, "sparse-structure VAE decoder")


def load_shape_slat_decoder(
    path: str | Path,
    config: OctreeVaeDecoderConfig = OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    *,
    resolution: int = SHAPE_DECODER_RESOLUTION,
    dtype: torch.dtype | None = None,
) -> FlexiDualGridVaeDecoder:
    """The FlexiDualGrid shape decoder (``shape_dec.`` in the shape VAE file)."""
    sd = _read(path, SHAPE_DECODER_PREFIX, dtype)
    return _fill(
        FlexiDualGridVaeDecoder(config, resolution=resolution), sd, "shape SLat decoder"
    )


def load_tex_slat_decoder(
    path: str | Path,
    config: OctreeVaeDecoderConfig = OCTREE_VAE_DECODER_TORSO_PRODUCTION,
    *,
    dtype: torch.dtype | None = None,
) -> SparseUnetVaeDecoder:
    """The texture decoder (``txt_dec.``, its own file). Six output channels and
    no subdivision head — it grows onto the shape decoder's coordinate grid."""
    sd = _read(path, TEXTURE_DECODER_PREFIX, dtype)
    module = SparseUnetVaeDecoder(config, out_channels=6, pred_subdiv=False)
    return _fill(module, sd, "texture SLat decoder")


# -- conditioner -----------------------------------------------------------


def load_dino_conditioner(
    path: str | Path, *, dtype: torch.dtype | None = None
) -> DinoV3ImageConditioner:
    """The DINOv3 image conditioner from its own single file."""
    return DinoV3ImageConditioner.from_file(path, dtype=dtype)
