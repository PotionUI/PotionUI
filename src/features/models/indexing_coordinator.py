"""Coordinates full-directory (re)indexing and stale-entry cleanup.

The heavy lifting - scanning the models directory, hashing files, upserting rows -
belongs to the injected `ModelScanner`. This class starts those runs, fires the
plugin hooks around them, and prunes index rows whose files have vanished.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from src.features.models.exceptions import ModelIndexingException
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.indexer import ModelScanner
from src.features.models.repository import ModelRepository
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)


class ModelIndexingCoordinator:
    """Drives the directory scanner and the plugin hooks that gate indexing."""

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        scanner: ModelScanner,
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.scanner = scanner

    def start_indexing(self) -> Dict[str, Any]:
        """Announce a background index run, letting a plugin veto it first.

        Fires model_index.before_index (can block). Raises ModelIndexingException
        if a plugin blocks; the caller schedules `run_indexing` on success.
        """
        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_index,
            {"action": "start_indexing"}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Indexing blocked by plugin")
            raise ModelIndexingException(reason)

        logger.info("Starting model indexing via manager")
        return {
            "message": "Model indexing started in background",
            "status": "running"
        }

    def run_indexing(self) -> None:
        """Execute the actual indexing (background task); fires model_index.after_index."""
        try:
            result = self.scanner.index_models()
            logger.info(f"Model indexing completed: {result}")

            execute_hook(
                self.plugins,
                MODEL_INDEX_HOOKS.after_index,
                {"result": result}
            )
        except Exception as e:
            logger.error(f"Error during background indexing: {e}")

    def cleanup_deleted_models(self) -> Dict[str, Any]:
        """Remove index rows whose backing file no longer exists on disk."""
        all_models = self.model_repo.get_all(include_providers=False)
        deleted_count = 0

        for model in all_models:
            if not Path(model.file_path).exists():
                self.model_repo.delete(model.id)
                deleted_count += 1
                logger.debug(f"Removed deleted model from index: {model.filename}")

        return {
            "message": "Cleanup completed",
            "deleted_from_index": deleted_count,
            "total_checked": len(all_models)
        }
