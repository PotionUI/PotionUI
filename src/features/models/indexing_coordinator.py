"""Coordinates full-directory (re)indexing and stale-entry cleanup.

The heavy lifting - scanning the models directory, hashing files, upserting rows -
belongs to the injected `ModelScanner`. This class starts those runs, fires the
plugin hooks around them, and prunes index rows whose files have vanished.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.features.models.exceptions import ModelIndexingException
from src.platform.database.rows import dt_iso, now_utc
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.indexer import ModelScanner
from src.features.models.native_availability_reconciler import (
    NativeAvailabilityReconciler,
    native_availability_reconciler as _default_native_availability_reconciler,
)
from src.features.models.repository import ModelRepository
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_SCANNING = "scanning"
STATE_INDEXING = "indexing"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_BLOCKED = "blocked"

RUNNING_STATES = (STATE_SCANNING, STATE_INDEXING)

MAX_REPORTED_FAILED_FILES = 50


class ModelIndexingCoordinator:
    """Drives the directory scanner and the plugin hooks that gate indexing."""

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        scanner: ModelScanner,
        backend_registry: Optional[Any] = None,
        native_availability_reconciler: Optional[NativeAvailabilityReconciler] = None,
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.scanner = scanner
        self.backend_registry = backend_registry
        self.native_availability_reconciler = (
            native_availability_reconciler or _default_native_availability_reconciler
        )

        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._cancel_event = threading.Event()

        self._state = STATE_IDLE
        self._executing = False
        self._trigger: Optional[str] = None
        self._pending_restart_trigger: Optional[str] = None
        self._started_at = None
        self._finished_at = None
        self._total = 0
        self._processed = 0
        self._indexed = 0
        self._found_on_disk: Optional[int] = None
        self._scanned_roots: List[str] = []
        self._failed_files: List[Dict[str, str]] = []
        self._failed_files_total = 0
        self._error: Optional[str] = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "trigger": self._trigger,
                "restart_pending": self._pending_restart_trigger is not None,
                "started_at": dt_iso(self._started_at),
                "finished_at": dt_iso(self._finished_at),
                "total": self._total,
                "processed": self._processed,
                "indexed": self._indexed,
                "found_on_disk": self._found_on_disk,
                "scanned_roots": list(self._scanned_roots),
                "failed_files": list(self._failed_files),
                "failed_files_total": self._failed_files_total,
                "error": self._error,
            }

    def start_indexing(self, trigger: str = "manual") -> Dict[str, Any]:
        with self._lock:
            already_running = self._state in RUNNING_STATES

        if already_running:
            logger.info("Model indexing already running; ignoring start_indexing() call")
            return self.status()

        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_index,
            {"action": "start_indexing"}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Indexing blocked by plugin")
            with self._lock:
                self._state = STATE_BLOCKED
                self._trigger = trigger
                self._error = reason
                self._finished_at = now_utc()
            raise ModelIndexingException(reason)

        with self._lock:
            self._state = STATE_SCANNING
            self._trigger = trigger
            self._started_at = now_utc()
            self._finished_at = None
            self._total = 0
            self._processed = 0
            self._indexed = 0
            self._found_on_disk = None
            self._scanned_roots = self._scanned_roots_snapshot()
            self._failed_files = []
            self._failed_files_total = 0
            self._error = None
            self._pending_restart_trigger = None
        self._cancel_event.clear()

        logger.info(f"Starting model indexing (trigger={trigger})")
        return self.status()

    def _scanned_roots_snapshot(self) -> List[str]:
        try:
            return [str(self.scanner.models_dir)]
        except Exception:
            return []

    def run_indexing(self) -> None:
        if not self._run_lock.acquire(blocking=False):
            with self._lock:
                if not self._executing:
                    self._pending_restart_trigger = self._trigger
            logger.info("Model indexing already executing; ignoring concurrent run_indexing() call")
            return
        try:
            self._execute_indexing()
            while True:
                with self._lock:
                    pending = self._pending_restart_trigger
                    if pending is None:
                        break
                    self._pending_restart_trigger = None
                try:
                    self.start_indexing(trigger=pending)
                except ModelIndexingException:
                    break
                self._execute_indexing()
        finally:
            self._run_lock.release()

    def _execute_indexing(self) -> None:
        with self._lock:
            self._executing = True

        def on_progress(current: int, total: int, message: str) -> None:
            with self._lock:
                if total > 0 and self._state == STATE_SCANNING:
                    self._state = STATE_INDEXING
                if total > 0:
                    self._total = total
                self._processed = current

        def is_cancelled() -> bool:
            return self._cancel_event.is_set()

        try:
            self.scanner.set_progress_callback(on_progress)
            result = self.scanner.index_models(cancel_check=is_cancelled)
        except Exception as e:
            logger.error(f"Error during background indexing: {e}")
            with self._lock:
                self._state = STATE_FAILED
                self._error = str(e)
                self._finished_at = now_utc()
                self._executing = False
            return
        finally:
            self.scanner.set_progress_callback(None)

        logger.info(f"Model indexing completed: {result}")

        failed = result.get('failed_files') or []
        normalized_failures = [
            f if isinstance(f, dict) else {"path": f, "error": "Failed to hash or index this file"}
            for f in failed
        ]
        cancelled = bool(result.get('cancelled')) or self._cancel_event.is_set()

        with self._lock:
            self._indexed = result.get('indexed', 0)
            self._processed = result.get('new_files', self._processed)
            self._found_on_disk = result.get('found_on_disk', result.get('total', 0))
            self._failed_files_total = len(normalized_failures)
            self._failed_files = normalized_failures[:MAX_REPORTED_FAILED_FILES]
            self._finished_at = now_utc()
            self._state = STATE_CANCELLED if cancelled else STATE_DONE
            self._executing = False

        execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.after_index,
            {"result": result}
        )

        if cancelled:
            return

        self._reconcile_native_availability()

    def cancel_and_restart(self, trigger: str = "location_change") -> Dict[str, Any]:
        with self._lock:
            running = self._state in RUNNING_STATES
            if running:
                self._pending_restart_trigger = trigger
        if running:
            self._cancel_event.set()
            return self.status()
        return self.start_indexing(trigger=trigger)

    def _reconcile_native_availability(self) -> None:
        from src.features.recipes.executors._async_bridge import run_sync

        try:
            run_sync(self.native_availability_reconciler.reconcile(self.backend_registry))
        except Exception as e:
            logger.warning(f"Error reconciling native availability after indexing: {e}")

    def count_unindexed(self) -> Dict[str, Any]:
        """Cheap directory-walk + DB diff: how many files on disk await indexing,
        by type. No hashing, no writes."""
        return self.scanner.count_unindexed()

    def resume_interrupted_indexing(self) -> bool:
        if not self.count_unindexed().get("total"):
            return False

        try:
            self.start_indexing(trigger="startup")
        except ModelIndexingException as e:
            logger.warning(f"Skipped resuming interrupted model indexing: {e}")
            return False

        self.run_indexing()
        return True

    def cleanup_deleted_models(self) -> Dict[str, Any]:
        """Remove index rows whose backing file no longer exists on disk."""
        all_models = self.model_repo.get_all(include_providers=False, include_files=False)
        deleted_count = 0

        for model in all_models:
            if model.file_path and not Path(model.file_path).exists():
                self.model_repo.delete(model.id)
                deleted_count += 1
                logger.debug(f"Removed deleted model from index: {model.filename}")

        return {
            "message": "Cleanup completed",
            "deleted_from_index": deleted_count,
            "total_checked": len(all_models)
        }
