import logging
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from src.features.models.digest_queue_repository import PRIORITY_CHOSEN
from src.features.models.digests import POLICY_UNSET
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
from src.platform.filesystem.model_types import header_classified_types
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
UNSUCCESSFUL_STATES = (STATE_FAILED, STATE_BLOCKED, STATE_CANCELLED)
UNSUCCESSFUL_MESSAGES = {
    STATE_FAILED: "Model indexing failed",
    STATE_BLOCKED: "Model indexing was blocked",
    STATE_CANCELLED: "Model indexing was cancelled",
}
SHUTDOWN_MESSAGE = "Model indexing is shutting down"
CONTESTED_WARNING = "Another file has exactly the same size, so this one is being compared with it before it is added."

AFTER_DIGESTS_MIN_SECONDS = 5.0
STARTUP_REASON = "startup"

MAX_REPORTED_FAILED_FILES = 50
MAX_REPORTED_SKIPPED_DUPLICATES = 200


def spawn_daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="model-indexing", daemon=True).start()


def start_daemon_timer(delay: float, target: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, target)
    timer.daemon = True
    timer.start()
    return timer


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
        type_manager: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
        start_timer: Callable[[float, Callable[[], None]], Any] = start_daemon_timer,
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
        self.type_manager = type_manager
        self._clock = clock
        self._start_timer = start_timer
        self._after_digests_ids: Set[str] = set()
        self._after_digests_at: Optional[float] = None
        self._after_digests_timer: Optional[Any] = None

        self._lock = threading.Lock()
        self._settled = threading.Condition(self._lock)
        self._run_lock = threading.Lock()
        self._worker_active = False
        self._shutdown = False
        self._reproject_pending = False
        self._last_result: Optional[Dict[str, Any]] = None
        self._progress_listeners: List[Callable[[int, int, str], None]] = []

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
        self._pins_awaiting_digest: Dict[str, Any] = {}
        self._pins_awaiting_contest: Dict[str, Any] = {}
        self.scanner.contested_listener = self._on_contested_resolved

    @property
    def state(self) -> str:
        return self._state

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
        digests = self.scanner.digests
        if digests is not None:
            snapshot["digests"] = self._safe_locations_call(digests.status, None)
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
        indexed_by_root: Dict[str, int] = self._safe_locations_call(self.locations_repo.indexed_files_by_root, {})
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
            self._spawn_worker()
        return self.status()

    def _spawn_worker(self) -> None:
        try:
            self._spawn(self._run_scheduled)
        except Exception as e:
            logger.error(f"Could not start the model indexing worker: {e}")
            with self._settled:
                self._state = STATE_FAILED
                self._error = str(e)
                self._finished_at = now_utc()
                self._queued = False
                self._settled.notify_all()
            raise ModelIndexingException(f"Could not start model indexing: {e}") from e

    def _is_busy_locked(self) -> bool:
        return self._state in RUNNING_STATES or self._queued or self._worker_active

    def _join_or_start(self, trigger: str) -> None:
        while True:
            with self._lock:
                if self._shutdown:
                    raise ModelIndexingException(SHUTDOWN_MESSAGE)
                if self._is_busy_locked():
                    if self._pending_restart_trigger is None:
                        self._pending_restart_trigger = trigger
                    return
            if self._begin_indexing(trigger):
                self._spawn_worker()
                return

    def run_and_wait(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        trigger: str = "recipe",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        if on_progress is not None:
            self._progress_listeners.append(on_progress)
        try:
            self._join_or_start(trigger)
            deadline = None if timeout is None else time.monotonic() + timeout
            with self._settled:
                while self._is_busy_locked():
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise ModelIndexingException("Timed out waiting for model indexing to finish")
                    self._settled.wait(remaining)
                state = self._state
                error = self._error
                result = dict(self._last_result or {})
            if state in UNSUCCESSFUL_STATES:
                raise ModelIndexingException(error or UNSUCCESSFUL_MESSAGES[state])
            return result
        finally:
            if on_progress is not None and on_progress in self._progress_listeners:
                self._progress_listeners.remove(on_progress)

    def reproject(self, trigger: str = "roots_change") -> Dict[str, Any]:
        with self._lock:
            shutting_down = self._shutdown
            busy = self._is_busy_locked()
            if busy and not shutting_down:
                self._reproject_pending = True
        if not busy and not shutting_down:
            try:
                self._spawn(self._reconcile_native_availability)
            except Exception as e:
                logger.error(f"Could not refresh native availability after '{trigger}': {e}")
        return self.status()

    def shutdown(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._settled:
            self._shutdown = True
            self._pending_restart_trigger = None
            self._reproject_pending = False
            if self._after_digests_timer is not None:
                self._after_digests_timer.cancel()
                self._after_digests_timer = None
            self._after_digests_ids = set()
            while self._is_busy_locked():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._settled.wait(remaining)
        return True

    def update_settings(self, level: Optional[str] = None, policy: Optional[str] = None) -> Dict[str, Any]:
        governor = self.scanner.governor
        digests = self.scanner.digests
        if level is not None:
            if digests is not None:
                digests.set_level(level)
            else:
                governor.set_level(level)
        if policy is not None and digests is not None:
            digests.set_policy(policy)
        result = {"level": governor.level()}
        if digests is not None:
            result["policy"] = digests.policy
        return result

    def _begin_indexing(self, trigger: str, queued: bool = True) -> bool:
        with self._lock:
            if self._shutdown:
                raise ModelIndexingException(SHUTDOWN_MESSAGE)
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
            if self._shutdown:
                raise ModelIndexingException(SHUTDOWN_MESSAGE)
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
            with self._settled:
                if not self._queued:
                    return
                self._queued = False
                if self._shutdown:
                    self._state = STATE_CANCELLED
                    self._finished_at = now_utc()
                    self._settled.notify_all()
                    return
                self._worker_active = True
            try:
                self._run_until_settled()
            except Exception as e:
                logger.error(f"Scheduled model indexing crashed: {e}")
                with self._lock:
                    self._state = STATE_FAILED
                    self._error = str(e)
                    self._finished_at = now_utc()
                    self._pending_restart_trigger = None
            finally:
                with self._settled:
                    self._worker_active = False
                    self._settled.notify_all()

    def _run_until_settled(self) -> None:
        self._execute_indexing()
        while True:
            with self._settled:
                pending = self._pending_restart_trigger
                reproject = self._reproject_pending
                if self._shutdown or (pending is None and not reproject):
                    self._pending_restart_trigger = None
                    self._reproject_pending = False
                    self._worker_active = False
                    self._settled.notify_all()
                    return
                self._pending_restart_trigger = None
                self._reproject_pending = False
            if pending is None:
                self._reconcile_native_availability()
                continue
            try:
                begun = self._begin_indexing(trigger=pending, queued=False)
            except ModelIndexingException:
                return
            if not begun:
                return
            self._execute_indexing()

    def _execute_indexing(self) -> None:
        def on_progress(current: int, total: int, message: str) -> None:
            with self._lock:
                if total > 0 and self._state == STATE_SCANNING:
                    self._state = STATE_INDEXING
                if total > 0:
                    self._total = total
                self._processed = current
                listeners = list(self._progress_listeners)
            for listener in listeners:
                try:
                    listener(current, total, message)
                except Exception:
                    logger.debug("Progress listener failed", exc_info=True)

        def is_cancelled() -> bool:
            return self._shutdown

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
        self._last_result = result

        failed = result.get('failed_files') or []
        normalized_failures = [
            f if isinstance(f, dict) else {"path": f, "error": "Failed to hash or index this file"}
            for f in failed
        ]
        skipped_duplicates = result.get('skipped_duplicates') or []
        cancelled = bool(result.get('cancelled')) or self._shutdown

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

    def rescan(self, trigger: str = "location_change") -> Dict[str, Any]:
        with self._lock:
            shutting_down = self._shutdown
            busy = self._is_busy_locked()
            if busy and not shutting_down and not self._queued and self._pending_restart_trigger is None:
                self._pending_restart_trigger = trigger
        if busy or shutting_down:
            return self.status()
        return self.start_indexing(trigger=trigger)

    def on_digests_applied(self, model_ids: Any) -> None:
        conflicts = self.scanner.settle_digested(set(model_ids))
        if conflicts:
            with self._lock:
                merged = {(c['model_id'], c['model_type']): c for c in (*self._type_conflicts, *conflicts)}
                self._type_conflicts = list(merged.values())
        self._pin_digested_downloads(set(model_ids))
        self._reconcile_native_availability()
        self._announce_digests(set(model_ids))

    def _announce_digests(self, model_ids: Set[str]) -> None:
        fire = False
        with self._lock:
            if self._shutdown:
                return
            self._after_digests_ids.update(model_ids)
            if self._after_digests_timer is None:
                waited = None if self._after_digests_at is None else self._clock() - self._after_digests_at
                if waited is None or waited >= AFTER_DIGESTS_MIN_SECONDS:
                    fire = True
                else:
                    self._after_digests_timer = self._start_timer(
                        AFTER_DIGESTS_MIN_SECONDS - waited, self._fire_after_digests
                    )
        if fire:
            self._fire_after_digests()

    def _fire_after_digests(self) -> None:
        with self._lock:
            ids = sorted(self._after_digests_ids)
            self._after_digests_ids = set()
            self._after_digests_timer = None
            self._after_digests_at = self._clock()
            if self._shutdown:
                return
        if ids:
            execute_hook(self.plugins, MODEL_INDEX_HOOKS.after_digests, {"model_ids": ids})

    def _pin_digested_downloads(self, model_ids: Any) -> None:
        with self._lock:
            due = {model_id: self._pins_awaiting_digest.pop(model_id) for model_id in model_ids if model_id in self._pins_awaiting_digest}
        for model_id, loc in due.items():
            model = self.model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
            warning = self._pin_downloaded_type(model, loc) if model is not None else None
            if warning:
                logger.warning(f"Classifying downloaded model '{model.filename}': {warning}")

    def settle_digest_queue(self, reason: str) -> None:
        digests = self.scanner.digests
        if digests is None:
            return
        digests.reconcile()
        if reason == STARTUP_REASON and digests.policy == POLICY_UNSET:
            return
        digests.apply_policy(reason)

    def _on_contested_resolved(self, found: Any, outcome: Optional[Any], error: Optional[str]) -> None:
        with self._lock:
            if outcome is None:
                self._failed_files_total += 1
                if len(self._failed_files) < MAX_REPORTED_FAILED_FILES:
                    self._failed_files.append({"path": found.abs_path, "error": error or "Failed to hash or index this file"})
            elif outcome.model is not None:
                self._indexed += 1
            elif outcome.duplicate_of is not None:
                self._skipped_duplicates_total += 1
                if len(self._skipped_duplicates) < MAX_REPORTED_SKIPPED_DUPLICATES:
                    self._skipped_duplicates.append(self.scanner.skipped_duplicate_entry(found, outcome.duplicate_of))
            if outcome is not None and outcome.type_conflicts:
                merged = {(c['model_id'], c['model_type']): c for c in (*self._type_conflicts, *outcome.type_conflicts)}
                self._type_conflicts = list(merged.values())
            loc = self._pins_awaiting_contest.pop(found.abs_path, None)
        if loc is not None and outcome is not None and outcome.model is not None:
            warning = self._pin_downloaded_type(outcome.model, loc)
            if warning:
                logger.warning(f"Classifying downloaded model '{outcome.model.filename}': {warning}")
        self.reproject(trigger="contested_resolved")

    def _reconcile_native_availability(self) -> None:
        from src.features.recipes.executors._async_bridge import run_sync

        try:
            run_sync(self.native_availability_projector.reconcile(self.backend_registry))
        except Exception as e:
            logger.warning(f"Error reconciling native availability after indexing: {e}")

    def index_path(self, path: str) -> Dict[str, Any]:
        return self._index_one(path, classify=False)

    def index_downloaded(self, path: str) -> Dict[str, Any]:
        return self._index_one(path, classify=self.type_manager is not None)

    def _index_one(self, path: str, classify: bool) -> Dict[str, Any]:
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
        classified = classify and loc.model_type in header_classified_types()
        if classified:
            found = replace(found, scan_headers=True)
        outcome = self.scanner.index_file(found, queue_priority=PRIORITY_CHOSEN)
        model = outcome.model
        warning = outcome.header_error
        if outcome.contested:
            warning = CONTESTED_WARNING
            if classified:
                with self._lock:
                    self._pins_awaiting_contest[found.abs_path] = loc
        if classified and model is not None and warning is None:
            warning = self._pin_downloaded_type(model, loc)
        duplicate_of = outcome.duplicate_of.to_dict() if outcome.duplicate_of is not None else None

        with self._lock:
            self._last_single_index = {
                "path": str(candidate),
                "model_id": model.id if model else None,
                "at": dt_iso(now_utc()),
            }

        if model is not None:
            self._reconcile_native_availability()

        return {
            "model_id": model.id if model else None,
            "indexed": model is not None,
            "duplicate_of": duplicate_of,
            "warning": warning,
        }

    def _pin_downloaded_type(self, model: Any, loc: Any) -> Optional[str]:
        location = next(
            (
                row for row in self.locations_repo.list_for_model(model.id)
                if (row["root_id"], row["model_type"], row["rel_path"]) == (loc.root_id, loc.model_type, loc.rel_path)
            ),
            None,
        )
        if location is None:
            return None
        if not location["sha256"]:
            with self._lock:
                self._pins_awaiting_digest[model.id] = loc
            return None
        if location["status"] == "conflict":
            return (
                f"Downloaded, but another model named '{model.filename}' already exists as a "
                f"{model.model_type.replace('_', ' ')}, so this file was not indexed separately."
            )
        sha256 = location["sha256"]
        self.scanner.ensure_verdict_for_location(location)
        verdict = self.scanner.types.get_verdicts([model.id]).get(model.id)
        if not verdict or verdict["status"] != "decided" or not verdict["model_type"]:
            return None
        outcome = self.type_manager.assert_type(sha256, verdict["model_type"], "download")
        if outcome.conflicts:
            return (
                f"Downloaded, but '{model.filename}' looks like a {verdict['model_type'].replace('_', ' ')} "
                f"and another model already uses that name there, so it stays in its folder's type."
            )
        return None

    def count_unindexed(self) -> Dict[str, Any]:
        """Cheap directory-walk + DB diff: how many files on disk await indexing,
        by type. No hashing, no writes."""
        return self.scanner.count_unindexed()

    def resume_interrupted_indexing(self) -> bool:
        self.settle_digest_queue(STARTUP_REASON)
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
