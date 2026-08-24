"""Native text encoders (Klein/Qwen3, Flux1 T5-XXL + CLIP-L), fully offline.

Public surface:
    NativeTextEncoder    - the encode() -> role-keyed dict interface (see base.py)
    load_text_encoder    - loader entry (single file, or T5+CLIP-L pair for Flux1)
    Qwen3TextEncoder / T5XXLTextEncoder / CLIPLTextEncoder / FluxTextEncoder
"""

from __future__ import annotations

from .base import NativeTextEncoder
from .clip_l import CLIPLModel, CLIPLTextEncoder
from .embed_cache import PromptEmbedCache, get_prompt_embed_cache, image_content_fingerprint, prompt_embed_key
from .gemma3 import Gemma3Model, Gemma3TextEncoder
from .loader import FluxTextEncoder, TESpec, load_text_encoder
from .qwen3 import KLEIN_LAYERS, Qwen3Model, Qwen3TextEncoder, ZImageTextEncoder
from .qwen25_vl import Qwen25VLModel, Qwen25VLTextEncoder
from .qwen_vl_vision import Qwen2VLVisionTower, preprocess_qwen_vl_image, qwen25vl_mrope_position_ids
from .t5xxl import T5XXLModel, T5XXLTextEncoder, UMT5TextEncoder

__all__ = [
    "NativeTextEncoder",
    "PromptEmbedCache",
    "get_prompt_embed_cache",
    "prompt_embed_key",
    "image_content_fingerprint",
    "load_text_encoder",
    "TESpec",
    "Qwen3Model",
    "Qwen3TextEncoder",
    "ZImageTextEncoder",
    "KLEIN_LAYERS",
    "Qwen25VLModel",
    "Qwen25VLTextEncoder",
    "Qwen2VLVisionTower",
    "preprocess_qwen_vl_image",
    "qwen25vl_mrope_position_ids",
    "Gemma3Model",
    "Gemma3TextEncoder",
    "T5XXLModel",
    "T5XXLTextEncoder",
    "UMT5TextEncoder",
    "CLIPLModel",
    "CLIPLTextEncoder",
    "FluxTextEncoder",
]
