from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, TYPE_CHECKING

from src.features.media.records import Upload
from src.features.media.validators import UPLOAD_PURPOSE_DERIVED
from src.features.media_tools.dto import InputRef
from src.platform.filesystem.storage_driver import (
    StorageKeyError,
    generations_key,
    local_copy,
    uploads_key,
)
from src.platform.http.errors import InvalidInputError, NotFoundError
from src.platform.media_tools.types import MEDIA_KINDS

if TYPE_CHECKING:
    from src.features.generation.file_repository import FileRepository
    from src.features.media.upload_repository import UploadRepository
    from src.platform.filesystem.storage_driver import FileStorageDriver

GENERATION_FILE_KINDS = {"IMAGE": "image", "VIDEO": "video", "AUDIO": "audio"}
MISSING_MESSAGE = "Media not found"


@dataclass(frozen=True)
class ResolvedInput:
    kind: str
    key: str
    suffix: str
    original_filename: Optional[str]
    upload: Optional[Upload] = None
    generation_file_id: Optional[str] = None
    source_generation_id: Optional[str] = None

    @property
    def is_library_item(self) -> bool:
        return self.upload is not None and self.upload.purpose != UPLOAD_PURPOSE_DERIVED


def _not_found() -> NotFoundError:
    return NotFoundError("not_found", MISSING_MESSAGE)


def _unsupported(kind: str) -> InvalidInputError:
    return InvalidInputError("unsupported_media", f"A {kind} file cannot be edited with media tools")


class InputResolver:
    def __init__(
        self,
        upload_repository: "UploadRepository",
        file_repository: "FileRepository",
        storage_driver: "FileStorageDriver",
    ):
        self.upload_repo = upload_repository
        self.file_repo = file_repository
        self.storage_driver = storage_driver

    def resolve(self, user_id: str, ref: InputRef) -> ResolvedInput:
        if bool(ref.item_id) == bool(ref.generation_file_id):
            raise InvalidInputError(
                "invalid_selection", "Each input needs exactly one of item_id or generation_file_id"
            )
        if ref.item_id:
            return self._library_item(user_id, ref.item_id)
        return self._generation_file(user_id, ref.generation_file_id)

    def _library_item(self, user_id: str, item_id: str) -> ResolvedInput:
        upload = self.upload_repo.get_by_id(item_id, user_id)
        if upload is None:
            raise _not_found()
        try:
            key = uploads_key(upload.filename)
        except StorageKeyError:
            raise _not_found()
        if not self.storage_driver.exists(key):
            raise _not_found()
        if upload.media_type not in MEDIA_KINDS:
            raise _unsupported(upload.media_type)
        return ResolvedInput(
            kind=upload.media_type,
            key=key,
            suffix=Path(upload.filename).suffix,
            original_filename=upload.original_filename or upload.filename,
            upload=upload,
            source_generation_id=upload.source_generation_id,
        )

    def _generation_file(self, user_id: str, file_id: str) -> ResolvedInput:
        record = self.file_repo.get_by_id(file_id, user_id=user_id)
        if record is None:
            raise _not_found()
        kind = GENERATION_FILE_KINDS.get((record.file_type or "").upper())
        if kind is None:
            raise _unsupported(str(record.file_type).lower())
        try:
            key = generations_key(record.file_path)
        except StorageKeyError:
            raise _not_found()
        if not self.storage_driver.exists(key):
            raise _not_found()
        return ResolvedInput(
            kind=kind,
            key=key,
            suffix=Path(record.file_path).suffix,
            original_filename=Path(record.file_path).name,
            generation_file_id=record.id,
            source_generation_id=self.file_repo.get_generation_id(record.id),
        )

    @contextmanager
    def stage(self, resolved: ResolvedInput) -> Iterator[Path]:
        with local_copy(self.storage_driver, resolved.key, resolved.suffix) as path:
            yield path
