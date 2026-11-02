import hashlib
import logging
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from PIL import Image

from src.features.generation.thumbnail_profile import load_thumbnail_profile, profile_hash
from src.features.media.records import Upload
from src.features.media.upload_thumbnails import (
    delete_keys,
    paths_to_keys,
    render_image_thumbnails,
    render_video_thumbnails,
    run_in_background,
    thumbnail_keys,
)
from src.features.media_tools.dto import MediaToolItem
from src.platform.database.rows import dt_iso
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key
from src.platform.http.errors import ConflictError, NotFoundError
from src.platform.media_tools import ToolMedia
from src.platform.plugins.limit_kinds import AdmissionRequest
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

if TYPE_CHECKING:
    from src.features.media.media_types import MediaTypeResolver
    from src.features.media.upload_repository import UploadRepository
    from src.features.plans.guard import LimitGuard
    from src.platform.filesystem.storage_driver import FileStorageDriver

logger = logging.getLogger(__name__)

HASH_CHUNK_BYTES = 1024 * 1024


def to_item(upload: Upload) -> MediaToolItem:
    return MediaToolItem(
        id=upload.id,
        filename=upload.filename,
        original_filename=upload.original_filename,
        media_type=upload.media_type,
        mime_type=upload.mime_type,
        url=f"/api/media/uploads/{upload.filename}",
        width=upload.width,
        height=upload.height,
        duration_seconds=upload.duration_seconds,
        fps=upload.fps,
        size=upload.file_size,
        created_at=dt_iso(upload.created_at),
        thumbnail_small=_thumbnail_url(upload, "small"),
        thumbnail_medium=_thumbnail_url(upload, "medium"),
        thumbnail_large=_thumbnail_url(upload, "large"),
        source_generation_id=upload.source_generation_id,
        provenance=upload.provenance,
    )


def _thumbnail_url(upload: Upload, size: str) -> Optional[str]:
    if not getattr(upload, f"thumbnail_{size}", None):
        return None
    return f"/api/media/uploads/{upload.filename}?size={size}"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MediaToolPublisher:
    def __init__(
        self,
        upload_repository: "UploadRepository",
        media_type_resolver: "MediaTypeResolver",
        storage_driver: "FileStorageDriver",
        limit_guard: Optional["LimitGuard"] = None,
        settings: Optional[Settings] = None,
        thumbnail_runner: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        self.upload_repo = upload_repository
        self.media_types = media_type_resolver
        self.storage_driver = storage_driver
        self.limit_guard = limit_guard
        self.settings = settings if settings is not None else Settings(SettingRepository())
        self.thumbnail_runner = thumbnail_runner or run_in_background

    def precheck(self, user_id: str) -> None:
        if self.limit_guard is not None:
            self.limit_guard.check(AdmissionRequest(point="upload", user_id=user_id))

    def publish_new(
        self,
        user_id: str,
        outputs: Sequence[ToolMedia],
        names: Sequence[Optional[str]],
        source_generation_id: Optional[str] = None,
        provenance: Optional[Sequence[Optional[dict]]] = None,
    ) -> list[Upload]:
        profile = load_thumbnail_profile(self.settings)
        stored: list[str] = []
        thumbnails: Set[str] = set()
        created: list[Upload] = []
        try:
            for media in outputs:
                key = self._new_key(media.path.suffix)
                self.storage_driver.put_file(key, media.path)
                stored.append(key)
            sizes = tuple(self.storage_driver.size(key) or 0 for key in stored)
            self._admit(user_id, sum(sizes), sizes)
            marks = list(provenance) if provenance is not None else [None] * len(stored)
            for key, media, name, size, mark in zip(stored, outputs, names, sizes, marks):
                paths = self._image_thumbnails(profile, media, Path(key).stem)
                thumbnails |= paths_to_keys(paths, False)
                created.append(
                    self.upload_repo.create(
                        self._row(user_id, key, media, name, size, paths, profile, source_generation_id, mark)
                    )
                )
        except Exception:
            for row in created:
                self._forget_row(row, user_id)
            for key in stored:
                self._remove(key)
            delete_keys(self.storage_driver, thumbnails)
            raise
        for key, media, row in zip(stored, outputs, created):
            self._schedule_video_thumbnails(profile, media, key, row)
        return created

    def publish_replace(
        self, user_id: str, upload: Upload, source_key: str, output: ToolMedia
    ) -> Upload:
        profile = load_thumbnail_profile(self.settings)
        dest_key = self._new_key(output.path.suffix)
        paths: Dict[str, str] = {}
        try:
            self.storage_driver.put_file(dest_key, output.path)
            self._require_unchanged(user_id, upload)
            paths = self._image_thumbnails(profile, output, Path(dest_key).stem)
            written = self.storage_driver.size(dest_key) or 0
            self._admit(user_id, written - (upload.file_size or 0), ())
            updated = self.upload_repo.replace_file(
                upload_id=upload.id,
                user_id=user_id,
                expected_filename=upload.filename,
                filename=Path(dest_key).name,
                mime_type=self.media_types.get_media_type(Path(dest_key).suffix),
                width=output.width,
                height=output.height,
                duration_seconds=output.duration,
                fps=output.fps,
                file_size=written,
                content_hash=file_digest(output.path),
                thumbnail_small=paths.get("small"),
                thumbnail_medium=paths.get("medium"),
                thumbnail_large=paths.get("large"),
                thumbnail_profile=profile_hash(profile) if paths else None,
                has_alpha=output.has_alpha,
            )
            if not updated:
                self._require_unchanged(user_id, upload)
                raise NotFoundError("not_found", "Media not found")
        except Exception:
            self._remove(dest_key)
            delete_keys(self.storage_driver, paths_to_keys(paths, False))
            raise
        self._remove(source_key)
        is_video = (upload.media_type or "").lower() == "video"
        delete_keys(
            self.storage_driver,
            thumbnail_keys(upload, is_video) - paths_to_keys(paths, is_video),
        )
        self._schedule_video_thumbnails(profile, output, dest_key, updated)
        logger.info("Replaced library resource %s with an edited file", upload.id)
        return updated

    def _require_unchanged(self, user_id: str, upload: Upload) -> None:
        current = self.upload_repo.get_by_id(upload.id, user_id)
        if current is None:
            raise NotFoundError("not_found", "Media not found")
        if current.filename != upload.filename:
            raise ConflictError(
                "conflict", "Another edit changed this item while the tool was running; run it again"
            )

    def _image_thumbnails(self, profile, media: ToolMedia, stem: str) -> Dict[str, str]:
        if media.kind != "image":
            return {}
        try:
            with Image.open(media.path) as image:
                image.load()
                return render_image_thumbnails(self.storage_driver, profile, image, stem)
        except Exception as exc:
            logger.warning("Failed to generate thumbnails for a media tool image: %s", exc)
            return {}

    def _schedule_video_thumbnails(self, profile, media: ToolMedia, key: str, row: Upload) -> None:
        if media.kind != "video" or row is None:
            return
        storage_driver = self.storage_driver
        upload_repo = self.upload_repo
        suffix = Path(key).suffix
        stem = Path(key).stem
        upload_id = row.id

        def worker():
            render_video_thumbnails(storage_driver, upload_repo, profile, key, suffix, upload_id, stem)

        self.thumbnail_runner(worker)

    def _admit(self, user_id: str, incoming: Optional[int], items: Tuple[int, ...]) -> None:
        if self.limit_guard is None:
            return
        self.limit_guard.admit(AdmissionRequest(
            point="upload", user_id=user_id, incoming_bytes=incoming, item_bytes=items,
        ))

    def _new_key(self, suffix: str) -> str:
        try:
            return uploads_key(f"{uuid.uuid4()}{suffix.lower()}")
        except StorageKeyError as exc:
            raise ValueError("Invalid output file name") from exc

    def _row(
        self,
        user_id: str,
        key: str,
        media: ToolMedia,
        name: Optional[str],
        size: int,
        thumbnail_paths: Dict[str, str],
        profile,
        source_generation_id: Optional[str] = None,
        provenance: Optional[dict] = None,
    ) -> Upload:
        return Upload(
            user_id=user_id,
            filename=Path(key).name,
            original_filename=name,
            media_type=media.kind,
            mime_type=self.media_types.get_media_type(Path(key).suffix),
            width=media.width,
            height=media.height,
            duration_seconds=media.duration,
            fps=media.fps,
            file_size=size,
            thumbnail_small=thumbnail_paths.get("small"),
            thumbnail_medium=thumbnail_paths.get("medium"),
            thumbnail_large=thumbnail_paths.get("large"),
            thumbnail_profile=profile_hash(profile) if thumbnail_paths else None,
            content_hash=file_digest(media.path),
            has_alpha=media.has_alpha,
            source_generation_id=source_generation_id,
            provenance=provenance,
        )

    def _forget_row(self, row: Upload, user_id: str) -> None:
        try:
            self.upload_repo.delete(row.filename, user_id)
        except Exception as exc:
            logger.warning("Could not roll back library row %s: %s", row.id, exc)

    def _remove(self, key: str) -> None:
        try:
            self.storage_driver.delete(key)
        except Exception as exc:
            logger.warning("Could not remove %s after a media tool run: %s", key, exc)
