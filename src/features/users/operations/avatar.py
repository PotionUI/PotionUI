"""
User avatar upload/clear/resolve.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"validation failed" (the controller converts that to an HTTP
response).
"""
import logging
import uuid
from pathlib import Path
from typing import Optional

from src.features.users.repository import UserRepository
from src.platform.security.user import User
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_ALLOWED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _avatars_directory(settings: Settings) -> Path:
    """`storage/avatars/`, created lazily. Global, not per-user: filenames
    are opaque uuid4s, so there is no need for per-user isolation here."""
    storage_dir = settings.get_file_storage_directory()
    avatars_dir = Path(storage_dir) / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    return avatars_dir


def _validate_avatar_path(path: Path, base: Path) -> bool:
    try:
        return path.resolve().is_relative_to(base.resolve())
    except Exception:
        return False


def delete_avatar_file(settings: Settings, filename: str) -> None:
    """Delete an avatar file from disk, best-effort. Used both by
    `delete_avatar` (clearing the column) and by `src.features.users.operations.crud.delete`
    (removing a deleted user's leftover avatar)."""
    avatars_dir = _avatars_directory(settings)
    path = avatars_dir / filename
    if not _validate_avatar_path(path, avatars_dir):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning(f"Failed to delete avatar file: {filename}")


def resolve_avatar_path(settings: Settings, filename: str) -> Path:
    """Resolve `filename` to a real file under the avatars directory.

    Raises ValueError uniformly for both traversal attempts and missing
    files, so a caller serving this over `<img src>` cannot distinguish
    the two from the response.
    """
    avatars_dir = _avatars_directory(settings)
    candidate = avatars_dir / filename

    if not _validate_avatar_path(candidate, avatars_dir):
        raise ValueError("Avatar not found")

    resolved = candidate.resolve()
    if not resolved.is_file():
        raise ValueError("Avatar not found")

    return resolved


def upload_avatar(
    repository: UserRepository,
    settings: Settings,
    user_id: str,
    file_data: bytes,
    filename: Optional[str],
    content_type: Optional[str],
) -> User:
    """
    Replace a user's avatar.

    Raises:
        ValueError: If the user is missing, the upload fails validation,
            or the write fails
    """
    user = repository.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    if not content_type or not content_type.startswith("image/"):
        raise ValueError("Only image files are allowed")

    ext = Path(filename).suffix.lower() if filename else ""
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        raise ValueError(
            "Unsupported image extension. Allowed: " + ", ".join(sorted(AVATAR_ALLOWED_EXTENSIONS))
        )

    if len(file_data) > AVATAR_MAX_BYTES:
        raise ValueError("Avatar exceeds the 5MB size limit")

    avatars_dir = _avatars_directory(settings)
    new_filename = f"{uuid.uuid4()}{ext}"
    (avatars_dir / new_filename).write_bytes(file_data)

    updated_user = repository.update(user_id, avatar_filename=new_filename)
    if not updated_user:
        delete_avatar_file(settings, new_filename)
        raise ValueError("Failed to update avatar")

    if user.avatar_filename:
        delete_avatar_file(settings, user.avatar_filename)

    logger.info(f"Updated avatar for user: {user_id}")
    return updated_user


def delete_avatar(repository: UserRepository, settings: Settings, user_id: str) -> User:
    """
    Clear a user's avatar and remove its file.

    Raises:
        ValueError: If the user is missing or the update fails
    """
    user = repository.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    if user.avatar_filename:
        delete_avatar_file(settings, user.avatar_filename)

    updated_user = repository.update(user_id, avatar_filename=None)
    if not updated_user:
        raise ValueError("Failed to update avatar")

    logger.info(f"Cleared avatar for user: {user_id}")
    return updated_user
