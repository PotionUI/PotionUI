"""``ZImageConfig`` — construction config for the Z-Image NextDiT.

Z-Image is Alpha-VLLM's NextDiT (the Lumina-Image-2.0 backbone) at ``dim=3840``
with the ``z_image_modulation`` variant enabled. It is a single-stream joint
transformer: the caption tokens (from a Qwen3-4B text encoder) and the patchified
image tokens are refined by their own small refiner stacks, concatenated, and run
through ``n_layers`` shared blocks under one adaLN modulation driven by the
timestep alone (no pooled/CLIP vector on this variant).

Shape-derived fields come from the detector; the RoPE constants (``axes_dims`` /
``rope_theta``) and the head split are the ComfyUI ``ZImage`` config values for
the ``dim==3840`` branch (they are not recoverable from tensor shapes because the
qkv projection fuses q/k/v and Z-Image has no GQA — n_kv_heads == n_heads). The
``z_image_modulation`` flag selects the 256-dim (vs 1024-dim) adaLN input and the
learned caption/image pad tokens padded to ``pad_tokens_multiple``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LUMINA2 = "lumina2"


@dataclass(frozen=True)
class ZImageConfig:
    """Fully-resolved Z-Image NextDiT hyper-parameters."""

    in_channels: int              # VAE latent channels the DiT ingests (16)
    dim: int                      # model width (3840)
    cap_feat_dim: int             # caption/text-encoder embedding width (2560, Qwen3-4B)
    n_layers: int                 # shared joint-transformer blocks
    n_refiner_layers: int         # noise_refiner / context_refiner depth (2)
    n_heads: int                  # attention heads
    n_kv_heads: int               # kv heads (== n_heads for Z-Image, no GQA)
    intermediate_size: int        # SwiGLU FFN hidden dim (10240)
    axes_dims: tuple[int, int, int]   # per-axis RoPE dims (sum == head_dim)
    axes_lens: tuple[int, int, int]   # per-axis RoPE lengths
    rope_theta: float = 256.0
    patch_size: int = 2
    norm_eps: float = 1e-5
    pad_tokens_multiple: int = 32
    time_scale: float = 1000.0

    @property
    def head_dim(self) -> int:
        return self.dim // self.n_heads

    def __post_init__(self) -> None:
        if self.dim % self.n_heads:
            raise ValueError(f"dim {self.dim} not divisible by n_heads {self.n_heads}")
        if sum(self.axes_dims) != self.head_dim:
            raise ValueError(
                f"axes_dims {self.axes_dims} (sum {sum(self.axes_dims)}) must equal "
                f"head_dim {self.head_dim}"
            )

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "ZImageConfig":
        if config.get("image_model") != LUMINA2:
            raise ValueError(f"ZImageConfig: unsupported image_model {config.get('image_model')!r}")
        return cls(
            in_channels=int(config["in_channels"]),
            dim=int(config["dim"]),
            cap_feat_dim=int(config["cap_feat_dim"]),
            n_layers=int(config["n_layers"]),
            n_refiner_layers=int(config.get("n_refiner_layers", 2)),
            n_heads=int(config["n_heads"]),
            n_kv_heads=int(config.get("n_kv_heads", config["n_heads"])),
            intermediate_size=int(config["intermediate_size"]),
            axes_dims=tuple(config["axes_dims"]),
            axes_lens=tuple(config.get("axes_lens", (1536, 512, 512))),
            rope_theta=float(config.get("rope_theta", 256.0)),
            patch_size=int(config.get("patch_size", 2)),
            norm_eps=float(config.get("norm_eps", 1e-5)),
            pad_tokens_multiple=int(config.get("pad_tokens_multiple", 32)),
            time_scale=float(config.get("time_scale", 1000.0)),
        )
