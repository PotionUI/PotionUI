"""Accumulates the WebSocket messages a generation emits into a durable
"run report" - the same status-history/pipe-timer/artifact/plugin-output data
the live history drawer shows, kept around after the connection that carried
it closes.

Fed from the single funnel every generation output already passes through
regardless of subscriber count - `GenerationController._handle_generation_output`
(`src/features/generation/routes.py`) - so a generation nobody is watching
still gets a report.
"""

import logging
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Set

from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.platform.database.rows import now_iso

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_STATUS_HISTORY_CAP = 500
_ARTIFACTS_CAP = 100

# Message types with a dedicated report section - anything else is treated as
# a plugin/custom output type and captured generically (latest wins).
_STATUS_MESSAGE_TYPE = "generation_status"
_ARTIFACT_MESSAGE_TYPE = "pipe_artifact"
# Carry preview binaries / files already persisted on the Generation row -
# recording them into plugin_outputs would duplicate storage and bloat the
# report for no benefit to the drawer.
_UNTRACKED_MESSAGE_TYPES = frozenset({
    _STATUS_MESSAGE_TYPE, _ARTIFACT_MESSAGE_TYPE, "workbench_update", "gallery_update",
})

# A generation whose terminal output_callback(None) never arrives (process
# restart mid-run, etc.) must not accumulate forever - swept opportunistically
# rather than on a background timer, matching GenerationStatusTracker.prune_finished.
_DEFAULT_SWEEP_INTERVAL_S = 300
_DEFAULT_MAX_AGE_S = 3600


@dataclass
class _Accumulator:
    created_at: float = field(default_factory=time.time)
    status_history: List[Dict[str, Any]] = field(default_factory=list)
    status_history_truncated: bool = False
    pipe_timers: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    artifacts_truncated: bool = False
    plugin_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _last_boundary_key: Optional[tuple] = None


class RunReportRecorder:
    """In-memory per-generation accumulator, flushed once to durable storage."""

    def __init__(self, repository: GenerationRunReportRepository):
        self._repository = repository
        self._lock = RLock()
        self._accumulators: Dict[str, _Accumulator] = {}
        self._last_sweep = time.time()

    def record_output(self, generation_id: str, message: Dict[str, Any]) -> None:
        """Fold one serialized WebSocket message into the generation's report."""
        message_type = message.get("type")
        pipe_id = message.get("pipe_id")
        at = now_iso()

        with self._lock:
            self._maybe_sweep_locked()
            acc = self._accumulators.setdefault(generation_id, _Accumulator())

            if pipe_id is not None:
                self._touch_pipe_timer(acc, pipe_id, at)

            if message_type == _STATUS_MESSAGE_TYPE:
                self._record_status_boundary(acc, message, pipe_id, at)
            elif message_type == _ARTIFACT_MESSAGE_TYPE:
                self._record_artifact(acc, message, pipe_id, at)
            elif message_type not in _UNTRACKED_MESSAGE_TYPES and message_type is not None:
                self._record_plugin_output(acc, message, at)

    @staticmethod
    def _touch_pipe_timer(acc: _Accumulator, pipe_id: Any, at: str) -> None:
        key = str(pipe_id)
        timer = acc.pipe_timers.get(key)
        if timer is None:
            acc.pipe_timers[key] = {"started_at": at, "ended_at": at}
        else:
            timer["ended_at"] = at

    def _record_status_boundary(
        self, acc: _Accumulator, message: Dict[str, Any], pipe_id: Any, at: str
    ) -> None:
        step = message.get("current_step")
        boundary_key = (pipe_id, step)
        if boundary_key == acc._last_boundary_key:
            return
        acc._last_boundary_key = boundary_key
        self._append_capped(
            acc, "status_history", "status_history_truncated", _STATUS_HISTORY_CAP,
            {
                "at": at,
                "pipe_id": pipe_id,
                "step": step,
                "message": message.get("message"),
                "progress": message.get("progress"),
            },
        )

    def _record_artifact(
        self, acc: _Accumulator, message: Dict[str, Any], pipe_id: Any, at: str
    ) -> None:
        self._append_capped(
            acc, "artifacts", "artifacts_truncated", _ARTIFACTS_CAP,
            {
                "at": at,
                "pipe_id": pipe_id,
                "artifact_type": message.get("artifact_type"),
                "artifact_data": message.get("artifact_data"),
            },
        )

    @staticmethod
    def _record_plugin_output(acc: _Accumulator, message: Dict[str, Any], at: str) -> None:
        message_type = message["type"]
        acc.plugin_outputs[message_type] = {
            "plugin_id": message.get("pipe_name") or message.get("output_type"),
            "message": message,
            "at": at,
        }

    @staticmethod
    def _append_capped(
        acc: _Accumulator, list_attr: str, truncated_attr: str, cap: int, entry: Dict[str, Any]
    ) -> None:
        items: List[Dict[str, Any]] = getattr(acc, list_attr)
        items.append(entry)
        if len(items) > cap:
            del items[0]
            setattr(acc, truncated_attr, True)

    def flush(
        self,
        generation_id: str,
        terminal_status: str,
        terminal_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Finalize and persist the report for a terminal generation.

        Always writes a row, even for a generation that produced no
        trackable output before failing (get-or-create), so the admin detail
        endpoint never has to distinguish "no report yet" from "nothing
        happened".
        """
        with self._lock:
            acc = self._accumulators.pop(generation_id, None)
            if acc is None:
                acc = _Accumulator()

            boundary_key = ("__terminal__", terminal_status)
            if boundary_key != acc._last_boundary_key:
                self._append_capped(
                    acc, "status_history", "status_history_truncated", _STATUS_HISTORY_CAP,
                    {
                        "at": now_iso(),
                        "pipe_id": None,
                        "step": terminal_status,
                        "message": terminal_message,
                        "progress": None,
                    },
                )

            report = {
                "schema_version": SCHEMA_VERSION,
                "status_history": acc.status_history,
                "status_history_truncated": acc.status_history_truncated,
                "pipe_timers": acc.pipe_timers,
                "artifacts": acc.artifacts,
                "artifacts_truncated": acc.artifacts_truncated,
                "plugin_outputs": acc.plugin_outputs,
            }

        self._repository.save(generation_id, report)
        return report

    def get_report(self, generation_id: str) -> Optional[Dict[str, Any]]:
        """Read-side passthrough for admin endpoints - the persisted report only."""
        return self._repository.get(generation_id)

    def has_reports(self, generation_ids: Iterable[str]) -> Set[str]:
        """Read-side passthrough for admin endpoints - which ids have a persisted report."""
        return self._repository.exists_bulk(generation_ids)

    def sweep(self, max_age_s: float = _DEFAULT_MAX_AGE_S) -> int:
        """Drop accumulators older than `max_age_s` that were never flushed.

        Never persists anything for a swept generation - a crashed run has
        no terminal status to report, and a partial report would be
        misleading. Returns the number evicted.
        """
        with self._lock:
            return self._sweep_locked(max_age_s)

    def _maybe_sweep_locked(self) -> None:
        """Opportunistic sweep, throttled - called with `_lock` already held."""
        if time.time() - self._last_sweep < _DEFAULT_SWEEP_INTERVAL_S:
            return
        self._sweep_locked(_DEFAULT_MAX_AGE_S)

    def _sweep_locked(self, max_age_s: float) -> int:
        now = time.time()
        stale = [
            gid for gid, acc in self._accumulators.items()
            if (now - acc.created_at) > max_age_s
        ]
        for gid in stale:
            del self._accumulators[gid]
        self._last_sweep = now
        if stale:
            logger.warning(f"[RUN_REPORT] Swept {len(stale)} stale in-memory run report(s)")
        return len(stale)
