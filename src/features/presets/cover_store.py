import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from src.features.media.file_resolver import PRESET_PUBLIC_ROOT, PRESET_SERVABLE_EXTENSIONS
from src.features.media.media_types import sniff_media_extension
from src.features.presets.exceptions import InvalidCoverError
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

DISPLAY_ROOT = "display"
COVER_IMAGE_MAX_BYTES = 10 * 1024 * 1024
COVER_VIDEO_MAX_BYTES = 1536 * 1024
_IMAGE_EXTENSIONS = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg", ".webp": ".webp"}
_VIDEO_EXTENSIONS = {".mp4": ".mp4", ".webm": ".webm"}


def _contained(path: Path, base: Path) -> bool:
    try:
        return path.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def display_file_name(cover: Optional[str]) -> Optional[str]:
    if not isinstance(cover, str):
        return None
    parts = PurePosixPath(cover).parts
    if len(parts) != 2 or parts[0] != DISPLAY_ROOT or parts[1] in ("", ".", ".."):
        return None
    return parts[1]


class PresetCoverStore:
    def __init__(self, settings: Settings, preset_repository: Any):
        self.settings = settings
        self.preset_repository = preset_repository

    def root(self) -> Path:
        return Path(self.settings.get_file_storage_directory()) / "preset_display"

    def directory(self, preset_db_id: str) -> Path:
        return self.root() / preset_db_id

    def save(self, preset_db_id: str, filename: str, content: bytes) -> str:
        suffix = PurePosixPath(filename or "").suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            extension, limit = _IMAGE_EXTENSIONS[suffix], COVER_IMAGE_MAX_BYTES
        elif suffix in _VIDEO_EXTENSIONS:
            extension, limit = _VIDEO_EXTENSIONS[suffix], COVER_VIDEO_MAX_BYTES
        else:
            raise InvalidCoverError("A cover must be a PNG, JPEG or WebP image, or an MP4 or WebM video")
        if not content:
            raise InvalidCoverError("The cover file is empty")
        if len(content) > limit:
            raise InvalidCoverError(f"The cover is too large: {extension[1:].upper()} covers may be at most {limit // 1024} KB")
        if sniff_media_extension(content) != extension:
            raise InvalidCoverError(f"The file is not a {extension[1:].upper()} file")
        directory = self.directory(preset_db_id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{generate_ulid().lower()}{extension}"
        target = directory / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return f"{DISPLAY_ROOT}/{name}"

    def has(self, preset_db_id: str, cover: Optional[str]) -> bool:
        name = display_file_name(cover)
        if name is None:
            return False
        directory = self.directory(preset_db_id)
        path = directory / name
        return _contained(path, directory) and path.is_file()

    def resolve(self, preset_id: str, file_path: str) -> Path:
        name = display_file_name(file_path)
        if name is None or PurePosixPath(name).suffix.lower() not in PRESET_SERVABLE_EXTENSIONS:
            raise ValueError("Preset file not found")
        row = self.preset_repository.get_installed_preset_by_preset_id(preset_id)
        if row is None or row.display_cover != file_path:
            raise ValueError("Preset file not found")
        directory = self.directory(row.id)
        path = directory / name
        if not _contained(path, directory):
            raise ValueError("Access denied - path traversal detected")
        return path.resolve()

    def prune(self, preset_db_id: str, keep: Optional[str]) -> None:
        directory = self.directory(preset_db_id)
        if not directory.is_dir():
            return
        kept = display_file_name(keep)
        for path in directory.iterdir():
            if path.is_file() and path.name != kept:
                path.unlink(missing_ok=True)

    def remove(self, preset_db_id: str) -> None:
        directory = self.directory(preset_db_id)
        if directory.is_dir() and _contained(directory, self.root()):
            shutil.rmtree(directory, ignore_errors=True)


def public_cover_exists(preset_path: Optional[str], cover: str) -> bool:
    if not preset_path:
        return False
    parts = PurePosixPath(cover).parts
    if not parts or parts[0] != PRESET_PUBLIC_ROOT:
        return False
    if PurePosixPath(cover).suffix.lower() not in PRESET_SERVABLE_EXTENSIONS:
        return False
    base = Path(preset_path)
    path = base / cover
    return _contained(path, base) and path.is_file()
