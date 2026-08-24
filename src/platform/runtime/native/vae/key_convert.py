"""Diffusers -> ldm VAE key renaming, vendored from ComfyUI's ``comfy/diffusers_convert.py``
(itself adapted from HuggingFace diffusers' ``convert_diffusers_to_original_stable_diffusion.py``).

Trimmed to the VAE half only (the text-encoder conversion in the original file
is irrelevant here). Behaviour is unchanged from the ComfyUI source: build a
per-key rename mapping via ordered substring replacement, then reshape the
mid-block attention ``q``/``k``/``v``/``proj_out`` weights from diffusers'
``Linear`` shape ``[C, C]`` to ldm's ``Conv2d`` shape ``[C, C, 1, 1]``.
"""

from __future__ import annotations

import logging

import torch

logger = logging.getLogger(__name__)

_vae_conversion_map = [
    # (ldm, diffusers)
    ("nin_shortcut", "conv_shortcut"),
    ("norm_out", "conv_norm_out"),
    ("mid.attn_1.", "mid_block.attentions.0."),
]

for _i in range(4):
    for _j in range(2):
        _vae_conversion_map.append((f"encoder.down.{_i}.block.{_j}.", f"encoder.down_blocks.{_i}.resnets.{_j}."))
    if _i < 3:
        _vae_conversion_map.append((f"down.{_i}.downsample.", f"down_blocks.{_i}.downsamplers.0."))
        _vae_conversion_map.append((f"up.{3 - _i}.upsample.", f"up_blocks.{_i}.upsamplers.0."))
    for _j in range(3):
        _vae_conversion_map.append((f"decoder.up.{3 - _i}.block.{_j}.", f"decoder.up_blocks.{_i}.resnets.{_j}."))

for _i in range(2):
    _vae_conversion_map.append((f"mid.block_{_i + 1}.", f"mid_block.resnets.{_i}."))

_vae_conversion_map_attn = [
    # (ldm, diffusers)
    ("norm.", "group_norm."),
    ("q.", "query."),
    ("k.", "key."),
    ("v.", "value."),
    ("q.", "to_q."),
    ("k.", "to_k."),
    ("v.", "to_v."),
    ("proj_out.", "to_out.0."),
    ("proj_out.", "proj_attn."),
]


def _reshape_for_conv(weight: torch.Tensor) -> torch.Tensor:
    return weight.reshape(*weight.shape, 1, 1)


def convert_vae_state_dict(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """Rename a diffusers-layout VAE state dict to ldm layout (in place key mapping)."""
    mapping = {k: k for k in sd.keys()}
    for k, v in mapping.items():
        for ldm_part, hf_part in _vae_conversion_map:
            v = v.replace(hf_part, ldm_part)
        mapping[k] = v
    for k, v in mapping.items():
        if "attentions" in k:
            for ldm_part, hf_part in _vae_conversion_map_attn:
                v = v.replace(hf_part, ldm_part)
            mapping[k] = v

    new_sd = {v: sd[k] for k, v in mapping.items()}

    for k in list(new_sd.keys()):
        for weight_name in ("q", "k", "v", "proj_out"):
            if k == f"encoder.mid.attn_1.{weight_name}.weight" or k == f"decoder.mid.attn_1.{weight_name}.weight":
                new_sd[k] = _reshape_for_conv(new_sd[k])

    return new_sd
