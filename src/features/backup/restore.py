"""Putting a backup archive back onto an install."""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.backup.archive import MEDIA_DIR_NAME
from src.features.backup.manifest import MANIFEST_FILENAME, load_manifest
from src.features.backup.mirror import SKIP, MirrorStats, mirror_tree
from src.features.backup.paths import (
    CONTENT_TREES,
    DB_FILENAME,
    ENV_FILENAME,
    SECRET_KEY_FILENAME,
    STORAGE_CONFIG_FILES,
    STORAGE_MEDIA_TREES,
    STORAGE_SMALL_TREES,
    StateLayout,
    resolve_layout,
)
from src.features.backup.verify import VerifyReport, verify_media
from src.platform.database.migration_runner import MigrationRunner

RESTORE_TIMESTAMP = "%Y%m%d-%H%M%S"
STATE_FILE_RELATIVE = Path(".runtime") / "state.json"

FILE = "file"
TREE = "tree"


class RestoreRefused(RuntimeError):
    """Restoring now would destroy state or produce an install that cannot boot."""


@dataclass(frozen=True)
class RestoreItem:
    name: str
    kind: str
    destination: Path


@dataclass
class RestorePlan:
    archive_path: Path
    manifest: Dict[str, Any]
    layout: StateLayout
    items: List[RestoreItem]
    media_dir: Optional[Path]
    blockers: List[str] = field(default_factory=list)
    verify: Optional[VerifyReport] = None


@dataclass
class RestoreResult:
    plan: RestorePlan
    placed: List[RestoreItem]
    previous_paths: List[Path]
    media: Optional[MirrorStats]
    verify: Optional[VerifyReport]
    dry_run: bool


def detect_running_app(repo_root: Path) -> List[str]:
    """The processes a previous `potionui start` left behind, still alive.

    Restoring under a running app would hand it a database it has open and a
    keyring it has already read into memory.
    """
    state_file = Path(repo_root) / STATE_FILE_RELATIVE
    if not state_file.is_file():
        return []
    try:
        state = json.loads(state_file.read_text())
    except (ValueError, OSError):
        return []

    alive = []
    for name in ("backend", "frontend"):
        info = state.get(name) or {}
        pid = info.get("pid")
        if not isinstance(pid, int):
            continue
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno != errno.EPERM:
                continue
        alive.append(f"{name} (pid {pid})")
    return alive


def _unsafe_members(names: List[str]) -> List[str]:
    unsafe = []
    for name in names:
        pure = Path(name)
        if pure.is_absolute() or ".." in pure.parts or name.startswith("/"):
            unsafe.append(name)
    return unsafe


def _planned_items(layout: StateLayout, names: set) -> List[RestoreItem]:
    def has_tree(prefix: str) -> bool:
        return any(name.startswith(f"{prefix}/") for name in names)

    items: List[RestoreItem] = []
    if DB_FILENAME in names:
        items.append(RestoreItem(DB_FILENAME, FILE, layout.db_path))
    if SECRET_KEY_FILENAME in names:
        items.append(RestoreItem(SECRET_KEY_FILENAME, FILE, layout.key_path))
    if ENV_FILENAME in names:
        items.append(RestoreItem(ENV_FILENAME, FILE, layout.env_path))
    for tree in CONTENT_TREES:
        if has_tree(tree):
            items.append(RestoreItem(tree, TREE, layout.repo_root / tree))
    for name in STORAGE_CONFIG_FILES:
        if f"storage/{name}" in names:
            items.append(RestoreItem(f"storage/{name}", FILE, layout.storage_dir / name))
    for name in STORAGE_SMALL_TREES:
        if has_tree(f"storage/{name}"):
            items.append(RestoreItem(f"storage/{name}", TREE, layout.storage_dir / name))
    return items


def _default_media_dir(archive_path: Path) -> Optional[Path]:
    candidate = archive_path.parent / MEDIA_DIR_NAME
    return candidate if candidate.is_dir() else None


def plan_restore(
    archive_path: Path,
    repo_root: Path,
    *,
    media_dir: Optional[Path] = None,
    available_migrations: Optional[List[str]] = None,
) -> RestorePlan:
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise RestoreRefused(f"no archive at {archive_path}.")

    layout = resolve_layout(repo_root)
    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        if MANIFEST_FILENAME not in names:
            raise RestoreRefused(
                f"{archive_path.name} has no {MANIFEST_FILENAME}: it is not a PotionUI backup."
            )
        manifest = load_manifest(zf.read(MANIFEST_FILENAME))

    blockers: List[str] = []

    unsafe = _unsafe_members(names)
    if unsafe:
        raise RestoreRefused(
            f"{archive_path.name} contains member paths outside the archive root "
            f"({', '.join(unsafe[:3])}). Refusing to unpack it."
        )

    if DB_FILENAME not in names:
        raise RestoreRefused(f"{archive_path.name} contains no {DB_FILENAME}.")

    available = available_migrations
    if available is None:
        available = MigrationRunner().get_available_migrations()
    head = manifest.get("migration_head")
    if head and head not in set(available):
        blockers.append(
            f"the archive's schema is at migration {head}, which this checkout does not "
            f"have (newest available: {available[-1] if available else 'none'}). Update "
            f"PotionUI to at least the version that wrote the backup "
            f"(app_version {manifest.get('app_version')})."
        )

    running = detect_running_app(layout.repo_root)
    if running:
        blockers.append(
            f"PotionUI is running ({', '.join(running)}). Stop it with `./potionui stop` first."
        )

    resolved_media = Path(media_dir) if media_dir else _default_media_dir(archive_path)
    if media_dir and not Path(media_dir).is_dir():
        raise RestoreRefused(f"no media mirror directory at {media_dir}.")

    return RestorePlan(
        archive_path=archive_path,
        manifest=manifest,
        layout=layout,
        items=_planned_items(layout, set(names)),
        media_dir=resolved_media,
        blockers=blockers,
    )


def _move(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(destination))


def _set_aside_database(db_path: Path, stamp: str) -> Optional[Path]:
    """Move the current database aside, taking its write-ahead log with it.

    The sidecars have to leave the restored database's path - SQLite would read
    a stale `-wal` as part of the new file - and they have to stay paired with
    the database they belong to, or the copy kept aside loses everything that
    had not been checkpointed.
    """
    if not db_path.exists():
        for suffix in ("-wal", "-shm"):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        return None

    previous = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
    os.replace(db_path, previous)
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            os.replace(sidecar, previous.with_name(previous.name + suffix))
    return previous


def _dry_run_verify(archive_path: Path, plan: RestorePlan) -> VerifyReport:
    roots = [root for root in (plan.media_dir, plan.layout.storage_dir) if root and root.is_dir()]
    with tempfile.TemporaryDirectory(prefix="potionui-restore-check-") as staging:
        with zipfile.ZipFile(archive_path) as zf:
            zf.extract(DB_FILENAME, staging)
        return verify_media(Path(staging) / DB_FILENAME, roots)


def run_restore(
    archive_path: Path,
    repo_root: Path,
    *,
    media_dir: Optional[Path] = None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> RestoreResult:
    plan = plan_restore(archive_path, repo_root, media_dir=media_dir)

    if dry_run:
        plan.verify = _dry_run_verify(plan.archive_path, plan)
        return RestoreResult(
            plan=plan, placed=[], previous_paths=[], media=None, verify=plan.verify, dry_run=True
        )

    if plan.blockers:
        raise RestoreRefused(" ".join(plan.blockers))

    stamp = (now or datetime.now()).strftime(RESTORE_TIMESTAMP)
    layout = plan.layout
    staging = layout.repo_root / f".potionui-restore-{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    placed: List[RestoreItem] = []
    previous_paths: List[Path] = []
    try:
        with zipfile.ZipFile(plan.archive_path) as zf:
            zf.extractall(staging)

        for item in plan.items:
            source = staging / item.name
            if not source.exists():
                continue
            item.destination.parent.mkdir(parents=True, exist_ok=True)

            if item.name == DB_FILENAME:
                previous = _set_aside_database(item.destination, stamp)
            elif item.name == SECRET_KEY_FILENAME and item.destination.exists():
                previous = item.destination.with_name(
                    f"{item.destination.name}.pre-restore-{stamp}"
                )
                os.replace(item.destination, previous)
            elif item.kind == TREE and item.destination.exists():
                previous = item.destination.with_name(
                    f"{item.destination.name}.pre-restore-{stamp}"
                )
                os.replace(item.destination, previous)
            else:
                previous = None

            if previous is not None:
                previous_paths.append(previous)
            _move(source, item.destination)
            placed.append(item)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    media_stats: Optional[MirrorStats] = None
    if plan.media_dir:
        media_stats = MirrorStats()
        for tree in STORAGE_MEDIA_TREES:
            media_stats.merge(
                mirror_tree(
                    plan.media_dir / tree,
                    layout.storage_dir / tree,
                    include_animated=True,
                    if_exists=SKIP,
                    group_depth=1,
                    group_prefix=f"{tree}/",
                )
            )

    verify = verify_media(layout.db_path, [layout.storage_dir])
    plan.verify = verify
    return RestoreResult(
        plan=plan,
        placed=placed,
        previous_paths=previous_paths,
        media=media_stats,
        verify=verify,
        dry_run=False,
    )
