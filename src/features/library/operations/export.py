"""Library export: package the caller's own library items into one zip.

Mirrors `GenerationHistoryArchive.export_zip`'s shape (spooled temp file,
skip-missing-on-disk, ownership verified per item) but over flat `uploads`
rows instead of per-generation file groups, so entries are named by the
item's own filename rather than nested under a generation id.
"""
import logging
import os
import tempfile
import zipfile
from typing import List, Tuple

from src.features.library.collaborators import LibraryCollaborators
from src.features.library.mappers import upload_key
from src.platform.filesystem.storage_driver import local_copy

logger = logging.getLogger(__name__)

# Matches `GenerationHistoryArchive._EXPORT_SPOOL_MAX_MEMORY_BYTES` - past
# this many bytes the archive rolls over to a temp file on disk instead of
# growing the process's memory.
_EXPORT_SPOOL_MAX_MEMORY_BYTES = 10 * 1024 * 1024


def export_zip(
    collaborators: LibraryCollaborators,
    item_ids: List[str],
    user_id: str,
) -> Tuple[tempfile.SpooledTemporaryFile, str]:
    """Zip the given library items' files, each ownership-checked.

    Entries are named after the item's `original_filename` (falling back to
    its storage filename), de-duplicated with a numeric suffix on collision
    since two items can legitimately share a display name. An item missing
    on disk is skipped with a warning, matching the generation export.

    Returns:
        Tuple of (zip_file, suggested_filename). `zip_file` is a
        `SpooledTemporaryFile` seeked to 0 - the caller reads it and is
        responsible for closing it.

    Raises:
        ValueError: If any item is not owned by this user.
    """
    zip_buffer = tempfile.SpooledTemporaryFile(max_size=_EXPORT_SPOOL_MAX_MEMORY_BYTES)
    used_names: set = set()

    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item_id in item_ids:
                upload = collaborators.upload_repository.get_by_id(item_id, user_id)
                if not upload:
                    raise ValueError("Library item not found")

                key = upload_key(upload.filename)
                if not collaborators.storage_driver.exists(key):
                    logger.warning(
                        f"Skipping missing file for export: {upload.filename} (item {item_id})"
                    )
                    continue

                arcname = _unique_name(upload.original_filename or upload.filename, used_names)
                suffix = os.path.splitext(upload.filename)[1]
                with local_copy(collaborators.storage_driver, key, suffix) as local_path:
                    zf.write(local_path, arcname)
    except Exception:
        zip_buffer.close()
        raise

    zip_buffer.seek(0)
    return zip_buffer, "potionui-library-export.zip"


def _unique_name(name: str, used: set) -> str:
    if name not in used:
        used.add(name)
        return name

    stem, ext = os.path.splitext(name)
    candidate, i = name, 2
    while candidate in used:
        candidate = f"{stem} ({i}){ext}"
        i += 1
    used.add(candidate)
    return candidate
