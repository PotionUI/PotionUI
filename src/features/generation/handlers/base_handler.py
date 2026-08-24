"""
Base handler class for generation output processing.

This module provides the abstract base class that all generation output handlers
must inherit from. Handlers are responsible for processing different types of
generation outputs (images, videos, artifacts, etc.) and performing actions such
as saving files, updating databases, or transforming data.

The base handler enforces a consistent interface across all handler implementations
and provides common initialization logic for generation context.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from src.pipelines.outputs import GenerationOutput
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)

_warned_storage_dir_fallback = False


class BaseGenerationOutputHandler(ABC):
    """Base class for generation output handlers."""

    def __init__(
        self,
        generation_id: str,
        user_id: Optional[str] = None,
        settings_manager: Optional[SettingsManager] = None,
        storage_driver: Optional[FileStorageDriver] = None,
    ):
        """
        Initialize the handler with generation context.

        Args:
            generation_id: Current generation ID for organizing outputs
            user_id: User ID for file ownership (optional)
            settings_manager: Settings manager for accessing configuration (optional)
            storage_driver: Where saved generation output bytes actually live
                - local disk by default, optionally S3 (see
                `StorageSettingsManager`). Falls back to a local driver rooted
                at the configured storage directory when not injected (tests,
                callers that construct a handler directly).
        """
        self.generation_id = generation_id
        # `file_storage_directory` is a PER-USER setting, and every reader
        # resolves it with the owner (`bind_form` containment via the
        # orchestrator, `media.file_resolver` when serving). A subclass
        # resolving the storage root without `user_id` writes into the global
        # root while the readers look in the user's: the file exists, its
        # recorded relative path reads as plausible, and nothing can find it.
        self.user_id = user_id
        self.settings_manager = settings_manager
        self.image_counter = 0
        self._counter_seeded = False
        # `None` unless the caller (OutputProcessor) injects the container's
        # shared driver. Resolved lazily by each handler's own save path
        # (a local `LocalFileStorageDriver` rooted at the configured storage
        # directory) rather than eagerly here, matching how the pre-driver
        # code lazily constructed its own `FileStore` only when actually
        # saving - constructing eagerly would `mkdir` a real directory for
        # every handler, including ones that never save anything.
        self.storage_driver = storage_driver

    def _resolve_storage_driver(self) -> FileStorageDriver:
        """The driver to save generation output through - the injected one,
        or a local driver rooted at the configured storage directory when
        none was injected (tests, callers that construct a handler
        directly). Resolved lazily, on first use, not at `__init__` - see
        `storage_driver`'s docstring."""
        if self.storage_driver is not None:
            return self.storage_driver

        from src.platform.filesystem.storage_driver import LocalFileStorageDriver

        base_storage_dir = "storage"
        if self.settings_manager:
            try:
                configured = self.settings_manager.get_file_storage_directory(self.user_id)
                if configured:
                    base_storage_dir = configured
            except Exception:
                logger.warning(
                    "Failed to get file storage directory from settings for generation %s; using default",
                    self.generation_id, exc_info=True,
                )
        self.storage_driver = LocalFileStorageDriver(base_storage_dir)
        return self.storage_driver

    def _resolve_storage_dir(self) -> str:
        """Base storage directory from settings, falling back to 'storage'.
        Only used as `FileStore`'s local root for filename/mime helpers and
        `tmp`/`models` writes - actual `generations` bytes go through
        `self.storage_driver` regardless of this value."""
        base_storage_dir = "storage"
        if not self.settings_manager:
            return base_storage_dir

        try:
            configured_dir = self.settings_manager.get_file_storage_directory(self.user_id)
            if configured_dir:
                return configured_dir
            logger.debug("File storage directory setting not configured, using default 'storage'")
        except Exception as e:
            global _warned_storage_dir_fallback
            if not _warned_storage_dir_fallback:
                logger.warning(f"Failed to get file storage directory from settings, using default: {e}")
                _warned_storage_dir_fallback = True

        return base_storage_dir

    def seed_counter_from_persisted_files(self) -> None:
        """Start ``image_counter`` after every file already recorded for this
        generation. Handlers are constructed FRESH per output (see
        ``OutputProcessor._process_via_spec``), so a bare 0 start makes any
        SECOND file-producing output of one generation reuse the first one's
        numeric filenames — two gallery pipes in one pipeline (inline enhance)
        silently overwrote each other's originals and thumbnails on disk.
        """
        from src.features.generation.file_repository import file_repo

        try:
            self.image_counter = len(file_repo.get_generation_files(self.generation_id))
        except Exception:
            logger.warning(
                "could not seed file counter for generation %s; keeping %d",
                self.generation_id, self.image_counter, exc_info=True,
            )
        self._counter_seeded = True

    @abstractmethod
    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process the given output."""
        pass

    @abstractmethod
    def handle(self, output: GenerationOutput) -> Dict[str, Any]:
        """Process the generation output and return metadata."""
        pass
