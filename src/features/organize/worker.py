import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from src.platform.plugins.hooks import HookChain, HookContext

logger = logging.getLogger(__name__)

SUBSCRIBER_ID = "organize_engine"
QUEUE_SIZE = 10000
PRUNE_EVERY_S = 6 * 3600

HOOK_EVENTS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "generation.after_complete": ("generation_completed", ("generation_id", "status")),
    "generation.after_update_tags": ("generation_tags_changed", ("generation_id",)),
    "media.after_record": ("upload_created", ("upload_id", "purpose")),
    "model_index.after_index": ("models_indexed", ()),
    "model_index.after_download": ("model_added", ("model_id",)),
    "model_index.after_assign": ("model_added", ("model_id", "user_id")),
    "model_index.after_update_tags": ("model_tags_changed", ("model_id",)),
    "model_index.after_update_metadata": ("model_metadata_changed", ("model_id", "user_id")),
}

_STOP = object()


class OrganizeWorker:

    def __init__(self, handle: Callable[[str, Dict[str, Any]], None], prune: Optional[Callable[[], Any]] = None,
                 maxsize: int = QUEUE_SIZE):
        self._handle = handle
        self._prune = prune
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=maxsize)
        self._thread: Optional[threading.Thread] = None
        self._last_prune = -float(PRUNE_EVERY_S)

    def subscribe(self, hook_chain: HookChain) -> None:
        for hook_name, (kind, keys) in HOOK_EVENTS.items():
            hook_chain.register(hook_name, SUBSCRIBER_ID, self._make_handler(kind, keys))

    def unsubscribe(self, hook_chain: HookChain) -> None:
        for hook_name in HOOK_EVENTS:
            hook_chain.unregister(hook_name, SUBSCRIBER_ID)

    def _make_handler(self, kind: str, keys: Tuple[str, ...]):
        def handler(context: HookContext) -> HookContext:
            data = context.data or {}
            self.enqueue(kind, {key: data.get(key) for key in keys})
            return context

        return handler

    def enqueue(self, kind: str, payload: Dict[str, Any]) -> bool:
        try:
            self._queue.put_nowait((kind, payload))
            return True
        except queue.Full:
            logger.warning("Auto-organize queue is full; dropping a %s event", kind)
            return False

    def pending(self) -> int:
        return self._queue.qsize()

    def drain(self) -> int:
        handled = 0
        while True:
            try:
                entry = self._queue.get_nowait()
            except queue.Empty:
                return handled
            if entry is _STOP:
                continue
            self._dispatch(entry)
            handled += 1

    def _dispatch(self, entry: Tuple[str, Dict[str, Any]]) -> None:
        kind, payload = entry
        try:
            self._handle(kind, payload)
        except Exception:
            logger.exception("Auto-organize failed to handle a %s event", kind)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="organize-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            pass
        thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while True:
            self._maybe_prune()
            try:
                entry = self._queue.get(timeout=60)
            except queue.Empty:
                continue
            if entry is _STOP:
                return
            self._dispatch(entry)

    def _maybe_prune(self) -> None:
        if self._prune is None or time.monotonic() - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = time.monotonic()
        try:
            self._prune()
        except Exception:
            logger.warning("Auto-organize retention pass failed", exc_info=True)
