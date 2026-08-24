"""The storage driver contract: where the bytes under `file_storage_directory`
actually live.

Everything the driver deals in is a *key* - a storage-root-relative, POSIX-style
path such as `generations/2026-08-15/<id>/0.png` or `uploads/<uuid>.png`. Keys
are exactly the `file_path` strings already stored in the `files`/`uploads`
tables, so switching drivers never changes what the database means by a path -
only where a key resolves to bytes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional

from src.platform.util.ids import generate_ulid

DEFAULT_CHUNK_SIZE = 65536


class StorageKeyError(ValueError):
    """A key is not a valid, storage-root-relative path."""


def validate_key(key: str) -> str:
    """Normalize `key` to a POSIX-style relative path, or raise.

    Rejects absolute paths, `..` segments, empty segments and NUL bytes - the
    same shape of check `FilePathResolver.validate_path_security` performs on
    a resolved filesystem path, applied here before a key is ever turned into
    one.
    """
    if not isinstance(key, str) or not key:
        raise StorageKeyError("Storage key must be a non-empty string.")
    if "\x00" in key:
        raise StorageKeyError(f"Storage key contains a NUL byte: {key!r}")
    if key.startswith("/") or key.startswith("\\"):
        raise StorageKeyError(f"Storage key must be relative: {key!r}")

    posix = PurePosixPath(key.replace("\\", "/"))
    if posix.is_absolute() or any(part in ("..", "") for part in posix.parts):
        raise StorageKeyError(f"Storage key must not escape the storage root: {key!r}")

    return posix.as_posix()


def uploads_key(filename: str) -> str:
    """The storage key for a file in the uploads namespace - the same
    `uploads/{filename}` convention `MediaManager.upload_media` writes to.

    Raises `StorageKeyError`, like `validate_key`; callers translate that into
    whatever error vocabulary they already return.
    """
    return validate_key(f"uploads/{filename}")


def generations_key(file_path: str) -> str:
    """The storage key for a generation output - unlike `uploads_key`, this
    never adds a prefix: a `files.file_path` value already IS a
    `generations/<date>/<generation_id>/...` relative path (see
    `docs/media_path_conventions` in project memory), so this is a validated
    pass-through that gives call sites one canonical name for "this DB path
    is a driver key" instead of handing `validate_key` a raw string.

    Raises `StorageKeyError`, like `validate_key`.
    """
    return validate_key(file_path)


@contextmanager
def local_copy(driver: "FileStorageDriver", key: str, suffix: str = "") -> Iterator[Path]:
    """A real filesystem path for `key`'s bytes, for callers (ffprobe, PIL,
    ffmpeg, ...) that need one regardless of storage backend.

    The local driver already has one - handed out directly, nothing copied.
    Any other driver has no local file, so its bytes are materialized into a
    temp file for the duration of the `with` block and removed on the way out.
    """
    direct = driver.local_path(key)
    if direct is not None:
        yield direct
        return

    data = driver.get_bytes(key)
    with tempfile.NamedTemporaryFile(suffix=suffix or "", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


@contextmanager
def local_target(driver: "FileStorageDriver", key: str, suffix: str = "") -> Iterator[Path]:
    """A real filesystem path to WRITE `key`'s bytes into, for callers
    (ffmpeg subprocesses, ...) that need to write through a path rather than
    hand over an in-memory buffer. The write-side counterpart to `local_copy`.

    The local driver's own path for `key` IS the destination, so writing
    there already publishes it - handed out directly, parent directory
    created, nothing pushed afterwards. Any other driver has no local file:
    the caller writes into a temp file, which is pushed through `put_file`
    when the `with` block exits and removed either way.
    """
    direct = driver.local_path(key)
    if direct is not None:
        direct.parent.mkdir(parents=True, exist_ok=True)
        yield direct
        return

    with tempfile.NamedTemporaryFile(suffix=suffix or "", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        yield tmp_path
        driver.put_file(key, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


class FileStorageDriver(ABC):
    """Put/get/delete bytes by key. Implementations: `LocalFileStorageDriver`
    (default) and `S3FileStorageDriver` (opt-in)."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> int:
        """Store `data` at `key`, returning the byte count written."""

    @abstractmethod
    def put_file(self, key: str, source_path: Path) -> int:
        """Stream the local file at `source_path` into `key` without loading
        it into memory, returning the byte count written."""

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """Read the whole object at `key`. Raises `FileNotFoundError` if absent."""

    @abstractmethod
    def get_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        """Stream the object at `key` in chunks. Raises `FileNotFoundError` if absent."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete `key`. Returns whether something was actually deleted."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def size(self, key: str) -> Optional[int]:
        """Byte size of `key`, or `None` if it does not exist."""

    def local_path(self, key: str) -> Optional[Path]:
        """The real filesystem path for `key`, for callers (ffprobe, PIL,
        thumbnail generation, ...) that need one - or `None` when this driver
        has no local files of its own (e.g. `S3FileStorageDriver`), in which
        case the caller must materialize a local copy itself."""
        return None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically: temp file in the same directory,
    fsync, then `os.replace()`. A crash or full disk mid-write can never leave
    a truncated file at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f"{path.name}.{generate_ulid()}.part"
    try:
        with open(tmp_path, "wb") as f:
            written = f.write(data)
            if written != len(data):
                raise OSError(f"Short write: expected {len(data)} bytes, wrote {written}")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _atomic_copy_file(source_path: Path, dest_path: Path) -> int:
    """Copy `source_path` to `dest_path` atomically, streaming through a
    bounded buffer instead of holding the whole file in memory (unlike
    `_atomic_write_bytes`, which is the right tradeoff for small in-memory
    payloads like an already-encoded image but not for a multi-hundred-MB
    video file). Same atomicity guarantee: write to a temp file in the same
    directory, fsync, then `os.replace()` into place.

    Raises on any failure (missing source, short copy, OSError, ...); the
    temp file is removed before re-raising. Returns the copied byte count.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.parent / f"{dest_path.name}.{generate_ulid()}.part"
    try:
        with open(source_path, "rb") as src, open(tmp_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp_path, dest_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    return dest_path.stat().st_size


class LocalFileStorageDriver(FileStorageDriver):
    """Stores objects as ordinary files under `base_dir`. Byte-identical to
    the pre-driver behavior - this is what every install runs by default."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        normalized = validate_key(key)
        resolved_base = self.base_dir.resolve()
        target = (resolved_base / normalized).resolve()
        if not target.is_relative_to(resolved_base):
            raise StorageKeyError(f"Storage key escapes the storage root: {key!r}")
        return target

    def put_bytes(self, key: str, data: bytes) -> int:
        path = self._resolve(key)
        _atomic_write_bytes(path, data)
        return len(data)

    def put_file(self, key: str, source_path: Path) -> int:
        path = self._resolve(key)
        return _atomic_copy_file(source_path, path)

    def get_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(f"No such storage key: {key!r}")
        return path.read_bytes()

    def get_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(f"No such storage key: {key!r}")

        def _iter() -> Iterator[bytes]:
            with open(path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk

        return _iter()

    def delete(self, key: str) -> bool:
        path = self._resolve(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def size(self, key: str) -> Optional[int]:
        path = self._resolve(key)
        if not path.is_file():
            return None
        return path.stat().st_size

    def local_path(self, key: str) -> Optional[Path]:
        return self._resolve(key)
