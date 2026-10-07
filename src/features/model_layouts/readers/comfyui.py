from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    read_bounded_text,
)

DEFAULT_FILE = "extra_model_paths.yaml"

KEY_TO_MODEL_TYPE = {
    "checkpoints": "checkpoint",
    "diffusion_models": "diffusion_model",
    "unet": "diffusion_model",
    "text_encoders": "text_encoder",
    "clip": "text_encoder",
    "loras": "lora",
    "vae": "vae",
    "embeddings": "embedding",
    "upscale_models": "upscaler",
    "controlnet": "controlnet",
    "t2i_adapter": "controlnet",
    "ultralytics_bbox": "detection_bbox",
    "ultralytics_segm": "detection_segm",
    "geometry_estimation": "geometry_estimation",
}


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    path = find_child_ci(ctx.install_dir, file or DEFAULT_FILE)
    if path is None:
        return
    result.source_file = str(path)
    text = read_bounded_text(path, result)
    if text is None:
        return
    try:
        config = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        result.warnings.append(f"{path.name} is not valid YAML: {exc}")
        return
    if config is None:
        return
    if not isinstance(config, dict):
        result.warnings.append(f"{path.name} must be a mapping of sections")
        return
    yaml_dir = Path(path).parent
    for section, conf in config.items():
        if not isinstance(conf, dict):
            continue
        section_name = str(section)
        base_path = None
        if isinstance(conf.get("base_path"), str):
            base_path = ctx.resolve(conf["base_path"], yaml_dir, result)
            if base_path is None:
                continue
        is_default = conf.get("is_default") is True
        for key, value in conf.items():
            if key in ("base_path", "is_default"):
                continue
            model_type = KEY_TO_MODEL_TYPE.get(str(key))
            if model_type is None:
                continue
            if isinstance(value, str):
                lines = value.split("\n")
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                lines = value
            else:
                result.warnings.append(f"{section_name}.{key} must be a string or a list of strings")
                continue
            for line in lines:
                if not line.strip():
                    continue
                resolved = ctx.resolve(line, base_path if base_path else yaml_dir, result)
                if resolved:
                    ctx.add_entry(result, model_type, resolved, str(key), section_name, is_default)
