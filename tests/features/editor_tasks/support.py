from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.features.presets.loader import PresetTemplateLoader

PRESET_ID = "EDITOR-TASKS-TEST"

IMAGE = {"name": "source_image", "type": "image", "label": "Picture", "configuration": {"allow_inpaint": True}}
PLAIN_IMAGE = {"name": "source_image", "type": "image", "label": "Picture"}
RESOLUTION = {"name": "resolution", "type": "resolution", "label": "Resolution", "default": "1024x1024"}
OUTPUT_SIZE = {
    "name": "output_size",
    "type": "select",
    "label": "Output size",
    "default": "match",
    "configuration": {"options": [{"label": "Match", "value": "match"}, {"label": "Custom", "value": "custom"}]},
}
MATCH = {"name": "match_source", "type": "checkbox", "label": "Match", "default": True}
SEED = {"name": "seed", "type": "seed", "label": "Seed", "default": -1}
QUANTITY = {"name": "quantity", "type": "stepper", "label": "Quantity", "default": 1, "configuration": {"min": 1, "max": 4, "step": 1}}
MEDIA = {"name": "references", "type": "media", "label": "Pictures", "configuration": {"multi": True}}

BASE_FIELDS: List[Dict[str, Any]] = [IMAGE, RESOLUTION, OUTPUT_SIZE, MATCH, SEED, QUANTITY, MEDIA]

CROP = {"schema_version": 1, "tasks": [{"task": "edit_region", "region": "crop", "inputs": {"image": "source_image"}}]}
MASK = {
    "schema_version": 1,
    "tasks": [
        {
            "task": "edit_region",
            "region": "mask",
            "inputs": {"image": "source_image", "mask": "source_image_inpaint_mask"},
        }
    ],
}


def task(**changes: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"task": "edit_region", "region": "crop", "inputs": {"image": "source_image"}}
    entry.update(changes)
    return entry


def declaration(*tasks: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema_version": 1, "tasks": list(tasks)}


def write_preset(
    root: Path,
    editor: Any = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    preset_id: str = PRESET_ID,
    mode: str = "edit",
    extra_manifest: Optional[Dict[str, Any]] = None,
) -> Path:
    preset_dir = root / preset_id
    mode_dir = preset_dir / "modes" / mode
    mode_dir.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "id": preset_id,
        "name": f"Preset {preset_id}",
        "version": "1.0.0",
        "category": "image",
        "engine": "native",
        "modes": [mode],
    }
    manifest.update(extra_manifest or {})
    (preset_dir / "preset.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    pipeline = {
        "pipeline": [
            {"name": "generator", "id": "generator", "configuration": {"quantity": "{{ form.quantity }}"}},
            {"name": "gallery", "input": [["image", "generator", "image"], ["seed", "seed_generator", "seed"]]},
        ]
    }
    (mode_dir / "pipeline.yml").write_text(yaml.safe_dump(pipeline), encoding="utf-8")
    form = {
        "name": "custom",
        "fields": [{"type": "tabs", "children": [{"type": "tab", "anchor": "main", "label": "Main", "children": fields or BASE_FIELDS}]}],
    }
    (mode_dir / "form.yml").write_text(yaml.safe_dump(form), encoding="utf-8")
    if editor is not None:
        text = editor if isinstance(editor, str) else yaml.safe_dump(editor)
        (mode_dir / "editor.yml").write_text(text, encoding="utf-8")
    return preset_dir


def load(*roots: Path) -> PresetTemplateLoader:
    loader = PresetTemplateLoader([str(root) for root in roots])
    loader.load_presets()
    return loader
