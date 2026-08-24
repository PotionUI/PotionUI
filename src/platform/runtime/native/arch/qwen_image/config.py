"""``QwenImageConfig`` — construction config for the Qwen-Image MMDiT.

Qwen-Image is a dual-stream (joint-attention) MMDiT: image and text tokens run
through parallel modulated streams and attend jointly each block. Shape-derived
fields come from the detector; ``axes_dims_rope``/``theta``/``patch_size`` are
arch constants (ComfyUI ``QwenImageTransformer2DModel`` defaults). The variant
flags (``default_ref_method``, ``use_additional_t_cond``) are detected from
optional keys so the 2512 (plain t2i) and 2511 (edit / index-timestep-zero)
checkpoints both build cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QWEN_IMAGE = "qwen_image"


@dataclass(frozen=True)
class QwenImageConfig:
    """Fully-resolved Qwen-Image DiT hyper-parameters."""

    in_channels: int              # packed latent channels the DiT ingests (16 * patch^2 = 64)
    out_channels: int             # VAE latent channels (16)
    inner_dim: int                # model width (num_attention_heads * attention_head_dim)
    num_layers: int
    num_attention_heads: int
    attention_head_dim: int
    joint_attention_dim: int      # text-encoder embedding width (3584, Qwen2.5-VL)
    patch_size: int = 2
    axes_dims_rope: tuple[int, int, int] = (16, 56, 56)
    theta: int = 10000
    # variant-derived (optional keys):
    default_ref_method: str = "index"
    use_additional_t_cond: bool = False

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
    def from_detect_config(cls, config: dict[str, Any]) -> "QwenImageConfig":
        if config.get("image_model") != QWEN_IMAGE:
            raise ValueError(f"QwenImageConfig: unsupported image_model {config.get('image_model')!r}")
        return cls(
            in_channels=int(config["in_channels"]),
            out_channels=int(config["out_channels"]),
            inner_dim=int(config["inner_dim"]),
            num_layers=int(config["num_layers"]),
            num_attention_heads=int(config["num_attention_heads"]),
            attention_head_dim=int(config["attention_head_dim"]),
            joint_attention_dim=int(config["joint_attention_dim"]),
            patch_size=int(config.get("patch_size", 2)),
            axes_dims_rope=tuple(config.get("axes_dims_rope", (16, 56, 56))),
            theta=int(config.get("theta", 10000)),
            default_ref_method=str(config.get("default_ref_method", "index")),
            use_additional_t_cond=bool(config.get("use_additional_t_cond", False)),
        )
