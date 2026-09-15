# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/modeling_yue2.py (Apache-2.0)

"""``YuE2Config`` — construction config for the YuE2 AR/NAR backbone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

YUE2 = "yue2"


@dataclass(frozen=True)
class YuE2Config:
    """Fully-resolved YuE2 backbone hyper-parameters."""

    hidden_size: int = 2048
    num_hidden_layers: int = 28
    num_attention_heads: int = 16
    num_key_value_heads: int = 8
    head_dim: int = 128
    intermediate_size: int = 6144
    vocab_size: int = 184704
    rms_norm_eps: float = 1e-6
    rope_theta: float = 1_000_000.0
    max_position_embeddings: int = 24576
    latent_dim: int = 64
    max_latent_frames: int = 24576
    timestep_shift: float = 1.0

    def __post_init__(self) -> None:
        if self.hidden_size != self.num_attention_heads * self.head_dim:
            raise ValueError(
                f"hidden_size {self.hidden_size} != num_attention_heads "
                f"{self.num_attention_heads} * head_dim {self.head_dim}"
            )
        if self.num_key_value_heads <= 0 or self.num_attention_heads % self.num_key_value_heads:
            raise ValueError(
                f"num_attention_heads {self.num_attention_heads} is not a multiple of "
                f"num_key_value_heads {self.num_key_value_heads}"
            )

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "YuE2Config":
        if config.get("arch_type") != YUE2:
            raise ValueError(f"YuE2Config: unsupported arch_type {config.get('arch_type')!r}")
        return cls(
            hidden_size=int(config["hidden_size"]),
            num_hidden_layers=int(config["num_hidden_layers"]),
            num_attention_heads=int(config["num_attention_heads"]),
            num_key_value_heads=int(config.get("num_key_value_heads", config["num_attention_heads"])),
            head_dim=int(config["head_dim"]),
            intermediate_size=int(config["intermediate_size"]),
            vocab_size=int(config.get("vocab_size", 184704)),
            rms_norm_eps=float(config.get("rms_norm_eps", 1e-6)),
            rope_theta=float(config.get("rope_theta", 1_000_000.0)),
            max_position_embeddings=int(config.get("max_position_embeddings", 24576)),
            latent_dim=int(config.get("latent_dim", 64)),
            max_latent_frames=int(config.get("max_latent_frames", 24576)),
            timestep_shift=float(config.get("timestep_shift", 1.0)),
        )
