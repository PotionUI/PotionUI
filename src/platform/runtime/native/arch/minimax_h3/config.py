"""``MiniMaxH3Config`` — construction config for the MiniMax-H3 packed-sequence DiT.

MiniMax-H3 runs one stack of blocks over one packed 1-D sequence holding text,
conditioning-media, audio and video rows; there is no cross-attention and no
per-modality block weights (see ``model.py``'s module docstring). Shape-derived
fields come from the detector; ``patch_size`` / ``rope_theta`` / the eps
constants are MiniMax-H3 arch constants (not shape-recoverable).

Two checkpoint shapes exist for the same architecture (see ``detect/
unet_detect.py``): **full** ships a ``time_embedder`` MLP and a per-block
``adaln_proj.linear`` at ``time_embed_dim=2688``; **pruned** drops
``time_embedder`` entirely and ships an ``adaln_t_table`` lookup curve plus a
per-block ``adaln_proj.linear`` at a small rank (``time_embed_dim`` becomes the
curve's row width, 8 in the released checkpoint) instead. ``pruned`` selects
which of the two ``MiniMaxH3Model`` builds; ``time_embed_dim`` carries a
different meaning in each branch but the same checkpoint field name, mirroring
how the checkpoint itself overloads it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MINIMAX_H3 = "minimax_h3"

# Every AdaLN modulation table (per-block and final) holds one row per
# (distinct timestep, modality) pair; modality is always one of these three.
MINIMAX_H3_MODALITY_NUM = 3


@dataclass(frozen=True)
class MiniMaxH3Config:
    """Fully-resolved MiniMax-H3 DiT hyper-parameters (full or pruned AdaLN)."""

    hidden_size: int = 5376
    num_layers: int = 50
    num_refiner_layers: int = 2
    num_attention_heads: int = 56
    attention_head_dim: int = 128
    ffn_dim: int = 14336
    in_channels: int = 24              # video latent channels
    audio_in_channels: int = 32
    patch_size: tuple[int, int, int] = (1, 2, 2)
    text_dim: int = 5120                # Qwen3-VL hidden width the text encoder emits
    rope_freq_dim: int = 16
    rope_theta: float = 10000.0
    norm_eps: float = 1e-5
    qk_norm_eps: float = 1e-5
    final_norm_eps: float = 1e-5
    # AdaLN mode. False (full): ``time_embedder`` MLP feeds every ``adaln_proj``
    # at ``time_embed_dim`` width. True (pruned): no ``time_embedder``;
    # ``adaln_t_table`` is looked up and interpolated directly into the (much
    # narrower) ``time_embed_dim``, and every ``adaln_proj`` runs WITHOUT the
    # SiLU activation the full checkpoint applies (the curve is trained to
    # already be that activation's output — see ``model.py``'s
    # ``MiniMaxH3AdalnProj``).
    pruned: bool = False
    time_embed_dim: int = 2688
    freq_dim: int = 256                 # full only: sinusoidal timestep input width
    time_embed_hidden_dim: int = 5376   # full only: time_embedder MLP inner width
    adaln_curve_grid: int = 1025        # pruned only: adaln_t_table row count

    @property
    def inner_dim(self) -> int:
        return self.num_attention_heads * self.attention_head_dim

    @property
    def video_patch_dim(self) -> int:
        return self.in_channels * math.prod(self.patch_size)

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "MiniMaxH3Config":
        if config.get("image_model") != MINIMAX_H3:
            raise ValueError(f"MiniMaxH3Config: unsupported image_model {config.get('image_model')!r}")
        return cls(
            hidden_size=int(config["hidden_size"]),
            num_layers=int(config["num_layers"]),
            num_refiner_layers=int(config.get("num_refiner_layers", 2)),
            num_attention_heads=int(config["num_attention_heads"]),
            attention_head_dim=int(config["attention_head_dim"]),
            ffn_dim=int(config["ffn_dim"]),
            in_channels=int(config.get("in_channels", 24)),
            audio_in_channels=int(config.get("audio_in_channels", 32)),
            patch_size=tuple(config.get("patch_size", (1, 2, 2))),
            text_dim=int(config["text_dim"]),
            rope_freq_dim=int(config.get("rope_freq_dim", 16)),
            rope_theta=float(config.get("rope_theta", 10000.0)),
            norm_eps=float(config.get("norm_eps", 1e-5)),
            qk_norm_eps=float(config.get("qk_norm_eps", 1e-5)),
            final_norm_eps=float(config.get("final_norm_eps", 1e-5)),
            pruned=bool(config.get("pruned", False)),
            time_embed_dim=int(config["time_embed_dim"]),
            freq_dim=int(config.get("freq_dim", 256)),
            time_embed_hidden_dim=int(config.get("time_embed_hidden_dim", 5376)),
            adaln_curve_grid=int(config.get("adaln_curve_grid", 1025)),
        )
