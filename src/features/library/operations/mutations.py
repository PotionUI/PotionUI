"""Library write operations: delete, and copy-from-generation."""
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Dict, TYPE_CHECKING

from src.features.library.collaborators import LibraryCollaborators
from src.features.library.dto import LibraryItem
from src.features.library.mappers import upload_key, upload_to_item
from src.features.library.operations.guards import get_owned_or_raise
from src.features.media.records import Upload

if TYPE_CHECKING:
    from src.features.generation.records import File

logger = logging.getLogger(__name__)

# Generation `files.file_type` -> the library's `media_type` vocabulary.
_FILE_TYPE_TO_MEDIA_TYPE = {
    "IMAGE": "image",
    "VIDEO": "video",
    "AUDIO": "audio",
}


def delete_item(collaborators: LibraryCollaborators, item_id: str, user_id: str) -> None:
    """Delete one library item - DB row and file on disk.

    Tag and collection memberships go with the row (both junctions cascade
    from `uploads`, migration 115).

    Raises:
        ValueError: If no such item is owned by this user.
    """
    upload = get_owned_or_raise(collaborators, item_id, user_id)

    # Same containment-checked key the serve route uses.
    collaborators.storage_driver.delete(upload_key(upload.filename))

    collaborators.upload_repository.delete(upload.filename, user_id)


def copy_generation_file(collaborators: LibraryCollaborators, file_id: str, user_id: str) -> LibraryItem:
    """Copy one of the user's generated files into their library.

    The result is a resource, not a reference: fresh bytes under a fresh name
    in `storage/uploads/`, and an `uploads` row that carries only what a
    direct upload of the same file would carry - owner, name, media type,
    dimensions. No preset, no prompt, no seed, no generation id; the
    `uploads` table has nowhere to put them, which is what makes the copy
    indistinguishable from an upload rather than merely undecorated.

    Deleting the generation afterwards leaves this untouched - the bytes were
    copied, and no foreign key points back.

    Raises:
        ValueError: If the file is not found, not owned by this user, or not
            a media type the library holds.
    """
    file_record = collaborators.file_repository.get_by_id(file_id, user_id=user_id)
    if not file_record:
        raise ValueError("File not found")

    media_type = _FILE_TYPE_TO_MEDIA_TYPE.get((file_record.file_type or "").upper())
    if not media_type:
        raise ValueError(f"Cannot add a {file_record.file_type} file to the library")

    source = Path(collaborators.file_store.get_full_path(file_record.file_path))
    storage_root = Path(collaborators.file_store.base_storage_dir)
    if not collaborators.file_resolver.validate_path_security(source, storage_root):
        logger.warning(f"Refused library copy of out-of-tree file path: {file_record.file_path}")
        raise ValueError("File not found")
    if not source.exists():
        raise ValueError("File not found")

    # Same uuid-name convention as `MediaManager.upload_media`, so nothing
    # downstream can tell a copied item from an uploaded one by its name.
    filename = f"{uuid.uuid4()}{source.suffix}"
    key = upload_key(filename)
    written = collaborators.storage_driver.put_file(key, source)

    # The source generation file already paid for its thumbnails - copy those
    # bytes alongside it rather than regenerating them.
    thumbnail_paths = _copy_generation_thumbnails(collaborators, file_record, source.parent, Path(filename).stem)

    # The source's own basename, never the generation id: a name derived from
    # the generation would be exactly the back-reference this copy is
    # supposed to drop.
    original_filename = source.name
    mime_type = file_record.mime_type or mimetypes.guess_type(source.name)[0]

    upload = collaborators.upload_repository.create(Upload(
        user_id=user_id,
        filename=filename,
        original_filename=original_filename,
        media_type=media_type,
        mime_type=mime_type,
        width=file_record.width,
        height=file_record.height,
        duration_seconds=file_record.duration_seconds,
        fps=file_record.fps,
        file_size=file_record.file_size or written,
        thumbnail_small=thumbnail_paths.get('small'),
        thumbnail_medium=thumbnail_paths.get('medium'),
        thumbnail_large=thumbnail_paths.get('large'),
    ))

    logger.info(f"Copied generated file {file_id} into library as {filename}")
    return upload_to_item(upload, [])


def _copy_generation_thumbnails(
    collaborators: LibraryCollaborators, file_record: "File", source_dir: Path, new_stem: str
) -> Dict[str, str]:
    """Copy a generation file's thumbnail siblings into the uploads
    namespace, alongside its main copy.

    Best-effort: a missing or out-of-tree sibling just leaves that size
    unset, exactly like an upload whose own thumbnail generation failed - it
    never blocks the copy itself.
    """
    storage_root = Path(collaborators.file_store.base_storage_dir)
    thumbnail_paths: Dict[str, str] = {}

    for size in ("small", "medium", "large"):
        source_name = getattr(file_record, f"thumbnail_{size}", None)
        if not source_name:
            continue

        thumb_source = source_dir / source_name
        if not collaborators.file_resolver.validate_path_security(thumb_source, storage_root) or not thumb_source.exists():
            continue

        relative_path = f"thumbnails/{new_stem}_{size}{thumb_source.suffix}"
        try:
            collaborators.storage_driver.put_file(upload_key(relative_path), thumb_source)
            thumbnail_paths[size] = relative_path
        except Exception as e:
            logger.warning(f"Failed to copy {size} thumbnail for file {file_record.id}: {e}")
            continue

        # A video's static thumbnail has an animated sibling next to it (same
        # stem, `_animated.webp`) - carry it too so the copy's hover-preview
        # behaves exactly like the original.
        if (file_record.file_type or "").upper() == "VIDEO":
            animated_source = thumb_source.parent / f"{thumb_source.stem}_animated.webp"
            if animated_source.exists():
                try:
                    collaborators.storage_driver.put_file(
                        upload_key(f"thumbnails/{new_stem}_{size}_animated.webp"), animated_source
                    )
                except Exception as e:
                    logger.warning(f"Failed to copy animated {size} thumbnail for file {file_record.id}: {e}")

    return thumbnail_paths
