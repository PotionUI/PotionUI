"""Re-render stored thumbnails under the profile in force now.

Changing a thumbnail setting only changes what gets written from then on.
This is the deliberate second step: one background worker walking every
`files`/`uploads` row whose recorded profile fingerprint is not the current
one, re-rendering it and deleting the sizes the new profile no longer
produces. One file at a time, cancellable between files, and the gallery
keeps serving from whatever thumbnails a row still has while it runs.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from PIL import Image

from src.features.generation.file_repository import FileRepository
from src.features.generation.handlers.image_handler import generate_thumbnails
from src.features.generation.handlers.video_handler import generate_video_thumbnails
from src.features.generation.thumbnail_profile import (
    SIZE_ORDER,
    ThumbnailProfile,
    load_thumbnail_profile,
    profile_hash,
)
from src.features.media.upload_repository import UploadRepository
from src.platform.filesystem.storage_driver import FileStorageDriver, local_copy
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

MAX_RECORDED_ERRORS = 20
USAGE_CACHE_SECONDS = 60


class ThumbnailJobRunning(Exception):
    """A regeneration run is already in flight."""


@dataclass
class ThumbnailJob:
    """One regeneration run's live state."""

    id: str
    status: str  # running | cancelling | done | failed | cancelled
    total: int = 0
    done: int = 0
    failed: int = 0
    current: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: Optional[str] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "current": self.current,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_error": self.last_error,
            "errors": list(self.errors),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ThumbnailRegeneration:
    """Holds the single in-memory regeneration job and the disk-usage probe."""

    def __init__(
        self,
        settings: Settings,
        storage_driver: FileStorageDriver,
        file_repository: FileRepository,
        upload_repository: UploadRepository,
    ):
        self.settings = settings
        self.storage_driver = storage_driver
        self.file_repo = file_repository
        self.upload_repo = upload_repository
        self._lock = threading.Lock()
        self._job: Optional[ThumbnailJob] = None
        self._cancelled = threading.Event()
        self._usage_cache: Optional[tuple] = None

    # ---------- state ----------

    def current(self) -> Optional[ThumbnailJob]:
        with self._lock:
            return self._job

    def counts(self) -> Dict[str, int]:
        """How many rows exist per kind, and how many are stale right now."""
        target = profile_hash(load_thumbnail_profile(self.settings))
        files = self.file_repo.count_thumbnail_bearing()
        uploads = self.upload_repo.count_thumbnail_bearing()
        return {
            "images": files["images"] + uploads["images"],
            "videos": files["videos"] + uploads["videos"],
            "uploads": uploads["images"] + uploads["videos"],
            "stale": (
                self.file_repo.count_stale_thumbnails(target)
                + self.upload_repo.count_stale_thumbnails(target)
            ),
        }

    def stale_count(self) -> int:
        target = profile_hash(load_thumbnail_profile(self.settings))
        return (
            self.file_repo.count_stale_thumbnails(target)
            + self.upload_repo.count_stale_thumbnails(target)
        )

    def start(self) -> ThumbnailJob:
        with self._lock:
            if self._job is not None and self._job.status in ("running", "cancelling"):
                raise ThumbnailJobRunning("A thumbnail regeneration run is already in progress")
            job = ThumbnailJob(id=generate_ulid(), status="running", started_at=_now())
            self._job = job
        self._cancelled.clear()
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def cancel(self) -> Optional[ThumbnailJob]:
        with self._lock:
            job = self._job
            if job is None or job.status not in ("running", "cancelling"):
                return None
            job.status = "cancelling"
        self._cancelled.set()
        return job

    # ---------- usage ----------

    def usage(self) -> Optional[Dict[str, Any]]:
        """Bytes on disk under every thumbnails directory, or None when the
        storage driver is not local (an S3 walk would be a paid listing of
        the whole bucket on every admin page load)."""
        cached = self._usage_cache
        if cached is not None and time.monotonic() - cached[0] < USAGE_CACHE_SECONDS:
            return cached[1]

        roots = []
        for key in ("generations", "uploads"):
            local = self.storage_driver.local_path(key)
            if local is None:
                return None
            roots.append(local)

        static_bytes = 0
        animated_bytes = 0
        for root in roots:
            for dirpath, _, filenames in os.walk(root):
                if os.path.basename(dirpath) != "thumbnails":
                    continue
                for name in filenames:
                    try:
                        size = os.path.getsize(os.path.join(dirpath, name))
                    except OSError:
                        continue
                    if name.endswith("_animated.webp"):
                        animated_bytes += size
                    else:
                        static_bytes += size

        measured = {
            "static_bytes": static_bytes,
            "animated_bytes": animated_bytes,
            "total_bytes": static_bytes + animated_bytes,
            "measured_at": _now(),
        }
        self._usage_cache = (time.monotonic(), measured)
        return measured

    # ---------- worker ----------

    def _run(self, job: ThumbnailJob) -> None:
        try:
            profile = load_thumbnail_profile(self.settings)
            target = profile_hash(profile)
            work = [("file", record) for record in self.file_repo.list_stale_thumbnails(target)]
            work += [("upload", record) for record in self.upload_repo.list_stale_thumbnails(target)]
            job.total = len(work)

            cancelled = False
            for kind, record in work:
                if self._cancelled.is_set():
                    cancelled = True
                    break

                path = record.file_path if kind == "file" else record.filename
                job.current = path
                try:
                    if kind == "file":
                        self._regenerate_file(record, profile, target)
                    else:
                        self._regenerate_upload(record, profile, target)
                    job.done += 1
                except Exception as e:
                    job.failed += 1
                    job.last_error = str(e)
                    job.errors.append({"file_id": record.id, "path": path, "error": str(e)})
                    del job.errors[:-MAX_RECORDED_ERRORS]
                    logger.warning("Thumbnail regeneration failed for %s: %s", path, e)

            job.current = None
            job.status = "cancelled" if cancelled else "done"
        except Exception as e:
            logger.exception("Thumbnail regeneration run failed")
            job.current = None
            job.status = "failed"
            job.last_error = str(e)
        finally:
            job.finished_at = _now()

    def _regenerate_file(self, record, profile: ThumbnailProfile, target: str) -> None:
        base_key = PurePosixPath(record.file_path).parent.as_posix()
        base_key = "" if base_key == "." else base_key
        is_video = (record.file_type or "").upper() == "VIDEO"
        old_keys = self._existing_thumbnail_keys(record, base_key, is_video)

        paths = self._render(record.file_path, base_key, record, profile, is_video)

        self._delete_dropped(old_keys, self._new_thumbnail_keys(paths, base_key, is_video))
        self.file_repo.set_thumbnail_paths(
            [record.id], paths.get("small"), paths.get("medium"), paths.get("large"), target
        )

    def _regenerate_upload(self, record, profile: ThumbnailProfile, target: str) -> None:
        base_key = "uploads"
        source_key = f"uploads/{record.filename}"
        is_video = (record.media_type or "").lower() == "video"
        old_keys = self._existing_thumbnail_keys(record, base_key, is_video)

        paths = self._render(source_key, base_key, record, profile, is_video)

        self._delete_dropped(old_keys, self._new_thumbnail_keys(paths, base_key, is_video))
        self.upload_repo.set_thumbnail_paths(
            record.id, paths.get("small"), paths.get("medium"), paths.get("large"), target
        )

    def _render(
        self, source_key: str, base_key: str, record, profile: ThumbnailProfile, is_video: bool
    ) -> Dict[str, str]:
        if not self.storage_driver.exists(source_key):
            raise FileNotFoundError(f"Source file is gone: {source_key}")

        counter = self._counter(record, source_key)
        suffix = Path(source_key).suffix

        with local_copy(self.storage_driver, source_key, suffix=suffix) as local_path:
            if is_video:
                paths = generate_video_thumbnails(
                    str(local_path), self.storage_driver, base_key, counter, profile
                )
            else:
                with Image.open(local_path) as image:
                    image.load()
                    paths = generate_thumbnails(
                        image, self.storage_driver, base_key, counter, profile
                    )

        if not paths:
            raise RuntimeError("Thumbnail generation produced no files")
        return paths

    @staticmethod
    def _counter(record, source_key: str) -> str:
        """The filename stem every one of this row's thumbnails is named
        after - taken from an existing thumbnail so a re-render overwrites in
        place, and from the source file for a row that has none yet."""
        for size in SIZE_ORDER:
            existing = getattr(record, f"thumbnail_{size}", None)
            if existing:
                stem = PurePosixPath(existing).stem
                return stem[: -len(f"_{size}")] if stem.endswith(f"_{size}") else stem
        return PurePosixPath(source_key).stem

    @staticmethod
    def _thumbnail_key(base_key: str, relative: str) -> str:
        return f"{base_key}/{relative}" if base_key else relative

    def _existing_thumbnail_keys(self, record, base_key: str, is_video: bool) -> set:
        keys = set()
        for size in SIZE_ORDER:
            relative = getattr(record, f"thumbnail_{size}", None)
            if not relative:
                continue
            keys.add(self._thumbnail_key(base_key, relative))
            if is_video:
                animated = f"{PurePosixPath(relative).parent}/{PurePosixPath(relative).stem}_animated.webp"
                keys.add(self._thumbnail_key(base_key, animated))
        return keys

    def _new_thumbnail_keys(self, paths: Dict[str, str], base_key: str, is_video: bool) -> set:
        keys = set()
        for relative in paths.values():
            keys.add(self._thumbnail_key(base_key, relative))
            if is_video:
                animated = f"{PurePosixPath(relative).parent}/{PurePosixPath(relative).stem}_animated.webp"
                keys.add(self._thumbnail_key(base_key, animated))
        return keys

    def _delete_dropped(self, old_keys: set, new_keys: set) -> None:
        for key in sorted(old_keys - new_keys):
            try:
                self.storage_driver.delete(key)
            except Exception as e:
                logger.warning("Could not delete stale thumbnail %s: %s", key, e)
