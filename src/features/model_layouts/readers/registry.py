from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from src.features.model_layouts.readers import (
    comfyui,
    fooocus,
    sdnext,
    stabilitymatrix,
    swarmui,
    webui_args,
)
from src.features.model_layouts.readers.base import ConfigReader, ReaderContext, ReaderResult
from src.features.model_layouts.wsl import PathTranslator

READERS: Dict[str, ConfigReader] = {
    "comfyui_extra_model_paths": comfyui.read,
    "fooocus_config_txt": fooocus.read,
    "swarmui_settings_fds": swarmui.read,
    "stabilitymatrix_settings_json": stabilitymatrix.read,
    "sdnext_config_json": sdnext.read,
    "a1111_commandline_args": webui_args.read,
}


def reader_kinds() -> Tuple[str, ...]:
    return tuple(sorted(READERS))


def run_reader(
    kind: str,
    install_dir: Path,
    *,
    root: Optional[Union[str, Path]] = None,
    file: Optional[str] = None,
    translator: Optional[PathTranslator] = None,
) -> ReaderResult:
    result = ReaderResult(kind=kind)
    reader = READERS.get(kind)
    if reader is None:
        result.warnings.append(f"Unknown config reader '{kind}'")
        return result
    try:
        reader(ReaderContext(install_dir, root, translator), file, result)
    except Exception as exc:
        result.entries.clear()
        result.primary_root = None
        result.primary_outside_root = False
        result.extra_roots.clear()
        result.warnings.append(f"Could not read {kind}: {exc}")
    return result
