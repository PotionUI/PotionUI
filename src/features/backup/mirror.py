"""Copying a media tree to a second location, incrementally.

A mirror is a plain directory tree, not an archive: the day directory a
generation lives in is the same on both sides, so a single file can be pulled
back by hand. Nothing at the destination is ever deleted - a backup that
propagates a deletion is not a backup.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from src.features.backup.paths import ANIMATED_THUMBNAIL_SUFFIX, is_excluded

COMPARE = "compare"
SKIP = "skip"


@dataclass
class MirrorStats:
    files_copied: int = 0
    bytes_copied: int = 0
    files_skipped: int = 0
    bytes_skipped: int = 0
    animated_skipped_files: int = 0
    animated_skipped_bytes: int = 0
    directories: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def merge(self, other: "MirrorStats") -> "MirrorStats":
        self.files_copied += other.files_copied
        self.bytes_copied += other.bytes_copied
        self.files_skipped += other.files_skipped
        self.bytes_skipped += other.bytes_skipped
        self.animated_skipped_files += other.animated_skipped_files
        self.animated_skipped_bytes += other.animated_skipped_bytes
        for name, counts in other.directories.items():
            entry = self.directories.setdefault(name, {"files": 0, "bytes": 0, "copied": 0})
            for key, value in counts.items():
                entry[key] += value
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "files_skipped": self.files_skipped,
            "bytes_skipped": self.bytes_skipped,
            "animated_skipped_files": self.animated_skipped_files,
            "animated_skipped_bytes": self.animated_skipped_bytes,
            "directories": self.directories,
        }


def _up_to_date(source: Path, destination: Path) -> bool:
    src = source.stat()
    dst = destination.stat()
    return src.st_size == dst.st_size and int(src.st_mtime) == int(dst.st_mtime)


def copy_atomic(source: Path, destination: Path) -> None:
    """Copy into the destination directory under a temporary name, then rename,
    so an interrupted run never leaves a half-written file a later run would
    accept as up to date."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / f".{destination.name}.part-{os.getpid()}"
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def mirror_tree(
    source: Path,
    destination: Path,
    *,
    include_animated: bool = False,
    if_exists: str = COMPARE,
    group_depth: int = 1,
    group_prefix: str = "",
) -> MirrorStats:
    """Mirror `source` into `destination`.

    `if_exists=COMPARE` recopies a file whose size or mtime differs; `SKIP`
    leaves any existing destination file alone. `group_depth=1` records what
    the mirror now holds under each top-level subdirectory - the generation day
    directory - counting files this run skipped as well as files it copied.
    """
    source = Path(source)
    destination = Path(destination)
    stats = MirrorStats()
    if not source.is_dir():
        return stats

    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(is_excluded(part) for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue

        size = path.stat().st_size
        if path.name.endswith(ANIMATED_THUMBNAIL_SUFFIX) and not include_animated:
            stats.animated_skipped_files += 1
            stats.animated_skipped_bytes += size
            continue

        target = destination / relative
        already_there = target.exists() and (if_exists == SKIP or _up_to_date(path, target))
        if already_there:
            stats.files_skipped += 1
            stats.bytes_skipped += size
        else:
            copy_atomic(path, target)
            stats.files_copied += 1
            stats.bytes_copied += size

        if group_depth:
            group = relative.parts[0] if len(relative.parts) > group_depth else "."
            entry = stats.directories.setdefault(
                f"{group_prefix}{group}", {"files": 0, "bytes": 0, "copied": 0}
            )
            entry["files"] += 1
            entry["bytes"] += size
            entry["copied"] += 0 if already_there else 1

    return stats
