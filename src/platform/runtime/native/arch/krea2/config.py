"""``Krea2Config`` — construction config for the Krea-2 SingleStream MMDiT.

Krea-2 is a novel arch (gate zero proved it is NOT Flux): a single-stream MMDiT
with a built-in multi-layer text-fusion transformer (``txtfusion``), GQA
attention with a sigmoid output gate, shared per-block modulation, and SwiGLU
MLPs. Field names/defaults mirror diffusers 0.39.0's
``Krea2Transformer2DModel.__init__`` (``transformer_krea2.py``, Apache-2.0) —
see ``model.py``'s header for the full rename table (this dataclass's fields
were already named close to diffusers' own: ``features``==``hidden_size``,
``heads``==``num_attention_heads``, ``kvheads``==``num_key_value_heads``,
``txtdim``==``text_hidden_dim``, ``txtlayers``==``num_text_layers``, etc.).
There is no on-disk config.json for this family (the native engine detects
everything from tensor shapes — ``detect_unet_config``'s krea2 branch), so
this dataclass carries the fixed hyper-parameters diffusers stores as plain
config fields (``theta``, ``patch``, ``norm_eps``) alongside the
shape-derived ones the detector fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KREA2 = "krea2"


@dataclass(frozen=True)
class Krea2Config:
    """Fully-resolved Krea-2 DiT hyper-parameters."""

    features: int          # model width (== attention_head_dim * num_attention_heads)
    heads: int             # query heads
    kvheads: int           # key/value heads (GQA)
    channels: int          # latent channels (== VAE latent channels, 16)
    layers: int            # number of SingleStream blocks
    multiplier: int        # SwiGLU expansion multiplier
    tdim: int              # timestep embedding dim
    txtdim: int            # text-encoder hidden dim
    txtheads: int          # text-fusion query heads
    txtkvheads: int        # text-fusion kv heads
    txtlayers: int         # number of text-encoder layers fused (projector in-dim)
    patch: int = 2
    theta: float = 1000.0
    norm_eps: float = 1e-5
    bias: bool = False      # block attn/mlp biases (peripherals always carry bias)

    @property
    def headdim(self) -> int:
        return self.features // self.heads

    @property
    def rope_axes(self) -> list[int]:
        """RoPE (t, h, w) axis split. Diffusers stores this as a plain config
        field (``axes_dims_rope``, default ``(32, 48, 48)`` for its default
        ``attention_head_dim=128``); this formula reproduces exactly that
        default for headdim 128 and generalizes it for the detector's tiny
        test configs (any headdim divisible by 16), since we have no config
        metadata to read a fixed tuple from."""
        hd = self.headdim
        return [hd - 12 * (hd // 16), 6 * (hd // 16), 6 * (hd // 16)]

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "Krea2Config":
        if config.get("image_model") != KREA2:
            raise ValueError(f"Krea2Config: unsupported image_model {config.get('image_model')!r}")
        return cls(
            features=int(config["features"]),
            heads=int(config["heads"]),
            kvheads=int(config["kvheads"]),
            channels=int(config["channels"]),
            layers=int(config["layers"]),
            multiplier=int(config["multiplier"]),
            tdim=int(config["tdim"]),
            txtdim=int(config["txtdim"]),
            txtheads=int(config["txtheads"]),
            txtkvheads=int(config["txtkvheads"]),
            txtlayers=int(config["txtlayers"]),
            patch=int(config.get("patch", 2)),
            theta=float(config.get("theta", 1000.0)),
        )
