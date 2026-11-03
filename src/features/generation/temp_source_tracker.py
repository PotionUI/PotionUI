"""Per-generation registry of temp source files awaiting cleanup.

Video pipes write their raw output to `tempfile.NamedTemporaryFile(delete=False)`
paths (see `src/pipelines/pipes/generator/*/main.py`). The video/gallery output
handlers read those paths' bytes into `storage/` (once for a temporary preview,
again for the final non-temporary save) but never owned deleting the source --
nothing did, which is the leak. The source must survive until every
handler that reads it is done, so registration happens as each handler reads a
path, and the unlink happens once the owning generation reaches a terminal
state (`GenerationOrchestrator._finish_generation`).
"""

import logging
import os
import tempfile
from threading import Lock
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class TempSourceTracker:
    """Thread-safe per-generation set of temp file paths pending unlink."""

    def __init__(self):
        self._lock = Lock()
        self._paths: Dict[str, Set[str]] = {}
        self._storage_tmp: Dict[str, Dict[str, str]] = {}

    def register(self, generation_id: Optional[str], path: Optional[str]) -> None:
        """Record `path` as a temp source owned by `generation_id`.

        Silently ignores anything not resolving under the system temp
        directory -- this registry must never become a way to delete a
        `storage/` file.
        """
        if not generation_id or not path:
            return
        if not self._is_under_temp_dir(path):
            return
        with self._lock:
            self._paths.setdefault(generation_id, set()).add(path)

    def register_storage_tmp(self, generation_id: Optional[str], path: Optional[str], tmp_dir) -> None:
        if not generation_id or not path or not tmp_dir:
            return
        if not self._is_under(path, str(tmp_dir)):
            return
        with self._lock:
            self._storage_tmp.setdefault(generation_id, {})[path] = str(tmp_dir)

    def cleanup(self, generation_id: str) -> int:
        """Unlink every path registered for `generation_id` and forget them.

        Idempotent: a missing file is not an error (already removed, or never
        existed), just a debug log. Returns the number of files removed.
        """
        with self._lock:
            paths = self._paths.pop(generation_id, None) or set()
            storage_tmp = self._storage_tmp.pop(generation_id, None) or {}

        removed = 0
        for path in paths:
            if not self._is_under_temp_dir(path):
                logger.warning(f"Refusing to unlink non-temp-dir path {path}")
                continue
            removed += self._unlink(path)
        for path, tmp_dir in storage_tmp.items():
            if not self._is_under(path, tmp_dir):
                logger.warning(f"Refusing to unlink path outside the storage tmp dir {path}")
                continue
            removed += self._unlink(path)
        return removed

    @staticmethod
    def _unlink(path: str) -> int:
        try:
            os.remove(path)
            return 1
        except FileNotFoundError:
            logger.debug(f"Temp source already gone: {path}")
        except OSError as e:
            logger.debug(f"Failed to unlink temp source {path}: {e}")
        return 0

    @staticmethod
    def _is_under_temp_dir(path: str) -> bool:
        try:
            temp_dir = os.path.realpath(tempfile.gettempdir())
            resolved = os.path.realpath(path)
        except (TypeError, ValueError):
            return False
        return resolved == temp_dir or resolved.startswith(temp_dir + os.sep)

    @staticmethod
    def _is_under(path: str, root: str) -> bool:
        try:
            resolved_root = os.path.realpath(root)
            resolved = os.path.realpath(path)
        except (TypeError, ValueError):
            return False
        return resolved.startswith(resolved_root + os.sep)


temp_source_tracker = TempSourceTracker()
