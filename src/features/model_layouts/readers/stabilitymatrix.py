from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from src.features.model_layouts.readers.base import (
    ReaderContext,
    ReaderResult,
    find_child_ci,
    lower_keys,
    read_json_object,
)

DEFAULT_FILE = "settings.json"


def _library_dirs(ctx: ReaderContext, file: str, result: ReaderResult) -> List[Path]:
    dirs: List[Path] = []
    library_json = find_child_ci(ctx.install_dir, "library.json")
    if library_json is not None:
        data = read_json_object(library_json, result)
        if data is not None:
            raw = lower_keys(data).get("librarypath")
            resolved = ctx.resolve(raw, library_json.parent, result)
            if resolved:
                dirs.append(Path(resolved))
    for candidate in (ctx.install_dir, ctx.install_dir / "Data"):
        if find_child_ci(candidate, file) is not None and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def read(ctx: ReaderContext, file: Optional[str], result: ReaderResult) -> None:
    name = file or DEFAULT_FILE
    for library in _library_dirs(ctx, name, result):
        settings_path = find_child_ci(library, name)
        override = None
        if settings_path is not None:
            result.source_file = str(settings_path)
            data = read_json_object(settings_path, result)
            if data is not None:
                override = ctx.resolve(lower_keys(data).get("modeldirectoryoverride"), library, result)
        if override:
            ctx.set_primary(result, override)
            return
        models = find_child_ci(library, "Models")
        if models is not None:
            ctx.set_primary(result, str(models))
            return
