"""Frozen collaborators bundle for the library operations layer.

Every library operation needs some slice of seven infrastructure legs (the
library's own filtered-read repository, uploads CRUD, tags, generation files,
path resolution, the file store, and the storage driver). Bundling them once
here - built in the composition root and passed to `operations` functions as a
single object - avoids threading seven positional collaborators through every
call site. A plain, frozen data holder (no behavior beyond field access),
matching `PromptDatabaseCollaborators` (the reference shape for a
wide-collaborator dissolution).
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.features.library.repository import LibraryRepository
from src.features.media.upload_repository import UploadRepository

if TYPE_CHECKING:
    from src.features.generation.file_repository import FileRepository
    from src.features.media.file_resolver import FilePathResolver
    from src.features.tags.repository import TagRepository
    from src.platform.filesystem import FileStore
    from src.platform.filesystem.storage_driver import FileStorageDriver


@dataclass(frozen=True)
class LibraryCollaborators:
    repository: LibraryRepository
    upload_repository: UploadRepository
    tag_repository: "TagRepository"
    file_repository: "FileRepository"
    file_resolver: "FilePathResolver"
    file_store: "FileStore"
    storage_driver: "FileStorageDriver"
