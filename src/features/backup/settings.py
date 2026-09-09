"""The three settings that decide where backups go and how many are kept.

Stdlib-only like the rest of the package: `potionui backup` resolves its
default `--out` through here on a bare interpreter, so the admin panel and the
command line agree on the destination without either owning it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.features.backup.archive import TIERS
from src.features.backup.paths import database_path, read_setting

SETTING_DESTINATION = "backup_destination"
SETTING_RETENTION = "backup_retention"
SETTING_DEFAULT_TIER = "backup_default_tier"

DEFAULT_DESTINATION = "backups"
DEFAULT_RETENTION = 7
DEFAULT_TIER = "config"

MIN_RETENTION = 0
MAX_RETENTION = 365

PROBE_FILENAME = ".potionui_write_test"

DEFAULTS: Dict[str, Any] = {
    SETTING_DESTINATION: DEFAULT_DESTINATION,
    SETTING_RETENTION: DEFAULT_RETENTION,
    SETTING_DEFAULT_TIER: DEFAULT_TIER,
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_destination(root: Path, value: str) -> Path:
    path = Path(str(value).strip()).expanduser()
    return path if path.is_absolute() else (Path(root) / path)


def is_writable_dir(path: Path) -> bool:
    """Create the directory and prove a file can be written into it - the same
    test the `STORAGE` doctor check makes."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / PROBE_FILENAME
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def destination_state(path: Path) -> Tuple[bool, bool]:
    """Whether the destination is there, and whether a backup could write into
    it - without creating anything. A directory that is not there yet counts as
    writable when the nearest directory above it is: the run creates it."""
    path = Path(path)
    if path.exists():
        return True, path.is_dir() and os.access(path, os.W_OK | os.X_OK)
    for ancestor in path.parents:
        if ancestor.exists():
            return False, ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
    return False, False


def destination_reason(root: Path, value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return "must name a directory"
    target = resolve_destination(root, text)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"{target} could not be created ({exc.strerror or exc})"
    if not is_writable_dir(target):
        return f"{target} is not writable"
    return None


def validate_setting(key: str, value: Any, root: Optional[Path] = None) -> Optional[str]:
    """The reason `value` is not acceptable for backup setting `key`, or None.
    Returns None for any key this module does not own."""
    if key == SETTING_DESTINATION:
        return destination_reason(root or repo_root(), value)

    if key == SETTING_RETENTION:
        if isinstance(value, bool):
            return f"must be a whole number between {MIN_RETENTION} and {MAX_RETENTION}"
        try:
            keep = int(value)
        except (TypeError, ValueError):
            return f"must be a whole number between {MIN_RETENTION} and {MAX_RETENTION}"
        if not MIN_RETENTION <= keep <= MAX_RETENTION:
            return f"must be between {MIN_RETENTION} and {MAX_RETENTION}"
        return None

    if key == SETTING_DEFAULT_TIER:
        if str(value) not in TIERS:
            return f"must be one of {', '.join(TIERS)}"
        return None

    return None


def _retention(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_RETENTION
    try:
        keep = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION
    return max(MIN_RETENTION, min(MAX_RETENTION, keep))


def load_backup_settings(settings) -> Dict[str, Any]:
    """Every backup setting, clamped to what the API accepts."""
    destination = str(settings.get_setting(SETTING_DESTINATION, DEFAULT_DESTINATION) or "").strip()
    tier = str(settings.get_setting(SETTING_DEFAULT_TIER, DEFAULT_TIER))
    return {
        "destination": destination or DEFAULT_DESTINATION,
        "retention": _retention(settings.get_setting(SETTING_RETENTION, DEFAULT_RETENTION)),
        "default_tier": tier if tier in TIERS else DEFAULT_TIER,
    }


def destination_dir(root: Optional[Path] = None) -> Path:
    """Where a backup goes when nothing on the command line says otherwise:
    the stored setting when this checkout has a database, else the default."""
    root = Path(root or repo_root())
    stored = read_setting(database_path(root), SETTING_DESTINATION, DEFAULT_DESTINATION)
    return resolve_destination(root, stored or DEFAULT_DESTINATION)
