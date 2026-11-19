import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
from PIL import Image

from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.editor_tasks.repository import EditorRunRepository
from src.features.editor_tasks.runs import EditorRunManager
from src.features.media.upload_repository import UploadRepository
from tests.features.editor_tasks.run_support import Fakes, body
from tests.features.editor_tasks.support import BASE_FIELDS, load, write_preset

SCALE_FIELD = {
    "name": "upscale_factor",
    "type": "slider",
    "label": "Scale",
    "default": 2.0,
    "configuration": {"min": 1.5, "max": 4.0, "step": 0.5},
}
STRENGTH_FIELD = {
    "name": "denoise",
    "type": "slider",
    "label": "Denoise",
    "default": 0.5,
    "configuration": {"min": 0.0, "max": 1.0, "step": 0.05},
}
STRENGTH = {"field": "denoise", "default": 0.35, "min": 0.1, "max": 0.6}

UPSCALE = {
    "schema_version": 1,
    "task": "upscale",
    "from": "edit",
    "region": "whole",
    "inputs": {"source": "source_image", "scale": "upscale_factor"},
    "size": {"area": 262144},
}
FIX_FACES = {
    "schema_version": 1,
    "task": "fix_faces",
    "from": "edit",
    "region": "crop",
    "inputs": {"source": "source_image"},
    "strength": STRENGTH,
}
FIX_FACES_MASK = {
    "schema_version": 1,
    "task": "fix_faces",
    "from": "edit",
    "region": "mask",
    "inputs": {"source": "source_image", "mask": "source_image_inpaint_mask"},
}


def build_parts_manager(tmp_path, presets: Dict[str, Any]):
    fakes = Fakes(tmp_path)
    root = tmp_path / "presets"
    root.mkdir()
    for preset_id, editor in presets.items():
        write_preset(root, editor, fields=[*BASE_FIELDS, SCALE_FIELD, STRENGTH_FIELD], preset_id=preset_id)
    loader = load(root)
    tasks = EditorTaskManager(
        register_core_tasks(TaskRegistry()),
        lambda: loader.all(),
        lambda user: set(presets),
        lambda engine: True,
    )
    manager = EditorRunManager(
        EditorRunRepository(),
        tasks,
        EditManager(EditRepository(), UploadRepository()),
        fakes,
        fakes,
        fakes.submit,
        fakes.cancel,
        SimpleNamespace(get_by_id=fakes.get_by_id, get_files=fakes.get_files, update_origin=fakes.update_origin),
        SimpleNamespace(get_by_name=fakes.get_by_name),
        fakes,
    )
    return manager, fakes, tasks


def gradient(width: int, height: int) -> Image.Image:
    xs = np.linspace(0, 255, width, dtype=np.float32)[None, :].repeat(height, 0)
    ys = np.linspace(0, 255, height, dtype=np.float32)[:, None].repeat(width, 1)
    return Image.fromarray(np.dstack([xs, ys, (xs + ys) / 2]).astype(np.uint8))


def structured(width: int, height: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(40, 200, (height // 8 + 1, width // 8 + 1, 3), dtype=np.uint8)
    return np.asarray(Image.fromarray(base).resize((width, height), Image.Resampling.BILINEAR))


def regions_mask(size, boxes) -> Image.Image:
    array = np.zeros((size[1], size[0]), dtype=np.uint8)
    for top, bottom, left, right in boxes:
        array[top:bottom, left:right] = 255
    return Image.fromarray(array)


def upscale_body(edit_id: str, preset: str = "UP-SCALE", **extra):
    return body(edit_id, preset=preset, region=False, prompt=None, **extra)


async def settle_part(manager, fakes, generation_id: str, status: str = "completed", results=None, size=None):
    for index, pixels in enumerate(results or []):
        fakes.add_result(f"{generation_id}-{index}", size or (8, 8), generation_id, pixels)
    fakes.generation_by_id[generation_id] = SimpleNamespace(
        status=status, progress=1.0, error_user_message="The model failed" if status == "failed" else None
    )
    manager.on_generation_complete({"generation_id": generation_id, "status": status})


async def until(condition, limit: int = 600):
    for _ in range(limit):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition was never met")


def uploads_named(fakes, filename: str) -> List[Dict[str, Any]]:
    return [item for item in fakes.uploads if item["filename"] == filename]
