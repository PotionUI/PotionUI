from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

import yaml

SHADER = "vec4 effect(vec4 color, vec2 uv) {\n    return vec4(color.rgb * (1.0 - u_k), color.a);\n}\n"
RENDER = (
    "import numpy as np\n\n\n"
    "def apply(batch, params, derived):\n"
    "    return np.floor(batch.frames * (1 - derived['k']) + 0.5).astype(np.uint8)\n\n\n"
    "def apply_torch(batch, params, derived):\n"
    "    return batch.tensor * (1 - derived['k'])\n"
)
BASE = {
    "id": "dim",
    "label": "Dim",
    "group": "colour",
    "description": "Darker picture",
    "accepts": ["video", "image"],
    "time": "none",
    "params": [{"name": "strength", "type": "float", "label": "Strength", "default": 50, "min": 0, "max": 100}],
    "derive": {"k": "strength / 100"},
    "preview": {"shader": "preview.frag"},
    "render": [{"numpy": "render.py:apply"}],
    "parity": {
        "media": {"width": 64, "height": 36, "fps": 24, "duration": 1},
        "cases": [{"params": {"strength": 20}, "frames": [0]}],
        "tolerance": {"max_abs": 1, "mean_abs": 0.1},
    },
}


def effect_data(**changes) -> dict:
    data = copy.deepcopy(BASE)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def write_effect(
    root: Path,
    name: str = "dim",
    data: Optional[dict] = None,
    shader: Optional[str] = SHADER,
    files: Optional[dict] = None,
) -> Path:
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    payload = data if data is not None else effect_data(id=name)
    (folder / "effect.yml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    if shader is not None:
        (folder / "preview.frag").write_text(shader, encoding="utf-8")
    for filename, text in {"render.py": RENDER, **(files or {})}.items():
        (folder / filename).write_text(text, encoding="utf-8")
    return folder
