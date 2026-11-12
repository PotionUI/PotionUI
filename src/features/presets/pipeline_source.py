from pathlib import Path
from typing import Any, List, Tuple

from src.platform.filesystem.yaml_io import load_yaml_file

POINTER_KEY = "pipeline_from"


def pointer_problems(mode_dir: Path) -> List[str]:
    pipeline_file = mode_dir / "pipeline.yml"
    try:
        data = load_yaml_file(pipeline_file) or {}
    except Exception:
        return []
    if POINTER_KEY not in data:
        return []
    target = data[POINTER_KEY]
    if not isinstance(target, str) or not target.strip() or "/" in target or "\\" in target or target.startswith("."):
        return [f"{POINTER_KEY} must name a sibling mode directory, got {target!r}"]
    if len(data) > 1:
        return [f"{POINTER_KEY} cannot be combined with other keys ({sorted(k for k in data if k != POINTER_KEY)})"]
    target_file = mode_dir.parent / target / "pipeline.yml"
    if not target_file.exists():
        return [f"{POINTER_KEY}: modes/{target}/pipeline.yml not found"]
    if target == mode_dir.name:
        return [f"{POINTER_KEY}: a mode cannot point at itself"]
    try:
        target_data = load_yaml_file(target_file) or {}
    except Exception:
        return []
    if POINTER_KEY in target_data:
        return [f"{POINTER_KEY}: modes/{target} is itself a pointer; point at the mode that owns the pipeline"]
    return []


def resolve_mode_pipeline_file(mode_dir: Path) -> Path:
    pipeline_file = mode_dir / "pipeline.yml"
    if not pipeline_file.exists():
        return pipeline_file
    try:
        data = load_yaml_file(pipeline_file) or {}
    except Exception:
        return pipeline_file
    if POINTER_KEY not in data or pointer_problems(mode_dir):
        return pipeline_file
    return mode_dir.parent / data[POINTER_KEY] / "pipeline.yml"


def load_mode_pipeline(mode_dir: Path) -> Tuple[Any, List[str]]:
    pipeline_file = mode_dir / "pipeline.yml"
    data = load_yaml_file(pipeline_file) or {}
    if POINTER_KEY not in data:
        return data, []
    problems = pointer_problems(mode_dir)
    if problems:
        return None, problems
    return load_yaml_file(mode_dir.parent / data[POINTER_KEY] / "pipeline.yml") or {}, []
