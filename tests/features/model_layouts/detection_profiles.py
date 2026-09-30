from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.model_layouts.schema import ModelLayout, parse_layout


def _folder(path, model_type, **extra):
    return {"path": path, "model_type": model_type, **extra}


PROFILES: Dict[str, Dict[str, Any]] = {
    "comfyui": {
        "schema": 1,
        "id": "comfyui",
        "label": "ComfyUI",
        "priority": 60,
        "install_dirs": [".", "ComfyUI"],
        "markers": [{"path": "folder_paths.py", "kind": "file", "weight": 4}, {"path": "comfy", "kind": "dir", "weight": 2}],
        "min_marker_score": 4,
        "min_folder_evidence": 4,
        "models_root": ["models"],
        "config_readers": [{"kind": "comfyui_extra_model_paths", "file": "extra_model_paths.yaml"}],
        "folders": [
            _folder("checkpoints", "checkpoint", write=True),
            _folder("diffusion_models", "diffusion_model", write=True, scan_headers=True),
            _folder("unet", "diffusion_model", scan_headers=True),
            _folder("loras", "lora", write=True),
            _folder("vae", "vae", write=True),
            _folder("embeddings", "embedding", write=True),
            _folder("upscale_models", "upscaler", write=True),
            _folder("controlnet", "controlnet", write=True),
            _folder("text_encoders", "text_encoder", write=True),
            _folder("clip", "text_encoder"),
        ],
    },
    "a1111": {
        "schema": 1,
        "id": "a1111",
        "label": "A1111 / Forge",
        "priority": 50,
        "markers": [{"path": "webui.py", "kind": "file", "weight": 4}, {"path": "modules", "kind": "dir", "weight": 2}],
        "min_marker_score": 4,
        "min_folder_evidence": 4,
        "variants": [{"label": "Forge", "markers": [{"path": "modules_forge", "kind": "dir"}]}],
        "models_root": ["models"],
        "config_readers": [{"kind": "a1111_commandline_args"}],
        "folders": [
            _folder("Stable-diffusion", "checkpoint", write=True, scan_headers=True, weight=2),
            _folder("VAE", "vae", write=True),
            _folder("Lora", "lora", write=True),
            _folder("LyCORIS", "lora"),
            _folder("ESRGAN", "upscaler", write=True),
            _folder("RealESRGAN", "upscaler"),
            _folder("ControlNet", "controlnet", write=True),
            _folder("embeddings", "embedding", write=True, base="install"),
        ],
    },
    "stabilitymatrix": {
        "schema": 1,
        "id": "stabilitymatrix",
        "label": "StabilityMatrix",
        "priority": 55,
        "install_dirs": [".", "Data"],
        "markers": [{"path": ".sm-portable", "kind": "file", "weight": 4}, {"path": "Packages", "kind": "dir", "weight": 2}],
        "min_marker_score": 4,
        "models_root": ["Models"],
        "config_readers": [{"kind": "stabilitymatrix_settings_json"}],
        "folders": [
            _folder("StableDiffusion", "checkpoint", write=True, scan_headers=True),
            _folder("DiffusionModels", "diffusion_model", write=True, scan_headers=True),
            _folder("TextEncoders", "text_encoder", write=True),
            _folder("Lora", "lora", write=True),
            _folder("LyCORIS", "lora"),
            _folder("VAE", "vae", write=True),
            _folder("Embeddings", "embedding", write=True),
            _folder("ESRGAN", "upscaler", write=True),
            _folder("RealESRGAN", "upscaler"),
        ],
    },
    "fooocus": {
        "schema": 1,
        "id": "fooocus",
        "label": "Fooocus",
        "priority": 40,
        "markers": [{"path": "entry_with_update.py", "kind": "file", "weight": 4}],
        "min_marker_score": 4,
        "models_root": ["models"],
        "config_readers": [{"kind": "fooocus_config_txt"}],
        "folders": [
            _folder("checkpoints", "checkpoint", write=True),
            _folder("loras", "lora", write=True),
            _folder("embeddings", "embedding", write=True),
            _folder("vae", "vae", write=True),
        ],
    },
    "swarmui": {
        "schema": 1,
        "id": "swarmui",
        "label": "SwarmUI",
        "priority": 45,
        "markers": [{"path": "launch-linux.sh", "kind": "file", "weight": 4}, {"path": "src", "kind": "dir", "weight": 1}],
        "min_marker_score": 4,
        "models_root": ["Models"],
        "config_readers": [{"kind": "swarmui_settings_fds"}],
        "folders": [
            _folder("Stable-Diffusion", "checkpoint", write=True, scan_headers=True),
            _folder("Lora", "lora", write=True),
            _folder("VAE", "vae", write=True),
        ],
    },
    "pinokio": {
        "schema": 1,
        "id": "pinokio",
        "label": "Pinokio",
        "priority": 30,
        "markers": [{"path": "api", "kind": "dir", "weight": 2}, {"path": "drive", "kind": "dir", "weight": 2}],
        "min_marker_score": 4,
        "delegate": {"search": ["api/*/app", "api/*/*", "app", "*"], "max_candidates": 8},
    },
}


class FakeCatalog:
    def __init__(self, layouts: List[ModelLayout]):
        self._layouts = layouts

    def list_layouts(self):
        return list(self._layouts)


def catalog(*ids: str, extra: Optional[List[Dict[str, Any]]] = None) -> FakeCatalog:
    layouts = [parse_layout(PROFILES[i], source_path=f"{i}.yml") for i in (ids or PROFILES)]
    layouts += [parse_layout(data) for data in extra or []]
    return FakeCatalog(layouts)


def touch(base: Path, *relatives: str) -> None:
    for relative in relatives:
        target = base / relative
        if relative.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")


def model(base: Path, relative: str) -> None:
    touch(base, relative)
