"""Taking a backup from the admin panel, and what the panel shows about them.

The run itself is the same `run_backup` the `potionui backup` command calls -
this module only adds what the command line gets for free: one job at a time,
the archives already in the destination, and pruning to the retention the
settings ask for.
"""

from __future__ import annotations

import logging
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.features.backup.archive import ARCHIVE_PREFIX, TIERS, run_backup
from src.features.backup.manifest import MANIFEST_FILENAME, load_manifest
from src.features.backup.settings import (
    destination_state,
    load_backup_settings,
    resolve_destination,
)
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

ARCHIVE_GLOB = f"{ARCHIVE_PREFIX}*.zip"
CRON_SCHEDULE = "0 3 * * *"
LAUNCHER = "potionui"

RUNNING = "running"
DONE = "done"
FAILED = "failed"


class BackupRunning(Exception):
    """A backup is already in flight."""


@dataclass
class BackupJob:
    """One run's live state."""

    id: str
    status: str
    tier: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    archive: Optional[str] = None
    bytes: int = 0
    mirror: Optional[Dict[str, Any]] = None
    pruned: List[str] = field(default_factory=list)
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "tier": self.tier,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "archive": self.archive,
            "bytes": self.bytes,
            "mirror": self.mirror,
            "pruned": list(self.pruned),
            "last_error": self.last_error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_archive_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as zf:
            return load_manifest(zf.read(MANIFEST_FILENAME))
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None


def describe_archive(path: Path, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path),
        "bytes": path.stat().st_size,
        "created_at": (manifest or {}).get("created_at"),
        "tier": (manifest or {}).get("tier"),
        "tiers": list((manifest or {}).get("tiers") or []),
        "app_version": (manifest or {}).get("app_version"),
        "migration_head": (manifest or {}).get("migration_head"),
        "readable": manifest is not None,
    }


def _sort_key(entry: Dict[str, Any]) -> str:
    """Manifest time when the archive has one, else the timestamp in its name,
    so an unreadable archive still sorts among the rest instead of first."""
    return entry.get("created_at") or entry["name"]


def mirror_summary(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    block = manifest.get("media_mirror")
    if not isinstance(block, dict):
        return None
    directories = block.get("directories") or {}
    return {
        "last_synced": manifest.get("created_at"),
        "day_count": len(directories),
        "bytes": sum(int(counts.get("bytes", 0)) for counts in directories.values()),
    }


class BackupRuns:
    """The single in-memory backup job, and the archives on disk beside it."""

    def __init__(self, settings, repo_root: Path):
        self.settings = settings
        self.repo_root = Path(repo_root)
        self._lock = threading.Lock()
        self._job: Optional[BackupJob] = None

    # ---------- where ----------

    def config(self) -> Dict[str, Any]:
        return load_backup_settings(self.settings)

    def destination(self) -> Path:
        return resolve_destination(self.repo_root, self.config()["destination"])

    def state(self) -> Tuple[bool, bool]:
        """Whether the destination exists, and whether a backup could write
        into it. Reading the panel never creates a directory - the run does
        that, and so does saving the setting."""
        return destination_state(self.destination())

    def cron_line(self, config: Optional[Dict[str, Any]] = None) -> str:
        config = config or self.config()
        launcher = self.repo_root / LAUNCHER
        destination = resolve_destination(self.repo_root, config["destination"])
        return (
            f"{CRON_SCHEDULE} {launcher} backup "
            f"--tier {config['default_tier']} --out {destination}"
        )

    # ---------- archives ----------

    def scan(self, destination: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Every archive in the destination, newest first, and what the newest
        one that mirrored media says the mirror holds. Foreign files are not
        archives and are not listed."""
        destination = self.destination() if destination is None else destination
        if not destination.is_dir():
            return [], None

        entries = []
        manifests = {}
        for path in destination.glob(ARCHIVE_GLOB):
            if not path.is_file():
                continue
            manifest = read_archive_manifest(path)
            entries.append(describe_archive(path, manifest))
            manifests[path.name] = manifest

        entries.sort(key=_sort_key, reverse=True)
        mirror = None
        for entry in entries:
            manifest = manifests.get(entry["name"])
            mirror = mirror_summary(manifest) if manifest else None
            if mirror is not None:
                break
        return entries, mirror

    def list_archives(self) -> List[Dict[str, Any]]:
        return self.scan()[0]

    def last_backup(self, archives: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        archives = self.list_archives() if archives is None else archives
        if not archives:
            return None
        newest = archives[0]
        return {"time": newest["created_at"], "bytes": newest["bytes"], "tier": newest["tier"]}

    def archive_path(self, name: str) -> Optional[Path]:
        """The archive `name` names inside the destination, or None when the
        name reaches outside it or is not an archive."""
        if not name or name != Path(name).name or not name.endswith(".zip"):
            return None
        destination = self.destination().resolve()
        candidate = (destination / name).resolve()
        if candidate.parent != destination or not candidate.is_file():
            return None
        return candidate

    def delete_archive(self, name: str) -> bool:
        path = self.archive_path(name)
        if path is None:
            return False
        path.unlink()
        return True

    def prune(self, keep: int) -> List[str]:
        """Remove all but the newest `keep` archives. The media mirror is a
        directory, not an archive, so nothing here can reach it."""
        if keep <= 0:
            return []
        removed = []
        for entry in self.list_archives()[keep:]:
            try:
                Path(entry["path"]).unlink()
                removed.append(entry["name"])
            except OSError as exc:
                logger.warning("Could not prune backup %s: %s", entry["name"], exc)
        return removed

    # ---------- running ----------

    def current(self) -> Optional[BackupJob]:
        with self._lock:
            return self._job

    def start(self, tier: Optional[str] = None) -> BackupJob:
        config = self.config()
        tier = tier or config["default_tier"]
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")

        with self._lock:
            if self._job is not None and self._job.status == RUNNING:
                raise BackupRunning("A backup is already in progress")
            job = BackupJob(id=generate_ulid(), status=RUNNING, tier=tier, started_at=_now())
            self._job = job

        threading.Thread(target=self._run, args=(job, config), daemon=True).start()
        return job

    def _run(self, job: BackupJob, config: Dict[str, Any]) -> None:
        try:
            destination = resolve_destination(self.repo_root, config["destination"])
            result = run_backup(self.repo_root, destination, tier=job.tier)
            job.archive = str(result.archive_path)
            job.bytes = result.archive_bytes
            job.mirror = result.media.as_dict() if result.media is not None else None
            job.pruned = self.prune(config["retention"])
            job.status = DONE
        except Exception as e:
            logger.exception("Backup run failed")
            job.status = FAILED
            job.last_error = str(e)
        finally:
            job.finished_at = _now()

    # ---------- the panel ----------

    def overview(self) -> Dict[str, Any]:
        config = self.config()
        destination = resolve_destination(self.repo_root, config["destination"])
        exists, writable = destination_state(destination)
        archives, mirror = self.scan(destination)
        job = self.current()
        return {
            "settings": config,
            "destination_abs": str(destination),
            "exists": exists,
            "writable": writable,
            "archives": archives,
            "mirror": mirror,
            "last_backup": self.last_backup(archives),
            "cron_line": self.cron_line(config),
            "job": job.to_dict() if job else None,
        }
