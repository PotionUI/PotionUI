"""Building a backup: one zip of configuration, optionally a media mirror beside it."""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.features.backup.manifest import (
    MANIFEST_FILENAME,
    build_manifest,
    dump_manifest,
    load_manifest,
)
from src.features.backup.mirror import MirrorStats, mirror_tree
from src.features.backup.paths import (
    BACKEND_S3,
    CONTENT_TREES,
    DB_FILENAME,
    ENV_FILENAME,
    SECRET_KEY_FILENAME,
    STORAGE_CONFIG_FILES,
    STORAGE_MEDIA_TREES,
    STORAGE_SMALL_TREES,
    StateLayout,
    iter_tree_files,
    resolve_layout,
)
from src.features.backup.snapshot import migration_head, snapshot_database

TIER_CONFIG = "config"
TIER_MEDIA = "media"
TIER_ALL = "all"
TIERS = (TIER_CONFIG, TIER_MEDIA, TIER_ALL)

TIER_CHAIN = {
    TIER_CONFIG: [TIER_CONFIG],
    TIER_MEDIA: [TIER_CONFIG, TIER_MEDIA],
    TIER_ALL: [TIER_CONFIG, TIER_MEDIA, TIER_ALL],
}

ARCHIVE_PREFIX = "potionui-backup-"
ARCHIVE_TIMESTAMP = "%Y%m%d-%H%M%S"
MEDIA_DIR_NAME = "media"
MODELS_DIR_NAME = "models"


class BackupRefused(RuntimeError):
    """The backup cannot be taken as asked, and taking a partial one silently
    would be worse than stopping."""


@dataclass
class BackupResult:
    archive_path: Path
    tier: str
    manifest: Dict[str, Any]
    archive_bytes: int
    duration_seconds: float
    media_dir: Optional[Path] = None
    media: Optional[MirrorStats] = None
    models: Optional[MirrorStats] = None


def archive_name(moment: datetime) -> str:
    return f"{ARCHIVE_PREFIX}{moment.strftime(ARCHIVE_TIMESTAMP)}.zip"


def config_items(layout: StateLayout) -> List[Tuple[str, Path, bool]]:
    """Everything the configuration tier carries, as (archive name, source, is_tree).

    The database is not here: it is snapshotted, never read from its live path.
    """
    items: List[Tuple[str, Path, bool]] = [
        (SECRET_KEY_FILENAME, layout.key_path, False),
        (ENV_FILENAME, layout.env_path, False),
    ]
    items.extend((tree, layout.repo_root / tree, True) for tree in CONTENT_TREES)
    items.extend(
        (f"storage/{name}", layout.storage_dir / name, False) for name in STORAGE_CONFIG_FILES
    )
    items.extend(
        (f"storage/{name}", layout.storage_dir / name, True) for name in STORAGE_SMALL_TREES
    )
    return items


def _write_zip(
    destination: Path,
    *,
    snapshot_path: Path,
    layout: StateLayout,
    manifest_for: Any,
) -> Tuple[int, Dict[str, Dict[str, int]]]:
    """Stream the archive straight into a temporary file beside `destination`.

    Nothing is buffered in memory, and a crash mid-write leaves the partial
    file under its temporary name rather than a truncated archive that looks
    finished.
    """
    items: Dict[str, Dict[str, int]] = {}
    part = destination.parent / f".{destination.name}.part-{os.getpid()}"
    try:
        with open(part, "wb") as handle:
            with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.write(snapshot_path, DB_FILENAME)
                items[DB_FILENAME] = {"files": 1, "bytes": snapshot_path.stat().st_size}

                for name, source, is_tree in config_items(layout):
                    if is_tree:
                        count = 0
                        total = 0
                        for path, relative in iter_tree_files(source):
                            zf.write(path, f"{name}/{relative}")
                            count += 1
                            total += path.stat().st_size
                        if count:
                            items[name] = {"files": count, "bytes": total}
                    elif source.is_file():
                        zf.write(source, name)
                        items[name] = {"files": 1, "bytes": source.stat().st_size}

                zf.writestr(MANIFEST_FILENAME, dump_manifest(manifest_for(items)))
        os.replace(part, destination)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return destination.stat().st_size, items


def run_backup(
    repo_root: Path,
    out_dir: Path,
    *,
    tier: str = TIER_CONFIG,
    include_models: bool = False,
    include_animated_thumbnails: bool = False,
    now: Optional[datetime] = None,
) -> BackupResult:
    if tier not in TIERS:
        raise BackupRefused(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}.")

    repo_root = Path(repo_root).resolve()
    out_dir = Path(out_dir)
    layout = resolve_layout(repo_root)

    if not layout.db_path.is_file():
        raise BackupRefused(
            f"no database at {layout.db_path}. Start PotionUI once so it creates one, "
            f"or point POTIONUI_DB_PATH at the install you mean to back up."
        )

    wants_models = include_models or tier == TIER_ALL
    if tier == TIER_CONFIG and include_models:
        raise BackupRefused(
            "--include-models has nothing to mirror at the config tier. Use "
            "--tier all (media plus models), or drop the flag."
        )

    if tier != TIER_CONFIG and layout.storage_backend == BACKEND_S3:
        raise BackupRefused(
            "storage_backend is 's3': generation output and uploads live in the bucket, "
            "not on this disk, so there is no media tree to mirror. Turn on bucket "
            "versioning (and a lifecycle rule) for durable media, and take the "
            "configuration backup with --tier config."
        )

    moment = now or datetime.now().astimezone()
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)

    archive_path = out_dir / archive_name(moment)
    if archive_path.exists():
        raise BackupRefused(f"{archive_path} already exists.")

    work_dir = out_dir / f".{archive_path.stem}.work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    media_stats: Optional[MirrorStats] = None
    models_stats: Optional[MirrorStats] = None
    media_dir: Optional[Path] = None

    try:
        # The snapshot comes first: output bytes are written before the row that
        # points at them, so every row in this snapshot has its file on disk by
        # the time the mirror walks past it.
        snapshot_path = work_dir / DB_FILENAME
        snapshot_database(layout.db_path, snapshot_path)
        head = migration_head(snapshot_path)

        if tier != TIER_CONFIG:
            media_dir = out_dir / MEDIA_DIR_NAME
            media_stats = MirrorStats()
            for tree in STORAGE_MEDIA_TREES:
                media_stats.merge(
                    mirror_tree(
                        layout.storage_dir / tree,
                        media_dir / tree,
                        include_animated=include_animated_thumbnails,
                        group_depth=1,
                        group_prefix=f"{tree}/",
                    )
                )

        if wants_models and tier != TIER_CONFIG:
            models_stats = mirror_tree(
                layout.models_dir,
                out_dir / MODELS_DIR_NAME,
                include_animated=True,
                group_depth=1,
                group_prefix=f"{MODELS_DIR_NAME}/",
            )

        def manifest_for(items: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
            return build_manifest(
                tier=tier,
                tiers=TIER_CHAIN[tier],
                items=items,
                storage_backend=layout.storage_backend,
                migration_head=head,
                include_models=bool(models_stats is not None),
                include_animated_thumbnails=include_animated_thumbnails,
                media_mirror=media_stats.as_dict() if media_stats else None,
                models_mirror=models_stats.as_dict() if models_stats else None,
                created_at=moment,
            )

        archive_bytes, _items = _write_zip(
            archive_path,
            snapshot_path=snapshot_path,
            layout=layout,
            manifest_for=manifest_for,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    with zipfile.ZipFile(archive_path) as zf:
        manifest = load_manifest(zf.read(MANIFEST_FILENAME))

    return BackupResult(
        archive_path=archive_path,
        tier=tier,
        manifest=manifest,
        archive_bytes=archive_bytes,
        duration_seconds=time.monotonic() - started,
        media_dir=media_dir,
        media=media_stats,
        models=models_stats,
    )
