"""Storage-key convention for published inspiration media.

Shared by `src.features.inspirations.operations` (writing/deleting copies) and the media-serving
route (reading them back) so both agree on exactly one layout:
`inspirations/<inspiration_id>/<filename>`.
"""

from src.platform.filesystem.storage_driver import validate_key


def inspiration_media_key(inspiration_id: str, filename: str) -> str:
    """The storage key for one of an inspiration's copied media files.

    Raises `StorageKeyError` (like `validate_key`) if `filename` would escape
    the inspiration's own directory.
    """
    return validate_key(f"inspirations/{inspiration_id}/{filename}")
