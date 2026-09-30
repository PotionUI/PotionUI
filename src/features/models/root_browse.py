import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.platform.filesystem.model_roots import InvalidRelPathError, validate_rel_path
from src.platform.filesystem.model_types import SUPPORTED_MODEL_EXTENSIONS

BROWSE_BUDGET_SECONDS = 3.0
MAX_SUB_DEPTH = 8
MAX_FOLDERS = 500
HINT_ENTRY_LIMIT = 2_000


class BrowseError(Exception):
    code = "model_roots_browse_failed"

    def __init__(self, reason: str, status: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass
class _Budget:
    clock: Callable[[], float]
    deadline: float

    def spent(self) -> bool:
        return self.clock() >= self.deadline


def _inside(base: str, target: str) -> bool:
    anchor = os.path.normcase(os.path.normpath(base))
    other = os.path.normcase(os.path.normpath(target))
    try:
        return os.path.commonpath([anchor, other]) == anchor
    except ValueError:
        return False


def _has_models(directory: str, budget: _Budget) -> bool:
    seen = 0
    try:
        for _dirpath, _dirs, files in os.walk(directory, followlinks=False):
            for name in files:
                seen += 1
                if os.path.splitext(name)[1].lower() in SUPPORTED_MODEL_EXTENSIONS:
                    return True
                if seen >= HINT_ENTRY_LIMIT:
                    return False
            if budget.spent():
                return False
    except OSError:
        return False
    return False


def _clean_sub(sub: Optional[str]) -> str:
    raw = (sub or "").replace("\\", "/")
    if raw.startswith("/"):
        raise BrowseError("The subfolder must be a relative path inside the folder.")
    raw = raw.rstrip("/")
    if not raw:
        return ""
    try:
        parts = validate_rel_path(raw).parts
    except InvalidRelPathError:
        raise BrowseError("The subfolder must be a relative path inside the folder.")
    if len(parts) > MAX_SUB_DEPTH:
        raise BrowseError(f"The subfolder is nested deeper than {MAX_SUB_DEPTH} levels.")
    return "/".join(parts)


def browse_subfolders(
    path: str, sub: Optional[str] = None, *, clock: Callable[[], float] = time.monotonic
) -> Dict[str, Any]:
    if not path or not path.strip():
        raise BrowseError("A folder path is required.")
    cleaned = _clean_sub(sub)
    budget = _Budget(clock=clock, deadline=clock() + BROWSE_BUDGET_SECONDS)

    root = Path(path.strip())
    try:
        real_root = os.path.realpath(str(root))
        if not os.path.isdir(real_root):
            raise BrowseError("That folder doesn't exist or isn't reachable from the server.", 404)
        target = os.path.realpath(os.path.join(real_root, cleaned)) if cleaned else real_root
    except OSError as error:
        raise BrowseError(f"Can't read that folder: {error}", 404)
    if not _inside(real_root, target):
        raise BrowseError("The subfolder leaves the folder through a link.")
    if not os.path.isdir(target):
        raise BrowseError("That subfolder doesn't exist.", 404)

    folders: List[Dict[str, Any]] = []
    truncated = False
    direct_files = False
    try:
        with os.scandir(target) as entries:
            names = []
            for entry in entries:
                if entry.is_dir():
                    if not entry.name.startswith("."):
                        names.append(entry.name)
                elif os.path.splitext(entry.name)[1].lower() in SUPPORTED_MODEL_EXTENSIONS:
                    direct_files = True
            names.sort()
    except OSError as error:
        raise BrowseError(f"Can't read that folder: {error}", 403)

    for name in names:
        if len(folders) >= MAX_FOLDERS or budget.spent():
            truncated = True
            break
        child = os.path.join(target, name)
        child_real = os.path.realpath(child)
        if not _inside(real_root, child_real):
            continue
        folders.append(
            {
                "name": name,
                "subdir": f"{cleaned}/{name}" if cleaned else name,
                "has_models": _has_models(child_real, budget),
                "linked": os.path.islink(child),
            }
        )

    parent: Optional[str] = None
    if cleaned:
        parent = cleaned.rsplit("/", 1)[0] if "/" in cleaned else ""

    return {
        "path": str(root),
        "sub": cleaned,
        "parent": parent,
        "folders": folders,
        "has_models": direct_files or any(f["has_models"] for f in folders),
        "truncated": truncated,
    }
