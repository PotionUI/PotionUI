"""``QwenImage21Config`` — construction config for the Qwen-Image-2.1 DiT.

Qwen-Image-2.1 is a single-stream DiT: text and image tokens share one
sequence and one set of attention projections per block, unlike Qwen-Image
(1.0)'s dual-stream (joint-attention) design. It also consumes the VAE latent
unpatched (``in_channels`` IS the latent channel count, 64 — no 2x2 patchify,
unlike 1.0's ``in_channels = 16 * patch^2``), so this config carries no
``patch_size`` field. Shape-derived fields come from the detector;
``axes_dims_rope``/``theta`` are arch constants (ComfyUI
``QwenImage21Transformer2DModel`` defaults, shared with 1.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

QWEN_IMAGE21 = "qwen_image21"


@dataclass(frozen=True)
class QwenImage21Config:
    """Fully-resolved Qwen-Image-2.1 DiT hyper-parameters."""

    in_channels: int              # VAE latent channels the DiT ingests directly (64, unpatched)
    out_channels: int             # VAE latent channels emitted (64)
    inner_dim: int                # model width (num_attention_heads * attention_head_dim)
    num_layers: int
    num_attention_heads: int
    attention_head_dim: int
    joint_attention_dim: int      # text-encoder embedding width (4096, Qwen3-VL-8B)
    mlp_ratio: int = 3
    axes_dims_rope: tuple[int, int, int] = (16, 56, 56)
    theta: int = 10000
    fused_mlp: bool = True

    def __post_init__(self) -> None:
        if self.inner_dim != self.num_attention_heads * self.attention_head_dim:
            raise ValueError(
                f"inner_dim {self.inner_dim} != heads {self.num_attention_heads} * "
                f"head_dim {self.attention_head_dim}"
            )
        if sum(self.axes_dims_rope) != self.attention_head_dim:
            raise ValueError(
                f"axes_dims_rope {self.axes_dims_rope} (sum {sum(self.axes_dims_rope)}) "
                f"must equal attention_head_dim {self.attention_head_dim}"
            )

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "QwenImage21Config":
        if config.get("image_model") != QWEN_IMAGE21:
            raise ValueError(f"QwenImage21Config: unsupported image_model {config.get('image_model')!r}")
        return cls(
            in_channels=int(config["in_channels"]),
            out_channels=int(config["out_channels"]),
            inner_dim=int(config["inner_dim"]),
            num_layers=int(config["num_layers"]),
            num_attention_heads=int(config["num_attention_heads"]),
            attention_head_dim=int(config["attention_head_dim"]),
            joint_attention_dim=int(config["joint_attention_dim"]),
            mlp_ratio=int(config.get("mlp_ratio", 3)),
            axes_dims_rope=tuple(config.get("axes_dims_rope", (16, 56, 56))),
            theta=int(config.get("theta", 10000)),
            fused_mlp=bool(config.get("fused_mlp", True)),
        )
