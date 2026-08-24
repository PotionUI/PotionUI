"""MiniMax-Music3 text-to-music architecture: prompt contract, embedded tokenizer,
text-encoder config, the AR core (global LLM + depth decoder + generation loop),
the flow-matching DiT + fused condition encoder, and the windowed Euler
denoising loop.
"""

from __future__ import annotations

from .ar_loop import FRAME_HIDDEN_SIZE, generate, position_budget_warning
from .config import MiniMaxMusic3TextEncoderConfig
from .depth_decoder import DepthDecoderModule, generate_depth_codes
from .flow import chunk_starts, crop_bounds, denoise_windowed
from .lm import GlobalLMKVCache, MiniMaxMusic3AudioLM
from .model import MINIMAX_MUSIC3_DIT, MiniMaxMusic3DitConfig, MiniMaxMusic3Model
from .prompt import (
    AUDIO_CODE_OFFSET,
    AUDIO_END_TOKEN_ID,
    MAX_AUDIO_FRAMES,
    MAX_PROMPT_TOKENS,
    SEMANTIC_VOCAB_SIZE,
    SPECIAL_TOKEN_IDS,
    MiniMaxMusic3Tokenizer,
    build_prompt,
    clean_caption,
    normalize_lyrics,
)

__all__ = [
    "MiniMaxMusic3TextEncoderConfig",
    "MiniMaxMusic3Tokenizer",
    "SPECIAL_TOKEN_IDS",
    "AUDIO_CODE_OFFSET",
    "AUDIO_END_TOKEN_ID",
    "SEMANTIC_VOCAB_SIZE",
    "MAX_PROMPT_TOKENS",
    "MAX_AUDIO_FRAMES",
    "build_prompt",
    "clean_caption",
    "normalize_lyrics",
    "MINIMAX_MUSIC3_DIT",
    "MiniMaxMusic3DitConfig",
    "MiniMaxMusic3Model",
    "chunk_starts",
    "crop_bounds",
    "denoise_windowed",
    "MiniMaxMusic3AudioLM",
    "GlobalLMKVCache",
    "DepthDecoderModule",
    "generate_depth_codes",
    "generate",
    "position_budget_warning",
    "FRAME_HIDDEN_SIZE",
]
