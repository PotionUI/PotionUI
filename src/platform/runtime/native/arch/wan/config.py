"""Config params for the Wan 2.1 / 2.2 diffusion transformer.

``WanParams`` mirrors the subset of ``comfy/ldm/wan/model.py::WanModel.__init__``
kwargs the base t2v/i2v backbone needs (the vace/camera/s2v/humo/animate/
multitalk extensions are intentionally out of scope). ``from_detect_config``
maps a state-dict-derived config (see ``detect/unet_detect.py``) onto these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WanParams:
    """Hyperparameters for one Wan DiT variant."""

    model_type: str = "t2v"                       # "t2v" | "i2v"
    patch_size: tuple[int, int, int] = (1, 2, 2)  # (t, h, w)
    text_len: int = 512
    in_dim: int = 16
    dim: int = 2048
    ffn_dim: int = 8192
    freq_dim: int = 256
    text_dim: int = 4096
    out_dim: int = 16
    num_heads: int = 16
    num_layers: int = 32
    window_size: tuple[int, int] = (-1, -1)
    qk_norm: bool = True
    cross_attn_norm: bool = True
    eps: float = 1e-6
    # i2v first-last-frame positional-embedding token count (FLF2V models only).
    flf_pos_embed_token_number: int | None = None
    # Reference-conv input channels (a few i2v variants); None disables ref_conv.
    in_dim_ref_conv: int | None = None

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "WanParams":
        """Build from a detected DiT config dict (shape-derived hyperparams)."""
        return cls(
            model_type=config.get("model_type", "t2v"),
            patch_size=tuple(config.get("patch_size", (1, 2, 2))),
            in_dim=config["in_dim"],
            dim=config["dim"],
            ffn_dim=config["ffn_dim"],
            freq_dim=config.get("freq_dim", 256),
            text_dim=config.get("text_dim", 4096),
            out_dim=config["out_dim"],
            num_heads=config["num_heads"],
            num_layers=config["num_layers"],
            qk_norm=config.get("qk_norm", True),
            cross_attn_norm=config.get("cross_attn_norm", True),
            eps=config.get("eps", 1e-6),
            text_len=config.get("text_len", 512),
            flf_pos_embed_token_number=config.get("flf_pos_embed_token_number"),
            in_dim_ref_conv=config.get("in_dim_ref_conv"),
        )
