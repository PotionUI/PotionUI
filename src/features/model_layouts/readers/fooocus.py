from __future__ import annotations

from typing import Optional

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    read_json_object,
)

DEFAULT_FILE = "config.txt"

KEY_TO_MODEL_TYPE = {
    "path_checkpoints": "checkpoint",
    "path_loras": "lora",
    "path_embeddings": "embedding",
    "path_vae": "vae",
    "path_upscale_models": "upscaler",
    "path_controlnet": "controlnet",
}


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    path = find_child_ci(ctx.install_dir, file or DEFAULT_FILE)
    if path is None:
        return
    result.source_file = str(path)
    config = read_json_object(path, result)
    if config is None:
        return
    for key, model_type in KEY_TO_MODEL_TYPE.items():
        if key not in config:
            continue
        value = config[key]
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(item, str) for item in values):
            result.warnings.append(f"{key} must be a string or a list of strings")
            continue
        resolved = [ctx.resolve(item, ctx.install_dir, result) for item in values]
        if any(item is None for item in resolved) or not all(ctx.translator.is_dir(item) for item in resolved):
            result.warnings.append(
                f"{key} points at a folder that does not exist; Fooocus falls back to its default folder"
            )
            continue
        for item in resolved:
            ctx.add_entry(result, model_type, item, key)
    if result.entries:
        result.warnings.append(
            "Fooocus also reads path environment variables, which override config.txt and cannot be seen here"
        )
