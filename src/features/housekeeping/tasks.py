"""The pruning passes the housekeeping worker runs.

Each is a plain function over what it needs and returns a dict the API can
serialise straight through. A retention window of 0 disables its pass; the
result then reports nothing removed rather than an error.
"""

import logging
import os
import stat
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

logger = logging.getLogger(__name__)

MAX_RECORDED_ERRORS = 20
SECONDS_PER_DAY = 86400


def _stale_files(root: Path, cutoff: float) -> Iterator[Tuple[Path, int]]:
    """Every regular file under `root` last modified before `cutoff`, with its
    size. Symlinks are skipped rather than followed, and a path that does not
    resolve back inside `root` is skipped too, so nothing outside the scratch
    folder is ever a candidate."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_mtime >= cutoff:
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if root not in resolved.parents:
                continue
            yield path, info.st_size


def scan_tmp(tmp_dir: str, retention_days: int) -> Dict[str, int]:
    """What `prune_tmp` would remove right now, without removing it."""
    if retention_days <= 0:
        return {"files": 0, "bytes": 0}

    root = Path(tmp_dir).resolve()
    if not root.is_dir():
        return {"files": 0, "bytes": 0}

    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    files = 0
    total = 0
    for _, size in _stale_files(root, cutoff):
        files += 1
        total += size
    return {"files": files, "bytes": total}


def prune_tmp(tmp_dir: str, retention_days: int) -> Dict[str, Any]:
    """Delete scratch files older than `retention_days`, then the directories
    that emptying them left behind."""
    result: Dict[str, Any] = {"removed": 0, "bytes_freed": 0, "errors": []}
    if retention_days <= 0:
        return result

    root = Path(tmp_dir).resolve()
    if not root.is_dir():
        return result

    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    errors: List[str] = result["errors"]
    for path, size in list(_stale_files(root, cutoff)):
        try:
            path.unlink()
        except OSError as e:
            logger.warning("Could not remove scratch file %s: %s", path, e)
            if len(errors) < MAX_RECORDED_ERRORS:
                errors.append(f"{path.name}: {e}")
            continue
        result["removed"] += 1
        result["bytes_freed"] += size

    _remove_empty_directories(root)
    return result


def _remove_empty_directories(root: Path) -> None:
    for dirpath, _, _ in os.walk(root, topdown=False, followlinks=False):
        directory = Path(dirpath)
        if directory == root:
            continue
        try:
            directory.rmdir()
        except OSError:
            continue


def prune_run_reports(repository, recorder, retention_days: int) -> Dict[str, Any]:
    """Delete run reports older than `retention_days`. The removal goes
    through the recorder rather than the table, so the artifact bytes a report
    references are removed with the row that names them."""
    result: Dict[str, Any] = {"rows_removed": 0, "errors": []}
    if retention_days <= 0:
        return result

    errors: List[str] = result["errors"]
    for generation_id in repository.list_older_than(retention_days):
        try:
            if recorder.delete_report(generation_id):
                result["rows_removed"] += 1
        except Exception as e:
            logger.warning("Could not remove run report %s: %s", generation_id, e)
            if len(errors) < MAX_RECORDED_ERRORS:
                errors.append(f"{generation_id}: {e}")
    return result


def prune_llm_traces(repository, retention_days: int) -> Dict[str, Any]:
    """Delete chat LLM call traces older than `retention_days`."""
    result: Dict[str, Any] = {"rows_removed": 0, "errors": []}
    if retention_days <= 0:
        return result
    try:
        result["rows_removed"] = repository.prune_older_than(retention_days)
    except Exception as e:
        logger.warning("Could not prune chat LLM call traces: %s", e)
        result["errors"].append(str(e))
    return result
