from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src.platform.imaging.filters.cube import Cube, CubeError, read_cube
from src.platform.imaging.filters.lut import bake_cube, mix_cube, serialize_cube

_SAFE_RELATIVE = re.compile(r"^(?:\.\./)*[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")


class AssetError(ValueError):
    pass


@lru_cache(maxsize=16)
def _baked(path: str, mtime_ns: int, size: int) -> Cube:
    try:
        return bake_cube(read_cube(path, allow_shaper=True))
    except CubeError as exc:
        raise AssetError(f"{Path(path).name}: {exc}") from None


@lru_cache(maxsize=32)
def _mixed(path: str, mtime_ns: int, size: int, strength: float) -> Cube:
    baked = _baked(path, mtime_ns, size)
    return baked if strength >= 1 else mix_cube(baked, strength)


def load_lut(path: Path, strength: float = 1.0) -> Cube:
    stat = Path(path).stat()
    return _mixed(str(path), stat.st_mtime_ns, stat.st_size, round(float(strength), 6))


def stage_cube(cube: Cube, folder: Path) -> Optional[str]:
    text = serialize_cube(cube)
    name = f"lut-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}.cube"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not target.is_file():
        target.write_text(text, encoding="utf-8")
    try:
        relative = os.path.relpath(target.resolve(), Path.cwd().resolve()).replace(os.sep, "/")
    except ValueError:
        return None
    return relative if _SAFE_RELATIVE.match(relative) else None
