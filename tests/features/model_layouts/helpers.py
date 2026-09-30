import copy
from pathlib import Path
from typing import Any, Dict

import yaml

BASE_LAYOUT: Dict[str, Any] = {
    "schema": 1,
    "id": "tool",
    "label": "Tool",
    "priority": 50,
    "install_dirs": [".", "Tool"],
    "markers": [
        {"path": "tool.py", "kind": "file", "weight": 4},
        {"path": "core", "kind": "dir", "weight": 2},
    ],
    "min_marker_score": 4,
    "models_root": ["models"],
    "config_readers": [{"kind": "comfyui_extra_model_paths", "file": "extra_model_paths.yaml"}],
    "folders": [
        {"path": "checkpoints", "model_type": "checkpoint", "write": True, "scan_headers": True},
        {"path": "loras", "model_type": "lora", "write": True},
        {"path": "LyCORIS", "model_type": "lora"},
    ],
}


def layout_data(**overrides: Any) -> Dict[str, Any]:
    data = copy.deepcopy(BASE_LAYOUT)
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def write_layout(root: Path, layout_id: str = "tool", **overrides: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{layout_id}.yml"
    path.write_text(yaml.safe_dump(layout_data(id=layout_id, **overrides)), encoding="utf-8")
    return path
