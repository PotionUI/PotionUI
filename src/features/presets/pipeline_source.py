from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.platform.filesystem.yaml_io import load_yaml_file

POINTER_KEY = "pipeline_from"


def _read(pipeline_file: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        data = load_yaml_file(pipeline_file) or {}
    except Exception as error:
        return None, f"failed to parse: {error}"
    return (data, None) if isinstance(data, dict) else ({}, None)


def _problems(mode_dir: Path, data: Dict[str, Any]) -> List[str]:
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
    target_data, error = _read(target_file)
    if error:
        return [f"{POINTER_KEY}: modes/{target}/pipeline.yml {error}"]
    if POINTER_KEY in target_data:
        return [f"{POINTER_KEY}: modes/{target} is itself a pointer; point at the mode that owns the pipeline"]
    return []


def pointer_problems(mode_dir: Path) -> List[str]:
    data, error = _read(mode_dir / "pipeline.yml")
    if error:
        return [error]
    return _problems(mode_dir, data)


def resolve_mode_pipeline_file(mode_dir: Path) -> Path:
    pipeline_file = mode_dir / "pipeline.yml"
    data, error = _read(pipeline_file) if pipeline_file.exists() else (None, None)
    if error or data is None or POINTER_KEY not in data or _problems(mode_dir, data):
        return pipeline_file
    return mode_dir.parent / data[POINTER_KEY] / "pipeline.yml"


def load_mode_pipeline(mode_dir: Path) -> Tuple[Any, List[str]]:
    data, error = _read(mode_dir / "pipeline.yml")
    if error:
        return None, [error]
    if POINTER_KEY not in data:
        return data, []
    problems = _problems(mode_dir, data)
    if problems:
        return None, problems
    target_data, error = _read(mode_dir.parent / data[POINTER_KEY] / "pipeline.yml")
    return (None, [error]) if error else (target_data, [])
