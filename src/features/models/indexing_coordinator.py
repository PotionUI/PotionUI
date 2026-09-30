import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.features.models.exceptions import ModelIndexingException
from src.features.models.locations_repository import ModelLocationsRepository
from src.platform.database.rows import dt_iso, now_iso, now_utc
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.indexer import ModelScanner
from src.features.models.native_availability_reconciler import (
    NativeAvailabilityProjector,
    native_availability_projector as _default_native_availability_projector,
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
MAX_REPORTED_SKIPPED_DUPLICATES = 200


def spawn_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="model-indexing", daemon=True).start()


class ModelIndexingCoordinator:
    """Drives the directory scanner and the plugin hooks that gate indexing."""

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        scanner: ModelScanner,
        backend_registry: Optional[Any] = None,
        native_availability_projector: Optional[NativeAvailabilityProjector] = None,
        locations_repository: Optional[ModelLocationsRepository] = None,
        spawn: Callable[[Callable[[], None]], None] = spawn_daemon_thread,
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.scanner = scanner
        self.backend_registry = backend_registry
        self.native_availability_projector = (
            native_availability_projector or _default_native_availability_projector
        )
        self.locations_repo = locations_repository or ModelLocationsRepository()
        self._spawn = spawn

        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._cancel_event = threading.Event()

        self._state = STATE_IDLE
        self._queued = False
        self._trigger: Optional[str] = None
        self._pending_restart_trigger: Optional[str] = None
        self._started_at = None
        self._finished_at = None
        self._total = 0
        self._processed = 0
        self._indexed = 0
        self._found_on_disk: Optional[int] = None
        self._scanned_roots: List[str] = []
        self._found_by_root: Dict[str, int] = {}
        self._failed_by_root: Dict[str, int] = {}
        self._failed_files: List[Dict[str, str]] = []
        self._failed_files_total = 0
        self._skipped_duplicates: List[Dict[str, Any]] = []
        self._skipped_duplicates_total = 0
        self._type_conflicts: List[Dict[str, Any]] = []
        self._error: Optional[str] = None
        self._last_single_index: Optional[Dict[str, Any]] = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = {
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
                "skipped_duplicates": list(self._skipped_duplicates),
                "skipped_duplicates_total": self._skipped_duplicates_total,
                "error": self._error,
                "last_single_index": self._last_single_index,
            }
            type_conflicts = list(self._type_conflicts)
            found_by_root = dict(self._found_by_root)
            failed_by_root = dict(self._failed_by_root)

        snapshot["roots"] = self._roots_status(found_by_root, failed_by_root)
        snapshot["conflicts"] = self._safe_locations_call(self.locations_repo.list_conflicts, []) + type_conflicts
        snapshot["needs_type_total"] = self._safe_locations_call(lambda: self.scanner.types.count_needs_type(), 0)
        snapshot["classified_total"] = self._safe_locations_call(lambda: self.scanner.types.count_header_classified(), 0)
        snapshot["duplicates"] = self._safe_locations_call(self.locations_repo.list_duplicate_models, [])
        return snapshot

    @staticmethod
    def _safe_locations_call(fn, default):
        try:
            return fn()
        except Exception as e:
            logger.debug(f"[MODEL_INDEX] status() location aggregate failed: {e}")
            return default

    def _roots_status(self, found_by_root: Dict[str, int], failed_by_root: Dict[str, int]) -> List[Dict[str, Any]]:
        try:
            roots = list(self.scanner.resolver.roots())
        except Exception:
            return []
        aggregates = self._safe_locations_call(self.locations_repo.aggregate_by_root_and_type, {})
        indexed_by_root: Dict[str, int] = {}
        for (root_id, _model_type), agg in aggregates.items():
            indexed_by_root[root_id] = indexed_by_root.get(root_id, 0) + agg.get("indexed_files", 0)
        return [
            {
                "root_id": root.id,
                "label": root.label,
                "state": root.state,
                "found": found_by_root.get(root.id, 0),
                "indexed": indexed_by_root.get(root.id, 0),
                "failed": failed_by_root.get(root.id, 0),
            }
            for root in roots
        ]

    def start_indexing(self, trigger: str = "manual") -> Dict[str, Any]:
        if self._begin_indexing(trigger):
            try:
                self._spawn(self._run_scheduled)
            except Exception as e:
                logger.error(f"Could not start the model indexing worker: {e}")
                with self._lock:
                    self._state = STATE_FAILED
                    self._error = str(e)
                    self._finished_at = now_utc()
                    self._queued = False
                raise ModelIndexingException(f"Could not start model indexing: {e}") from e
        return self.status()

    def _begin_indexing(self, trigger: str, queued: bool = True) -> bool:
        with self._lock:
            already_running = self._state in RUNNING_STATES

        if already_running:
            logger.info("Model indexing already running; ignoring start_indexing() call")
            return False

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
            if self._state in RUNNING_STATES:
                return False
            self._state = STATE_SCANNING
            self._queued = queued
            self._trigger = trigger
            self._started_at = now_utc()
            self._finished_at = None
            self._total = 0
            self._processed = 0
            self._indexed = 0
            self._found_on_disk = None
            self._scanned_roots = self._scanned_roots_snapshot()
            self._found_by_root = {}
            self._failed_by_root = {}
            self._failed_files = []
            self._failed_files_total = 0
            self._error = None
            self._pending_restart_trigger = None

        logger.info(f"Starting model indexing (trigger={trigger})")
        return True

    def _scanned_roots_snapshot(self) -> List[str]:
        try:
            return list(self.scanner.resolver.online_root_ids())
        except Exception:
            return []

    def _run_scheduled(self) -> None:
        with self._run_lock:
            with self._lock:
                if not self._queued:
                    return
                self._queued = False
            self._cancel_event.clear()
            try:
                self._run_until_settled()
            except Exception as e:
                logger.error(f"Scheduled model indexing crashed: {e}")
                with self._lock:
                    self._state = STATE_FAILED
                    self._error = str(e)
                    self._finished_at = now_utc()
                    self._pending_restart_trigger = None

    def _run_until_settled(self) -> None:
        self._execute_indexing()
        while True:
            with self._lock:
                pending = self._pending_restart_trigger
                if pending is None:
                    break
                self._pending_restart_trigger = None
            try:
                begun = self._begin_indexing(trigger=pending, queued=False)
            except ModelIndexingException:
                break
            if not begun:
                break
            self._cancel_event.clear()
            self._execute_indexing()

    def _execute_indexing(self) -> None:
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
            return
        finally:
            self.scanner.set_progress_callback(None)

        logger.info(f"Model indexing completed: {result}")

        failed = result.get('failed_files') or []
        normalized_failures = [
            f if isinstance(f, dict) else {"path": f, "error": "Failed to hash or index this file"}
            for f in failed
        ]
        skipped_duplicates = result.get('skipped_duplicates') or []
        cancelled = bool(result.get('cancelled')) or self._cancel_event.is_set()

        with self._lock:
            if not cancelled:
                self._skipped_duplicates = skipped_duplicates[:MAX_REPORTED_SKIPPED_DUPLICATES]
                self._skipped_duplicates_total = len(skipped_duplicates)
            self._indexed = result.get('indexed', 0)
            self._processed = result.get('new_files', self._processed) + result.get('classified', 0)
            self._type_conflicts = list(result.get('type_conflicts') or [])
            self._found_on_disk = result.get('found_on_disk', result.get('total', 0))
            self._found_by_root = result.get('found_by_root', {})
            self._failed_by_root = result.get('failed_by_root', {})
            self._failed_files_total = len(normalized_failures)
            self._failed_files = normalized_failures[:MAX_REPORTED_FAILED_FILES]
            self._finished_at = now_utc()
            self._state = STATE_CANCELLED if cancelled else STATE_DONE

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
            if running and not self._queued:
                self._pending_restart_trigger = trigger
                self._cancel_event.set()
        if running:
            return self.status()
        return self.start_indexing(trigger=trigger)

    def _reconcile_native_availability(self) -> None:
        from src.features.recipes.executors._async_bridge import run_sync

        try:
            run_sync(self.native_availability_projector.reconcile(self.backend_registry))
        except Exception as e:
            logger.warning(f"Error reconciling native availability after indexing: {e}")

    def index_path(self, path: str) -> Dict[str, Any]:
        candidate = Path(path)
        loc = self.scanner.resolver.to_logical(candidate)
        if loc is None:
            raise ModelIndexingException(f"'{path}' is not under any known model root")

        root = next((r for r in self.scanner.resolver.roots() if r.id == loc.root_id), None)
        if root is None:
            raise ModelIndexingException(f"'{path}' resolved to an unknown root")
        if not self.scanner.resolver.is_online_for(loc.root_id, loc.model_type):
            raise ModelIndexingException(f"Root '{root.label}' is {root.state}")

        try:
            is_dir = candidate.is_dir()
            stat = candidate.stat()
        except OSError as e:
            raise ModelIndexingException(f"Cannot read '{path}': {e}") from e

        found = self.scanner.found_file_for(loc, candidate, stat.st_size, stat.st_mtime_ns, is_dir)
        outcome = self.scanner.index_file(found)
        model = outcome.model
        duplicate_of = outcome.duplicate_of.to_dict() if outcome.duplicate_of is not None else None

        with self._lock:
            self._last_single_index = {
                "path": str(candidate),
                "model_id": model.id if model else None,
                "at": dt_iso(now_utc()),
            }

        if model is not None:
            self._reconcile_native_availability()

        return {"model_id": model.id if model else None, "indexed": model is not None, "duplicate_of": duplicate_of}

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

        return True

    def cleanup_deleted_models(self) -> Dict[str, Any]:
        try:
            online_root_ids = list(self.scanner.resolver.online_root_ids())
        except Exception:
            online_root_ids = []
        deleted = self.locations_repo.delete_missing_for_roots(online_root_ids)
        return {
            "message": "Cleanup completed",
            "deleted_locations": deleted,
            "roots_checked": len(online_root_ids),
        }
