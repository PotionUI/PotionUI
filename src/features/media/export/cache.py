from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from src.platform.filesystem.storage_driver import _replace

DEFAULT_MAX_BYTES = 5 * 1024 ** 3
STALE_PART_SECONDS = 24 * 3600
PART_SUFFIX = ".part"


def cache_key(source: Path, options: dict) -> str:
    resolved = Path(source).resolve()
    stat = resolved.stat()
    payload = json.dumps(
        {"src": str(resolved), "mtime": stat.st_mtime_ns, "size": stat.st_size, "opts": options},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExportCache:
    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = Path(root)
        self.max_bytes = int(max_bytes)

    def final_path(self, key: str, extension: str) -> Path:
        return self.root / key[:2] / f"{key}.{extension}"

    def part_path(self, key: str, extension: str) -> Path:
        final = self.final_path(key, extension)
        final.parent.mkdir(parents=True, exist_ok=True)
        return final.with_name(f"{final.name}.{os.getpid()}.{time.monotonic_ns()}{PART_SUFFIX}")

    def get(self, key: str, extension: str) -> Optional[Path]:
        path = self.final_path(key, extension)
        if not path.is_file():
            return None
        try:
            os.utime(path, None)
        except OSError:
            pass
        return path

    def commit(self, part: Path, key: str, extension: str) -> Path:
        final = self.final_path(key, extension)
        final.parent.mkdir(parents=True, exist_ok=True)
        _replace(Path(part), final)
        self.evict(keep=final)
        return final

    def discard(self, part: Path) -> None:
        try:
            Path(part).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def total_bytes(self) -> int:
        return sum(size for _, size, _ in self._entries())

    def evict(self, keep: Optional[Path] = None) -> int:
        entries = self._entries()
        now = time.time()
        removed = 0
        finals = []
        total = 0
        for path, size, mtime in entries:
            if path.name.endswith(PART_SUFFIX):
                if now - mtime > STALE_PART_SECONDS:
                    self.discard(path)
                continue
            finals.append((mtime, size, path))
            total += size
        finals.sort(key=lambda item: item[0])
        for _, size, path in finals:
            if total <= self.max_bytes:
                break
            if keep is not None and path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def clear(self) -> None:
        for path, _, _ in self._entries():
            self.discard(path)

    def _entries(self) -> list[tuple[Path, int, float]]:
        if not self.root.is_dir():
            return []
        entries = []
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    stat = path.stat()
                    entries.append((path, stat.st_size, stat.st_mtime))
            except OSError:
                continue
        return entries
