"""Where PotionUI's own state lives, and which parts of it are worth keeping.

An allowlist, not a filter: anything not named here is not backed up. The
exclusion set below is a second line of defence for names that can appear
*inside* an included tree.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Tuple

from src.features.backup.repository import setting_value

DB_FILENAME = "db.sqlite"
SECRET_KEY_FILENAME = "secret.key"
ENV_FILENAME = ".env"

CONTENT_TREES = (
    "content/presets/local",
    "content/plugins/local",
    "content/automation/local",
)

STORAGE_CONFIG_FILES = (
    "llm.yml",
    "saved_prompts.json",
    "segment_categories.json",
    "segment_templates.json",
)

STORAGE_SMALL_TREES = (
    "avatars",
    "profiles",
    "preset_media",
    "inspirations",
    "lora-dataset",
    "spritesheet",
    "video-editor",
    "remote_imports",
)

STORAGE_MEDIA_TREES = ("uploads", "generations")

# Caches, scratch space, logs, and the settings file the settings table
# superseded: restoring any of these puts stale state back.
EXCLUDED_NAMES = frozenset({
    "tmp",
    "chromadb",
    "logs",
    "model_hash_cache.json",
    "database.db",
})

EXCLUDED_PATTERNS = (
    "*.bak-*",
    "settings.json",
    "settings.json.*",
    "*-wal",
    "*-shm",
    "*.pre-restore-*",
)

ANIMATED_THUMBNAIL_SUFFIX = "_animated.webp"

BACKEND_S3 = "s3"


def is_excluded(name: str) -> bool:
    if name in EXCLUDED_NAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS)


@dataclass(frozen=True)
class StateLayout:
    repo_root: Path
    db_path: Path
    key_path: Path
    env_path: Path
    storage_dir: Path
    models_dir: Path
    storage_backend: str


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path)


def database_path(repo_root: Path) -> Path:
    return _resolve(repo_root, os.environ.get("POTIONUI_DB_PATH", f"storage/{DB_FILENAME}"))


def secret_key_path(db_path: Path) -> Path:
    """Mirrors the resolution order in `src/platform/security/secrets.py`,
    which is deliberately not imported: it pulls `cryptography`."""
    override = os.environ.get("POTIONUI_SECRET_KEY_FILE")
    if override:
        return Path(override).expanduser()
    return db_path.parent / SECRET_KEY_FILENAME


def read_setting(db_path: Path, key: str, default: str) -> str:
    if not db_path.exists():
        return default
    value = setting_value(db_path, key)
    return default if value is None else value


def resolve_layout(repo_root: Path) -> StateLayout:
    repo_root = Path(repo_root).resolve()
    db_path = database_path(repo_root)
    storage_dir = _resolve(repo_root, read_setting(db_path, "file_storage_directory", "storage"))
    models_dir = _resolve(repo_root, read_setting(db_path, "models_dir", "models"))
    backend = read_setting(db_path, "storage_backend", "local")
    return StateLayout(
        repo_root=repo_root,
        db_path=db_path,
        key_path=secret_key_path(db_path),
        env_path=repo_root / ENV_FILENAME,
        storage_dir=storage_dir,
        models_dir=models_dir,
        storage_backend=backend if backend in ("local", BACKEND_S3) else "local",
    )


def iter_tree_files(root: Path) -> Iterator[Tuple[Path, str]]:
    """Every non-excluded file under `root`, as (absolute path, posix relative)."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if any(is_excluded(part) for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        yield path, path.relative_to(root).as_posix()
