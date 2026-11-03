"""Storage of generated artifacts on disk.

FileStore owns the naming and directory layout of everything a generation
produces, so callers name what they are saving, not where it goes.
"""

from .file_store import FileStore

__all__ = [
    "FileStore",
]
