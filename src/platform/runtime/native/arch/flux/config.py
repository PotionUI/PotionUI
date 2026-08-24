"""``FluxParams`` — the full construction config for the Flux DiT.

``detect_unet_config`` (``src/platform/runtime/native/detect/unet_detect.py``) emits only the
*shape-derived* fields (hidden size, depth, channel counts, axes, theta, ...).
The remaining fields are *variant-derived* — a fixed consequence of whether the
checkpoint is Flux1 or Flux2 — and are filled in here from ``image_model``,
mirroring ComfyUI's ``model_detection`` flux branch exactly:

  Flux1 (``image_model == "flux"``): per-block modulation, GELU MLP, biases on,
    a pooled-CLIP ``vector_in`` (768), text ids all-zero (``txt_ids_dims == []``).
  Flux2 (``image_model == "flux2"``): shared (global) modulation, SiLU-gated MLP,
    no biases, no ``vector_in``, text ids on RoPE axis 3 (``txt_ids_dims == [3]``).

Keeping the split here means the detector stays pure shape-sniffing and the arch
module receives one fully-resolved dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FLUX1 = "flux"
FLUX2 = "flux2"


@dataclass(frozen=True)
class FluxParams:
    """Fully-resolved Flux DiT hyper-parameters."""

    image_model: str
    in_channels: int
    out_channels: int
    hidden_size: int
    context_in_dim: int
    num_heads: int
    depth: int
    depth_single_blocks: int
    axes_dim: list[int]
    mlp_ratio: float
    theta: int
    patch_size: int
    qkv_bias: bool
    guidance_embed: bool
    # variant-derived (not in the detect dict):
    vec_in_dim: int | None = None
    txt_ids_dims: list[int] = field(default_factory=list)
    global_modulation: bool = False
    mlp_silu_act: bool = False
    ops_bias: bool = True
    yak_mlp: bool = False
    txt_norm: bool = False
    default_ref_method: str = "offset"
    ref_index_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.hidden_size % self.num_heads != 0:
            raise ValueError(
                f"hidden_size {self.hidden_size} not divisible by num_heads {self.num_heads}"
            )
        pe_dim = self.hidden_size // self.num_heads
        if sum(self.axes_dim) != pe_dim:
            raise ValueError(
                f"axes_dim {self.axes_dim} (sum {sum(self.axes_dim)}) must equal "
                f"head_dim {pe_dim}"
            )
        for i in self.txt_ids_dims:
            if not 0 <= i < len(self.axes_dim):
                raise ValueError(
                    f"txt_ids_dims axis {i} out of range for axes_dim {self.axes_dim}"
                )

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "FluxParams":
        """Build from the shape-derived detect dict, adding variant defaults."""
        image_model = config.get("image_model")
        if image_model == FLUX2:
            variant: dict[str, Any] = {
                "vec_in_dim": None,
                "txt_ids_dims": [3],
                "global_modulation": True,
                "mlp_silu_act": True,
                "ops_bias": False,
                "yak_mlp": False,
                "txt_norm": False,
                "default_ref_method": "index",
                "ref_index_scale": 10.0,
            }
        elif image_model == FLUX1:
            variant = {
                "vec_in_dim": 768,
                "txt_ids_dims": [],
                "global_modulation": False,
                "mlp_silu_act": False,
                "ops_bias": True,
                "yak_mlp": False,
                "txt_norm": False,
                "default_ref_method": "offset",
                "ref_index_scale": 1.0,
            }
        else:
            raise ValueError(f"FluxParams: unsupported image_model {image_model!r}")

        return cls(
            image_model=image_model,
            in_channels=int(config["in_channels"]),
            out_channels=int(config["out_channels"]),
            hidden_size=int(config["hidden_size"]),
            context_in_dim=int(config["context_in_dim"]),
            num_heads=int(config["num_heads"]),
            depth=int(config["depth"]),
            depth_single_blocks=int(config["depth_single_blocks"]),
            axes_dim=list(config["axes_dim"]),
            mlp_ratio=float(config["mlp_ratio"]),
            theta=int(config["theta"]),
            patch_size=int(config["patch_size"]),
            qkv_bias=bool(config["qkv_bias"]),
            guidance_embed=bool(config["guidance_embed"]),
            # a detected vec_in_dim (Flux1 control/variants) overrides the default.
            **{**variant, **({"vec_in_dim": config["vec_in_dim"]} if "vec_in_dim" in config else {})},
        )
