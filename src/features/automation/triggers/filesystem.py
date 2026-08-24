"""
`trigger.filesystem` - fires on filesystem events under a watched directory,
via `watchdog`.

The directory is picked from the app's configured locations (models root and
its subdirectories, storage dir, outputs dir - see `list_app_directories`)
or, when the user picks "Custom path...", an arbitrary absolute path is
allowed (explicit user requirement - there is no allow-list here beyond
"the path must exist and be a directory", checked both at `AutomationManager.
validate_graph` time and defensively again at `start()`, since a directory
can disappear between save and trigger start / server restart).

Watches are ref-counted per resolved absolute directory so N automations
watching the same directory share one `watchdog` `Observer` schedule.
"""

import fnmatch
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.features.automation.triggers.base import TriggerSource

logger = logging.getLogger(__name__)

# Sentinel `directory` value meaning "use `custom_path` instead" - the
# node-type registry/API/frontend all share this exact string.
CUSTOM_PATH_VALUE = "__custom__"

DEFAULT_DEBOUNCE_MS = 2000

_WATCHDOG_EVENT_MAP = {
    "created": "created",
    "modified": "modified",
    "moved": "modified",
    "deleted": "deleted",
}


def _default_settings_manager():
    """Lazily construct a `SettingsManager` when none is injected (mirrors the
    lazy-construction pattern already used by `CivitaiService.fetch_and_store_civitai_info`)."""
    from src.platform.settings.settings import SettingsManager
    from src.platform.settings.repository import SettingRepository
    return SettingsManager(SettingRepository())


def list_app_directories(settings_manager: Optional[Any] = None) -> List[Dict[str, str]]:
    """
    Options for the `trigger.filesystem` "directory" picker: the models root,
    each of its existing subdirectories (checkpoints, loras, vae, ...),
    the storage dir, the outputs dir, and a final "Custom path..." choice.
    """
    settings_manager = settings_manager or _default_settings_manager()
    options: List[Dict[str, str]] = []

    models_dir = settings_manager.get_models_dir()
    options.append({"value": models_dir, "label": "Models", "description": models_dir})

    try:
        for entry in sorted(Path(models_dir).iterdir(), key=lambda p: p.name):
            if entry.is_dir():
                sub_path = f"{models_dir}/{entry.name}"
                options.append(
                    {"value": sub_path, "label": f"Models › {entry.name}", "description": sub_path}
                )
    except OSError:
        pass  # models dir doesn't exist yet (fresh install) - just skip subdirectory enumeration

    storage_dir = settings_manager.get_file_storage_directory()
    options.append({"value": storage_dir, "label": "Storage", "description": storage_dir})

    outputs_dir = settings_manager.get_generations_directory()
    options.append({"value": outputs_dir, "label": "Outputs", "description": outputs_dir})

    options.append({"value": CUSTOM_PATH_VALUE, "label": "Custom path…"})
    return options


def resolve_effective_directory(config: Dict[str, Any]) -> str:
    """Resolves the effective watch directory from a `trigger.filesystem` node's config."""
    directory = config.get("directory", "")
    if directory == CUSTOM_PATH_VALUE:
        return (config.get("custom_path") or "").strip()
    return (directory or "").strip()


class _RefCountedWatch:
    """One watchdog schedule for a directory, shared by every subscriber to it."""

    def __init__(self, directory: str, observer: Observer):
        self.directory = directory
        self.ref_count = 0
        self._observer = observer
        self._handler = _DirEventHandler(directory)
        self._watch = None

    def add_subscriber(self, callback) -> None:
        self._handler.add_callback(callback)
        self.ref_count += 1
        if self._watch is None:
            # Always schedule recursive - watches are ref-counted per directory
            # and shared across every automation watching it, so a per-subscriber
            # recursive/non-recursive split isn't representable as one watchdog
            # schedule. Non-recursive semantics are enforced downstream instead:
            # `FilesystemTrigger._on_event` drops events whose immediate parent
            # isn't the watched directory when that trigger's own `recursive`
            # config is false.
            self._watch = self._observer.schedule(self._handler, self.directory, recursive=True)

    def remove_subscriber(self, callback) -> None:
        self._handler.remove_callback(callback)
        self.ref_count = max(0, self.ref_count - 1)
        if self.ref_count == 0 and self._watch is not None:
            self._observer.unschedule(self._watch)
            self._watch = None


class _DirEventHandler(FileSystemEventHandler):
    """Fans out raw watchdog events to every subscribed callback, debounced per path+event."""

    def __init__(self, directory: str):
        self.directory = directory
        self._callbacks: Set = set()
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def add_callback(self, callback) -> None:
        with self._lock:
            self._callbacks.add(callback)

    def remove_callback(self, callback) -> None:
        with self._lock:
            self._callbacks.discard(callback)

    def _dispatch(self, event_type: str, src_path: str) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(event_type, src_path)
            except Exception:
                logger.error("[FS_TRIGGER] Error in filesystem event callback", exc_info=True)

    def _debounced_dispatch(self, event_type: str, src_path: str, debounce_ms: int) -> None:
        timer_key = f"{event_type}:{src_path}"
        with self._lock:
            existing = self._timers.get(timer_key)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(debounce_ms / 1000.0, self._dispatch, args=(event_type, src_path))
            timer.daemon = True
            self._timers[timer_key] = timer
            timer.start()

    def on_created(self, event):
        if not event.is_directory:
            self._debounced_dispatch("created", event.src_path, DEFAULT_DEBOUNCE_MS)

    def on_modified(self, event):
        if not event.is_directory:
            self._debounced_dispatch("modified", event.src_path, DEFAULT_DEBOUNCE_MS)

    def on_moved(self, event):
        if not event.is_directory:
            self._debounced_dispatch("modified", event.dest_path, DEFAULT_DEBOUNCE_MS)

    def on_deleted(self, event):
        if not event.is_directory:
            self._debounced_dispatch("deleted", event.src_path, DEFAULT_DEBOUNCE_MS)


class FilesystemWatchManager:
    """Owns the single shared `watchdog` `Observer` and ref-counts per-directory watches."""

    def __init__(self):
        self._observer: Optional[Observer] = None
        self._watches: Dict[str, _RefCountedWatch] = {}
        self._lock = threading.Lock()

    def _ensure_observer(self) -> Observer:
        if self._observer is None:
            self._observer = Observer()
            self._observer.start()
        return self._observer

    def watch(self, directory: str, callback) -> None:
        resolved = str(Path(directory).resolve())
        with self._lock:
            observer = self._ensure_observer()
            watch = self._watches.get(resolved)
            if watch is None:
                watch = _RefCountedWatch(resolved, observer)
                self._watches[resolved] = watch
            watch.add_subscriber(callback)

    def unwatch(self, directory: str, callback) -> None:
        resolved = str(Path(directory).resolve())
        with self._lock:
            watch = self._watches.get(resolved)
            if watch is None:
                return
            watch.remove_subscriber(callback)
            if watch.ref_count == 0:
                del self._watches[resolved]

    def stop(self) -> None:
        with self._lock:
            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=5)
                self._observer = None
            self._watches.clear()


def build_event_payload(directory: str, src_path: str, event_type: str) -> Dict[str, Any]:
    """
    `path` is the absolute filesystem path. `parts`/`rel_parts` (identical -
    `rel_parts` is the unambiguous name, `parts` kept for back-compat) are the
    path components relative to the WATCHED directory, e.g. watching
    "models/loras" and a file landing at "models/loras/krea2/x.safetensors"
    gives `["krea2", "x.safetensors"]` - `rel_parts.0` is exactly what a
    `condition.switch` on the immediate subdirectory should key off (see the
    "Auto-index new loras" example automation). `rel_path` is those parts
    joined back into a single relative path string.
    """
    path = Path(src_path)
    try:
        rel_parts = list(path.relative_to(Path(directory).resolve()).parts)
    except ValueError:
        rel_parts = list(path.parts)
    size = None
    try:
        size = os.path.getsize(src_path) if event_type != "deleted" else None
    except OSError:
        size = None
    return {
        "path": str(path),
        "event": event_type,
        "dir": directory,
        "parts": rel_parts,
        "rel_parts": rel_parts,
        "rel_path": "/".join(rel_parts),
        "ext": path.suffix,
        "size": size,
    }


class FilesystemTrigger(TriggerSource):
    """`trigger.filesystem` node."""

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any], enqueue,
                 watch_manager: FilesystemWatchManager, notification_manager: Optional[Any] = None):
        super().__init__(automation_id, node_id, config, enqueue)
        self._watch_manager = watch_manager
        self._notification_manager = notification_manager
        self._watch_dir = resolve_effective_directory(config)
        self._event_filter = config.get("event", "any")
        self._pattern = config.get("pattern", "*")
        # Defaults true: files landing in subdirectories of the watched dir
        # (e.g. "models/loras/krea2/x.safetensors" while watching "models/loras")
        # fire too. See `_RefCountedWatch.add_subscriber` for why this is
        # enforced here rather than as a per-watch `recursive=` flag.
        self._recursive = bool(config.get("recursive", True))

    def _on_event(self, event_type: str, src_path: str) -> None:
        if self._event_filter != "any" and event_type != self._event_filter:
            return
        if self._pattern and not fnmatch.fnmatch(os.path.basename(src_path), self._pattern):
            return
        if not self._recursive and Path(src_path).resolve().parent != Path(self._watch_dir).resolve():
            return
        self.fire(build_event_payload(self._watch_dir, src_path, event_type))

    async def start(self) -> None:
        if not self._watch_dir:
            self._fail_to_start("no watch directory configured")
            return
        # Defensive re-check: validate_graph already required this directory
        # to exist at save time, but it can disappear before the trigger
        # actually starts (server restart, external deletion, ...).
        if not os.path.isdir(self._watch_dir):
            self._fail_to_start(f"directory '{self._watch_dir}' does not exist or is not a directory")
            return
        self._watch_manager.watch(self._watch_dir, self._on_event)

    def _fail_to_start(self, reason: str) -> None:
        logger.error(f"[FS_TRIGGER] Cannot start node {self.node_id} (automation {self.automation_id}): {reason}")
        if self._notification_manager is not None:
            try:
                self._notification_manager.notify(
                    level="error", category="automation", source="automation_engine",
                    title="Automation file watcher could not start",
                    message=f"Node {self.node_id}: {reason}",
                )
            except Exception:
                logger.error("[FS_TRIGGER] Failed to send start-failure notification", exc_info=True)

    async def stop(self) -> None:
        if self._watch_dir:
            self._watch_manager.unwatch(self._watch_dir, self._on_event)
