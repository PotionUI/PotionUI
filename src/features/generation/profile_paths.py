"""On-disk layout for per-generation resource profiles.

Single source of truth for *where* a generation's profiler artifacts live, so
the writer (``GenerationEngine``, which calls ``profiler.start``), the read
endpoint (``GenerationController.get_generation_profile``) and the
``has_profile`` availability flag (``GenerationHistoryQuery``) all agree.

Profiles are written under ``<storage>/profiles/<generation_id>/`` as
``profile.jsonl`` plus the ``generation.log`` cut (see
``src.platform.observability.profiling``). ``generation_id`` is treated as a
single path segment: a value containing a separator or ``..`` never resolves to
a directory (returns ``None``) so a client id can't escape the profiles root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from src.platform.observability.profiling import LOG_FILENAME

PROFILES_SUBDIR = "profiles"
PROFILE_JSONL = "profile.jsonl"


def _is_safe_segment(generation_id: str) -> bool:
    return bool(generation_id) and not (
        "/" in generation_id or "\\" in generation_id or ".." in generation_id
    )


def profiles_root(storage_dir: Union[str, Path]) -> Path:
    """The directory holding every generation's profile subdir."""
    return Path(storage_dir) / PROFILES_SUBDIR


def profile_dir(storage_dir: Union[str, Path], generation_id: str) -> Optional[Path]:
    """The profile directory for one generation, or ``None`` if the id is not a
    safe single path segment."""
    if not _is_safe_segment(generation_id):
        return None
    return profiles_root(storage_dir) / generation_id


def profile_jsonl_path(storage_dir: Union[str, Path], generation_id: str) -> Optional[Path]:
    """Path to the ``profile.jsonl`` for a generation (``None`` on an unsafe id).
    The file may not exist -- callers check ``is_file()``."""
    d = profile_dir(storage_dir, generation_id)
    return d / PROFILE_JSONL if d is not None else None


def profile_log_path(storage_dir: Union[str, Path], generation_id: str) -> Optional[Path]:
    """Path to the ``generation.log`` cut for a generation (``None`` on an unsafe id)."""
    d = profile_dir(storage_dir, generation_id)
    return d / LOG_FILENAME if d is not None else None


def has_profile(storage_dir: Union[str, Path], generation_id: str) -> bool:
    """Whether a ``profile.jsonl`` exists on disk for this generation."""
    p = profile_jsonl_path(storage_dir, generation_id)
    return p is not None and p.is_file()
