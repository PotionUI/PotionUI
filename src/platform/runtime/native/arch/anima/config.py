"""``AnimaConfig`` — construction config for the Anima DiT.

Anima is NVIDIA-Cosmos-Predict2's ``MiniTrainDIT`` (adaLN-modulated 3D DiT, cross-
attending to a text context) with an in-model ``LLMAdapter`` bolted on. The
adapter fuses a Qwen3-0.6B hidden state (its cross-attention *source*) with a set
of T5 token ids (its own ``Embedding(32128, ...)`` target sequence) into the DiT's
cross-attention context — so the adapter weights ship *inside* the diffusion
checkpoint, not the text encoder.

All the load-bearing hyper-parameters are shape-derived by the detector
(``detect/unet_detect.py:_detect_anima``); the fields here mirror ComfyUI's
``model_detection`` Anima branch and the ``LLMAdapter`` defaults
(``comfy/ldm/anima/model.py``). The local ``anima_aestheticV10b`` checkpoint is
the 2048-wide / 28-block image (t2i, ``in_channels`` 16) variant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ANIMA = "anima"


@dataclass(frozen=True)
class AnimaConfig:
    """Fully-resolved Anima DiT hyper-parameters (MiniTrainDIT + LLMAdapter)."""

    # -- MiniTrainDIT core (shape-derived) ---------------------------------
    in_channels: int              # VAE latent channels the DiT ingests (16, Wan21)
    out_channels: int             # velocity channels (16)
    model_channels: int           # model width (2048)
    num_blocks: int               # transformer depth (28)
    num_heads: int                # attention heads (16 -> head_dim 128)
    crossattn_emb_channels: int   # cross-attention context width (1024)
    patch_spatial: int = 2
    patch_temporal: int = 1
    concat_padding_mask: bool = True
    mlp_ratio: float = 4.0
    use_adaln_lora: bool = True
    adaln_lora_dim: int = 256
    # -- positional embedding (rope3d) -------------------------------------
    max_img_h: int = 240
    max_img_w: int = 240
    max_frames: int = 128
    min_fps: int = 1
    max_fps: int = 30
    rope_h_extrapolation_ratio: float = 4.0
    rope_w_extrapolation_ratio: float = 4.0
    rope_t_extrapolation_ratio: float = 1.0
    rope_enable_fps_modulation: bool = False
    # -- LLMAdapter (fixed arch, shape-verified) ---------------------------
    llm_source_dim: int = 1024    # Qwen3-0.6B hidden size (cross-attn source)
    llm_target_dim: int = 1024    # == crossattn_emb_channels (adapter output)
    llm_model_dim: int = 1024
    llm_num_layers: int = 6
    llm_num_heads: int = 16
    llm_vocab_size: int = 32128   # T5 vocab (the adapter's own token embedding)

    def __post_init__(self) -> None:
        if self.model_channels % self.num_heads != 0:
            raise ValueError(
                f"model_channels {self.model_channels} not divisible by num_heads {self.num_heads}"
            )

    @property
    def head_dim(self) -> int:
        return self.model_channels // self.num_heads

    @classmethod
    def from_detect_config(cls, config: dict[str, Any]) -> "AnimaConfig":
        if config.get("image_model") != ANIMA:
            raise ValueError(f"AnimaConfig: unsupported image_model {config.get('image_model')!r}")
        return cls(
            in_channels=int(config["in_channels"]),
            out_channels=int(config["out_channels"]),
            model_channels=int(config["model_channels"]),
            num_blocks=int(config["num_blocks"]),
            num_heads=int(config["num_heads"]),
            crossattn_emb_channels=int(config.get("crossattn_emb_channels", 1024)),
            patch_spatial=int(config.get("patch_spatial", 2)),
            patch_temporal=int(config.get("patch_temporal", 1)),
            concat_padding_mask=bool(config.get("concat_padding_mask", True)),
            mlp_ratio=float(config.get("mlp_ratio", 4.0)),
            use_adaln_lora=bool(config.get("use_adaln_lora", True)),
            adaln_lora_dim=int(config.get("adaln_lora_dim", 256)),
            max_img_h=int(config.get("max_img_h", 240)),
            max_img_w=int(config.get("max_img_w", 240)),
            max_frames=int(config.get("max_frames", 128)),
            min_fps=int(config.get("min_fps", 1)),
            max_fps=int(config.get("max_fps", 30)),
            rope_h_extrapolation_ratio=float(config.get("rope_h_extrapolation_ratio", 4.0)),
            rope_w_extrapolation_ratio=float(config.get("rope_w_extrapolation_ratio", 4.0)),
            rope_t_extrapolation_ratio=float(config.get("rope_t_extrapolation_ratio", 1.0)),
            rope_enable_fps_modulation=bool(config.get("rope_enable_fps_modulation", False)),
            llm_source_dim=int(config.get("llm_source_dim", 1024)),
            llm_target_dim=int(config.get("llm_target_dim", 1024)),
            llm_model_dim=int(config.get("llm_model_dim", 1024)),
            llm_num_layers=int(config.get("llm_num_layers", 6)),
            llm_num_heads=int(config.get("llm_num_heads", 16)),
            llm_vocab_size=int(config.get("llm_vocab_size", 32128)),
        )
