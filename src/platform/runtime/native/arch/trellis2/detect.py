"""Which TRELLIS.2 file is this? — role classification for the Comfy-Org layout.

Comfy-Org/TRELLIS.2 ships the family as four depot files, and none of them is
loadable by the generic single-DiT path:

  ``diffusion_models/trellis_2_bf16.safetensors``  — FOUR flow DiTs in one file
  ``vae/trellis_2_shape_vae_bf16.safetensors``     — struct + shape decoders
  ``vae/trellis_2_texture_vae_bf16.safetensors``   — texture decoder
  ``clip_vision/dino_v3_vit_l.safetensors``        — the DINOv3 image conditioner

A role is decided from the file's KEY SPACE, which is what actually determines
whether a loader can read it; the filename is a fallback for callers that only
have a name (a picker rendering a list, a preset naming a file that is not on
this host yet). Names are a hint and nothing more — a renamed file still loads,
and a file named right but keyed wrong is still rejected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

__all__ = [
    "FLOW_BUNDLE",
    "IMAGE_ENCODER",
    "SHAPE_VAE",
    "TEXTURE_VAE",
    "TRELLIS2_ROLES",
    "detect_trellis2_role",
    "detect_trellis2_role_from_filename",
    "trellis2_role_of_file",
]

FLOW_BUNDLE = "flow_bundle"
SHAPE_VAE = "shape_vae"
TEXTURE_VAE = "texture_vae"
IMAGE_ENCODER = "image_encoder"

TRELLIS2_ROLES = (FLOW_BUNDLE, SHAPE_VAE, TEXTURE_VAE, IMAGE_ENCODER)

#: The four sub-models inside the unified flow file, and the prefix each sits
#: under. There is one texture flow where upstream ships a 512 and a 1024 — the
#: two upstream texture configs differ only in ``resolution``, so both tiers are
#: pointed at it (see ``config.TEX_SLAT_FLOW_512`` / ``TEX_SLAT_FLOW_1024``).
FLOW_PREFIXES = {
    "structure": "model.structure_model.",
    "shape_512": "model.img2shape_512.",
    "shape_1024": "model.img2shape.",
    "texture": "model.shape2txt.",
}

#: Decoder prefixes inside each VAE file.
STRUCTURE_DECODER_PREFIX = "struct_dec."
SHAPE_DECODER_PREFIX = "shape_dec."
TEXTURE_DECODER_PREFIX = "txt_dec."

#: DINOv3 blocks sit at ``layer.N.*`` in the checkpoint (``conditioner.py``
#: remaps them); ``embeddings.patch_embeddings`` is what makes it a ViT rather
#: than any other ``layer.``-keyed stack.
_IMAGE_ENCODER_SIG = "embeddings.patch_embeddings.weight"


def _has_prefix(keys: Iterable[str], prefix: str) -> bool:
    return any(key.startswith(prefix) for key in keys)


def detect_trellis2_role(keys: Iterable[str]) -> str | None:
    """The role ``keys`` belong to, or ``None`` when this is not a TRELLIS.2 file.

    ``model.img2shape.`` is a prefix of nothing else here, but ``model.img2shape_512.``
    is NOT a prefix-match for it (the underscore breaks the dotted boundary), so
    the two shape flows are distinguishable and the bundle needs all four.
    """
    keys = list(keys)

    if all(_has_prefix(keys, prefix) for prefix in FLOW_PREFIXES.values()):
        return FLOW_BUNDLE
    if _has_prefix(keys, STRUCTURE_DECODER_PREFIX) and _has_prefix(keys, SHAPE_DECODER_PREFIX):
        return SHAPE_VAE
    if _has_prefix(keys, TEXTURE_DECODER_PREFIX):
        return TEXTURE_VAE
    if _IMAGE_ENCODER_SIG in keys and _has_prefix(keys, "layer."):
        return IMAGE_ENCODER
    return None


#: Substrings of the Comfy-Org file names, most specific first — ``texture_vae``
#: and ``shape_vae`` both contain ``vae``, and the bundle's own name contains
#: neither.
_FILENAME_MARKERS = (
    ("shape_vae", SHAPE_VAE),
    ("texture_vae", TEXTURE_VAE),
    ("dino", IMAGE_ENCODER),
    ("trellis_2", FLOW_BUNDLE),
    ("trellis2", FLOW_BUNDLE),
)


def detect_trellis2_role_from_filename(name: str) -> str | None:
    """A best-effort role for a name alone, for callers with no file to open.

    Never authoritative: :func:`detect_trellis2_role` is what a loader trusts.
    """
    stem = Path(name).name.lower()
    for marker, role in _FILENAME_MARKERS:
        if marker in stem:
            return role
    return None


def trellis2_role_of_file(path: str | Path) -> str | None:
    """The role of a real file, read from its safetensors header only.

    No tensor storage is touched — this is cheap enough to run over a whole
    depot directory.
    """
    from safetensors import safe_open

    path = Path(path)
    if path.suffix.lower() not in (".safetensors", ".sft"):
        return None
    try:
        with safe_open(str(path), framework="pt") as f:
            return detect_trellis2_role(f.keys())
    except Exception:
        return None
