"""Filesystem helpers shared by the local-weights lazy loaders (tagger,
vision embedder, prompt-database embedding provider)."""

from pathlib import Path
from typing import Dict, Optional


def dir_size(path: Path) -> Optional[int]:
    """Total bytes under `path`, or None if it doesn't exist / holds nothing."""
    if not path.is_dir():
        return None
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total if total > 0 else None


def weights_status(path: Path) -> Dict[str, object]:
    """Presence/path/size for a local weights directory without loading
    anything. `present` means "the directory exists and is non-empty" - a
    caller that needs a stricter presence check (e.g. specific required
    files) computes `present` itself rather than using this."""
    present = path.is_dir() and any(path.iterdir())
    return {"present": present, "path": str(path), "size": dir_size(path) if present else None}
