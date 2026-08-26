"""Save-to-library and unsave operations."""
import logging
import uuid
from pathlib import Path

from src.features.inspirations.collaborators import InspirationCollaborators
from src.features.inspirations.storage import inspiration_media_key
from src.features.media.records import Upload
from src.platform.filesystem.storage_driver import uploads_key

logger = logging.getLogger(__name__)


def save_to_library(collaborators: InspirationCollaborators, inspiration_id: str, user_id: str) -> int:
    """Copy this inspiration's media into the caller's library and mark it saved.

    Mirrors `src.features.library.operations.mutations.copy_generation_file`'s
    storage/DB writes, one file at a time, sourcing bytes from the
    inspiration's own copies rather than a generation.

    Raises:
        ValueError: If the inspiration is not found, or none of its media
            files could be copied.
    """
    insp = collaborators.repository.get_by_id(inspiration_id)
    if not insp:
        raise ValueError("Inspiration not found")

    storage_root = Path(collaborators.file_store.base_storage_dir)
    copied = 0
    for entry in insp.media:
        source = Path(collaborators.file_store.get_full_path(inspiration_media_key(inspiration_id, entry["filename"])))
        if not collaborators.file_resolver.validate_path_security(source, storage_root):
            logger.warning(f"Refused library copy of out-of-tree inspiration file: {entry.get('filename')!r}")
            continue
        if not source.exists():
            continue

        filename = f"{uuid.uuid4()}{source.suffix}"
        written = collaborators.storage_driver.put_file(uploads_key(filename), source)

        collaborators.upload_repository.create(Upload(
            user_id=user_id,
            filename=filename,
            original_filename=entry.get("filename") or filename,
            media_type=entry.get("type"),
            mime_type=entry.get("mime_type"),
            width=entry.get("width"),
            height=entry.get("height"),
            duration_seconds=entry.get("duration_seconds"),
            fps=entry.get("fps"),
            file_size=entry.get("file_size") or written,
        ))
        copied += 1

    if copied == 0 and insp.media:
        raise ValueError("Could not copy any files into your library")

    collaborators.repository.create_save(user_id, inspiration_id)
    return collaborators.repository.count_saves(inspiration_id)


def unsave(collaborators: InspirationCollaborators, inspiration_id: str, user_id: str) -> int:
    """Remove the save marker - the library copies made by `save_to_library` stay."""
    collaborators.repository.delete_save(user_id, inspiration_id)
    return collaborators.repository.count_saves(inspiration_id)
