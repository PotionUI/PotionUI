import asyncio
import re
import threading
import weakref
from typing import Any, Dict, Optional

from src.features.generation.failure import scope_generation_state
from src.features.llm.tools.base import ToolContext

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

_MARKER = re.compile(r"<<([A-Za-z]+):([^:>]*)(?::[^>]*)?>>")

_STAGE_LABELS = (
    ("loader", "Loading model"),
    ("generator", "Generating"),
    ("upscal", "Upscaling"),
    ("detail", "Refining"),
    ("refine", "Refining"),
    ("gallery", "Saving"),
)


class RunLookupUnavailable(Exception):
    pass


def _stage_label(pipe: str) -> str:
    family = pipe.lower()
    return next((label for key, label in _STAGE_LABELS if key in family), "Working")


def plain_step(raw: Optional[str], is_admin: bool) -> Optional[str]:
    if not raw:
        return None
    markers = {kind.upper(): value for kind, value in ((m.group(1), m.group(2)) for m in _MARKER.finditer(raw))}
    if is_admin:
        stripped = " ".join(_MARKER.sub(lambda m: m.group(2), raw).split())
        return stripped or None
    label = _stage_label(markers["PIPE"]) if "PIPE" in markers else None
    progress = markers.get("PROGRESS")
    if label and progress:
        return f"{label}, step {progress}"
    return label or (f"Working, step {progress}" if progress else None)


async def find_run(context: ToolContext, generation_id: str) -> Optional[Dict[str, Any]]:
    orchestrator = context.generation_orchestrator
    if orchestrator is None:
        raise RunLookupUnavailable("Generation is not available on this server.")
    live = await orchestrator.get_generation_status(generation_id)
    if live is not None:
        if getattr(live, "user_id", None) != context.user_id:
            return None
        return live.model_dump()
    facade = context.generation_history_facade
    if facade is None:
        return None
    stored = await asyncio.to_thread(facade.generation_repo.get_by_id, generation_id)
    if stored is None or stored.user_id != context.user_id:
        return None
    return stored.to_dict()


def queue_position(context: ToolContext, generation_id: str) -> Dict[str, Any]:
    snapshot = context.generation_orchestrator.get_queue_snapshot(context.user_id)
    pending = snapshot.get("pending") or []
    for entry in pending:
        if entry.get("generation_id") == generation_id:
            return {"queue_position": entry.get("queue_position"), "queued_for_you": len(pending)}
    return {}


def _percent(progress: Any) -> Optional[int]:
    if progress is None:
        return None
    try:
        return max(0, min(100, round(float(progress) * 100)))
    except (TypeError, ValueError):
        return None


def _error_text(scoped: Dict[str, Any], status: str) -> Optional[str]:
    if status != "failed":
        return None
    for key in ("error_user_message", "error_message", "message"):
        if scoped.get(key):
            return str(scoped[key])
    return "The generation failed."


async def status_payload(context: ToolContext, generation_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    status = str(run.get("status") or "pending")
    scoped = scope_generation_state(run, context.is_admin)
    payload: Dict[str, Any] = {
        "generation_id": generation_id,
        "status": status,
        "finished": status in TERMINAL_STATES,
        "progress_percent": 100 if status == "completed" else _percent(run.get("progress")),
        "step": plain_step(run.get("current_step"), context.is_admin),
        "step_number": run.get("current_step_num"),
        "total_steps": run.get("total_steps"),
        "preset_id": run.get("preset_id"),
        "error": _error_text(scoped, status),
        "cancel_notice": run.get("cancel_notice"),
    }
    if status == "pending":
        payload.update(queue_position(context, generation_id))
    return {key: value for key, value in payload.items() if value is not None}


MAX_CONCURRENT_WAITS = 3


class WaitRegistry:
    def __init__(self, tracker: Any):
        self._lock = threading.Lock()
        self._waiting: Dict[str, list] = {}
        self._active: Dict[str, int] = {}
        tracker.add_terminal_listener(self._on_terminal)

    def _on_terminal(self, record: Any) -> None:
        with self._lock:
            waiting = self._waiting.pop(record.id, [])
        for loop, event in waiting:
            loop.call_soon_threadsafe(event.set)

    def enter(self, user_id: str, generation_id: str) -> Optional[asyncio.Event]:
        with self._lock:
            if self._active.get(user_id, 0) >= MAX_CONCURRENT_WAITS:
                return None
            self._active[user_id] = self._active.get(user_id, 0) + 1
            event = asyncio.Event()
            self._waiting.setdefault(generation_id, []).append((asyncio.get_running_loop(), event))
            return event

    def leave(self, user_id: str, generation_id: str, event: asyncio.Event) -> None:
        with self._lock:
            self._active[user_id] = max(0, self._active.get(user_id, 1) - 1)
            entries = [entry for entry in self._waiting.get(generation_id, []) if entry[1] is not event]
            if entries:
                self._waiting[generation_id] = entries
            else:
                self._waiting.pop(generation_id, None)


_REGISTRIES: "weakref.WeakKeyDictionary[Any, WaitRegistry]" = weakref.WeakKeyDictionary()


def waits_for(tracker: Any) -> WaitRegistry:
    registry = _REGISTRIES.get(tracker)
    if registry is None:
        registry = _REGISTRIES[tracker] = WaitRegistry(tracker)
    return registry
