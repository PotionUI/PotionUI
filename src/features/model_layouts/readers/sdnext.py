from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    read_json_object,
)

DEFAULT_FILE = "config.json"

KEY_TO_MODEL_TYPE = {
    "ckpt_dir": "checkpoint",
    "vae_dir": "vae",
    "unet_dir": "diffusion_model",
    "te_dir": "text_encoder",
    "lora_dir": "lora",
    "embeddings_dir": "embedding",
    "esrgan_models_path": "upscaler",
    "realesrgan_models_path": "upscaler",
}


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    path = find_child_ci(ctx.install_dir, file or DEFAULT_FILE)
    if path is None:
        return
    result.source_file = str(path)
    config = read_json_object(path, result)
    if config is None:
        return
    models_dir = ctx.resolve(config.get("models_dir"), ctx.install_dir, result)
    if models_dir:
        ctx.set_primary(result, models_dir)
    for key, model_type in KEY_TO_MODEL_TYPE.items():
        resolved = ctx.resolve(config.get(key), ctx.install_dir, result)
        if resolved:
            ctx.add_entry(result, model_type, resolved, key)
