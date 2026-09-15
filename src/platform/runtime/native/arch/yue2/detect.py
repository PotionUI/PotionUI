"""Which YuE2 file is this? -- key-space role classification."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

__all__ = ["LM", "LM_COMFY_REPACK", "VAE", "YUE2_ROLES", "detect_yue2_role", "detect_yue2_role_from_filename"]

LM = "lm"
LM_COMFY_REPACK = "lm_comfy_repack"
VAE = "vae"
YUE2_ROLES = (LM, LM_COMFY_REPACK, VAE)

_LM_SIG = "vae2llm.weight"
_LM_SIG2 = "model.layers.0.nar_self_attn.q_proj.weight"

_LM_COMFY_REPACK_SIG = "vae2llm.weight"
_LM_COMFY_REPACK_SIG2 = "model.layers.0.self_attn.qkv_proj.weight"

_VAE_SIG = "decoder.layers.0.weight_v"
_VAE_SIG2 = "decoder.layers.8.weight_v"


def detect_yue2_role(keys: Iterable[str]) -> str | None:
    """The role ``keys`` belong to, or ``None`` when this is not a YuE2 file."""
    keys = set(keys)
    if _LM_SIG in keys and _LM_SIG2 in keys:
        return LM
    if _LM_COMFY_REPACK_SIG in keys and _LM_COMFY_REPACK_SIG2 in keys:
        return LM_COMFY_REPACK
    if _VAE_SIG in keys and _VAE_SIG2 in keys:
        return VAE
    return None


_FILENAME_MARKERS = (
    ("yue2-vae", VAE),
    ("yue2_vae", VAE),
    ("yue2vae", VAE),
    ("yue2-3b", LM),
    ("yue2_3b", LM),
    ("yue2", LM),
)


def detect_yue2_role_from_filename(name: str) -> str | None:
    stem = Path(name).name.lower()
    for marker, role in _FILENAME_MARKERS:
        if marker in stem:
            return role
    return None
