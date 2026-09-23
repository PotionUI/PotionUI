import io
from typing import Any, Dict, List, Optional

from PIL import Image
from PIL.PngImagePlugin import PngInfo

_CHECKPOINT_MODEL_TYPES = ("checkpoint", "diffusion_model")
_LORA_MODEL_TYPE = "lora"

_HASH_PREFIX_LEN = 10


def _short_hash(sha256: Optional[str]) -> Optional[str]:
    if not sha256:
        return None
    return sha256[:_HASH_PREFIX_LEN]


def _usable_hash(model: Dict[str, Any]) -> Optional[str]:
    if model.get("is_directory"):
        return None
    return _short_hash(model.get("sha256"))


def _model_label(model: Dict[str, Any]) -> str:
    return model.get("name") or model.get("filename") or model.get("id") or ""


def _checkpoint_model(models: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for model in models:
        if model.get("model_type") in _CHECKPOINT_MODEL_TYPES:
            return model
    return None


def _lora_models(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in models if m.get("model_type") == _LORA_MODEL_TYPE]


def _size_from_resolution(resolution: Any) -> Optional[str]:
    if isinstance(resolution, str) and resolution:
        return resolution
    if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
        width, height = resolution
        return f"{width}x{height}"
    return None


def build_a1111_parameters(parameters: Dict[str, Any], models: List[Dict[str, Any]]) -> str:
    lines: List[str] = [parameters.get("positive_prompt") or ""]

    negative_prompt = parameters.get("negative_prompt")
    if negative_prompt:
        lines.append(f"Negative prompt: {negative_prompt}")

    kv_pairs: List[str] = []

    steps = parameters.get("steps")
    if steps is not None:
        kv_pairs.append(f"Steps: {steps}")

    sampler = parameters.get("sampler")
    if sampler:
        kv_pairs.append(f"Sampler: {sampler}")

    cfg = parameters.get("cfg")
    if cfg is not None:
        kv_pairs.append(f"CFG scale: {cfg}")

    seed = parameters.get("seed")
    if seed is not None:
        kv_pairs.append(f"Seed: {seed}")

    size = _size_from_resolution(parameters.get("resolution"))
    if size:
        kv_pairs.append(f"Size: {size}")

    checkpoint = _checkpoint_model(models)
    if checkpoint:
        checkpoint_hash = _usable_hash(checkpoint)
        if checkpoint_hash:
            kv_pairs.append(f"Model hash: {checkpoint_hash}")
        checkpoint_name = _model_label(checkpoint)
        if checkpoint_name:
            kv_pairs.append(f"Model: {checkpoint_name}")

    lora_entries = [
        f"{_model_label(lora)}: {_usable_hash(lora)}"
        for lora in _lora_models(models)
        if _usable_hash(lora)
    ]
    if lora_entries:
        kv_pairs.append('Lora hashes: "' + ", ".join(lora_entries) + '"')

    if kv_pairs:
        lines.append(", ".join(kv_pairs))

    return "\n".join(lines)


def inject_a1111_parameters(png_bytes: bytes, parameters_text: str) -> bytes:
    with Image.open(io.BytesIO(png_bytes)) as image:
        image.load()
        png_info = PngInfo()
        png_info.add_text("parameters", parameters_text)

        out = io.BytesIO()
        image.save(out, format="PNG", pnginfo=png_info)
        return out.getvalue()
