"""Accumulates the WebSocket messages a generation emits into a durable
"run report" - the same status-history/pipe-timer/artifact/plugin-output data
the live history drawer shows, kept around after the connection that carried
it closes.

Fed from the single funnel every generation output already passes through
regardless of subscriber count - `GenerationController._handle_generation_output`
(`src/features/generation/routes.py`) - so a generation nobody is watching
still gets a report.

The live message and the recorded entry are two representations of the same
output. The message is never mutated: transport keeps its inline base64,
while the report keeps a reference to the same bytes in managed storage
(`run_report_artifacts`) plus explicit markers wherever something was too
large to keep at all.
"""

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Set

from src.features.generation.run_report_artifacts import (
    MEDIA_REF_MARKER,
    RUN_REPORT_ARTIFACT_REF,
    RunReportArtifactStore,
    find_ref,
    looks_like_stored_reference,
    sniff_image_payload,
)
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.platform.database.rows import now_iso

logger = logging.getLogger(__name__)

# 1: every payload inline, including base64 comparison images.
# 2: binary payloads offloaded to managed storage and referenced; byte
#    budgets and per-item omission markers.
SCHEMA_VERSION = 2

_STATUS_HISTORY_CAP = 500
_ARTIFACTS_CAP = 100

# A count cap alone bounds nothing: 12 comparison artifacts of 400 KB each
# are 5 MB of JSON in one row. Everything below bounds the same data by size.
_OFFLOAD_MIN_BYTES = 4096
_ARTIFACT_ITEM_BYTES_CAP = 64 * 1024
_ARTIFACTS_BYTES_CAP = 512 * 1024
_PLUGIN_OUTPUT_ITEM_BYTES_CAP = 64 * 1024
_PLUGIN_OUTPUTS_BYTES_CAP = 256 * 1024
_ARTIFACT_STORAGE_BYTES_CAP = 32 * 1024 * 1024

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
    artifacts_omitted: int = 0
    artifacts_bytes: int = 0
    stored_bytes: int = 0
    plugin_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plugin_outputs_omitted: int = 0
    plugin_outputs_bytes: int = 0
    _last_boundary_key: Optional[tuple] = None


def normalize_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Give a stored report the current shape without rewriting the row.

    A schema-1 report keeps its inline payloads exactly as written - readers
    accept both - and only gains the counters that did not exist when it was
    persisted, so a caller never has to branch on the version to read them.
    """
    if report is None:
        return None

    normalized = dict(report)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("artifacts_omitted", 0)
    normalized.setdefault("artifacts_bytes", 0)
    normalized.setdefault("stored_bytes", 0)
    normalized.setdefault("plugin_outputs_omitted", 0)
    normalized.setdefault("plugin_outputs_bytes", 0)
    return normalized


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


class RunReportRecorder:
    """In-memory per-generation accumulator, flushed once to durable storage."""

    def __init__(
        self,
        repository: GenerationRunReportRepository,
        file_service=None,
    ):
        self._repository = repository
        self._artifacts = RunReportArtifactStore(file_service) if file_service is not None else None
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
                self._record_artifact(acc, generation_id, message, pipe_id, at)
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
        self,
        acc: _Accumulator,
        generation_id: str,
        message: Dict[str, Any],
        pipe_id: Any,
        at: str,
    ) -> None:
        entry: Dict[str, Any] = {
            "at": at,
            "pipe_id": pipe_id,
            "artifact_type": message.get("artifact_type"),
        }

        data, omission = self._durable_artifact_data(
            acc, generation_id, message.get("artifact_data")
        )
        entry["artifact_data"] = data
        if omission is not None:
            entry["omitted"] = omission
            acc.artifacts_omitted += 1
        else:
            acc.artifacts_bytes += _json_bytes(data)

        evicted = self._append_capped(
            acc, "artifacts", "artifacts_truncated", _ARTIFACTS_CAP, entry
        )
        if evicted is not None:
            self._release_entry(acc, evicted)

    def _durable_artifact_data(
        self, acc: _Accumulator, generation_id: str, artifact_data: Any
    ):
        """The artifact payload as the report should keep it, plus an
        omission marker when it could not be kept in full."""
        if not isinstance(artifact_data, dict):
            size = _json_bytes(artifact_data)
            if size > _ARTIFACT_ITEM_BYTES_CAP:
                return None, {"reason": "item_bytes", "bytes": size}
            if acc.artifacts_bytes + size > _ARTIFACTS_BYTES_CAP:
                return None, {"reason": "report_bytes", "bytes": size}
            return copy.deepcopy(artifact_data), None

        durable: Dict[str, Any] = {}
        for key, value in artifact_data.items():
            if (
                isinstance(value, str)
                and len(value) >= _OFFLOAD_MIN_BYTES
                and not looks_like_stored_reference(value)
                and sniff_image_payload(value) is not None
            ):
                ref = self._offload(acc, generation_id, value)
                if ref is None:
                    self._release_data(acc, durable)
                    return None, {"reason": "unstorable_payload", "bytes": len(value)}
                durable[key] = ref
                continue
            durable[key] = copy.deepcopy(value)

        size = _json_bytes(durable)
        if size > _ARTIFACT_ITEM_BYTES_CAP:
            self._release_data(acc, durable)
            return None, {"reason": "item_bytes", "bytes": size}
        if acc.artifacts_bytes + size > _ARTIFACTS_BYTES_CAP:
            self._release_data(acc, durable)
            return None, {"reason": "report_bytes", "bytes": size}
        return durable, None

    def _offload(self, acc: _Accumulator, generation_id: str, value: str) -> Optional[Dict[str, Any]]:
        if self._artifacts is None:
            return None
        if acc.stored_bytes + len(value) > _ARTIFACT_STORAGE_BYTES_CAP:
            return None
        try:
            ref = self._artifacts.save(generation_id, value)
        except Exception:
            logger.warning(
                "[RUN_REPORT] Failed to offload artifact payload for %s", generation_id,
                exc_info=True,
            )
            return None
        if ref is not None:
            acc.stored_bytes += ref["bytes"]
        return ref

    def _release_data(self, acc: _Accumulator, artifact_data: Any) -> None:
        """Drop storage this report wrote for a payload it then rejected."""
        if self._artifacts is None or not isinstance(artifact_data, dict):
            return
        refs = [
            value for value in artifact_data.values()
            if isinstance(value, dict) and value.get(MEDIA_REF_MARKER) == RUN_REPORT_ARTIFACT_REF
        ]
        if not refs:
            return
        self._artifacts.delete_paths([ref["path"] for ref in refs])
        acc.stored_bytes = max(0, acc.stored_bytes - sum(ref.get("bytes", 0) for ref in refs))

    def _release_entry(self, acc: _Accumulator, entry: Dict[str, Any]) -> None:
        acc.artifacts_bytes = max(0, acc.artifacts_bytes - _json_bytes(entry.get("artifact_data")))
        self._release_data(acc, entry.get("artifact_data"))

    def _record_plugin_output(self, acc: _Accumulator, message: Dict[str, Any], at: str) -> None:
        message_type = message["type"]
        previous = acc.plugin_outputs.get(message_type)
        if previous is not None:
            acc.plugin_outputs_bytes = max(
                0, acc.plugin_outputs_bytes - _json_bytes(previous.get("message"))
            )

        entry: Dict[str, Any] = {
            "plugin_id": message.get("pipe_name") or message.get("output_type"),
            "at": at,
        }

        size = _json_bytes(message)
        if size > _PLUGIN_OUTPUT_ITEM_BYTES_CAP:
            entry["message"] = None
            entry["omitted"] = {"reason": "item_bytes", "bytes": size}
            acc.plugin_outputs_omitted += 1
        elif acc.plugin_outputs_bytes + size > _PLUGIN_OUTPUTS_BYTES_CAP:
            entry["message"] = None
            entry["omitted"] = {"reason": "report_bytes", "bytes": size}
            acc.plugin_outputs_omitted += 1
        else:
            entry["message"] = copy.deepcopy(message)
            acc.plugin_outputs_bytes += size

        acc.plugin_outputs[message_type] = entry

    @staticmethod
    def _append_capped(
        acc: _Accumulator, list_attr: str, truncated_attr: str, cap: int, entry: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Append `entry`, returning whatever the cap evicted."""
        items: List[Dict[str, Any]] = getattr(acc, list_attr)
        items.append(entry)
        if len(items) > cap:
            setattr(acc, truncated_attr, True)
            return items.pop(0)
        return None

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

        Storage this report wrote is owned by the row: a failed write drops
        it again rather than leaving files nothing references, and an
        overwrite drops the superseded report's files.
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
                "artifacts_omitted": acc.artifacts_omitted,
                "artifacts_bytes": acc.artifacts_bytes,
                "stored_bytes": acc.stored_bytes,
                "plugin_outputs": acc.plugin_outputs,
                "plugin_outputs_omitted": acc.plugin_outputs_omitted,
                "plugin_outputs_bytes": acc.plugin_outputs_bytes,
            }

        superseded = self._repository.get(generation_id)
        try:
            self._repository.save(generation_id, report)
        except Exception:
            self._discard_files(report)
            raise

        if superseded is not None:
            self._discard_files(superseded)
        return report

    def get_report(self, generation_id: str) -> Optional[Dict[str, Any]]:
        """Read-side passthrough for admin endpoints - the persisted report only."""
        return normalize_report(self._repository.get(generation_id))

    def delete_report(self, generation_id: str) -> bool:
        """Drop a persisted report and the files it owns."""
        report = self._repository.get(generation_id)
        if report is None:
            return False
        deleted = self._repository.delete(generation_id)
        self._discard_files(report)
        return deleted

    def discard_generation_files(self, generation_id: str) -> int:
        """Remove the storage a generation's report owns, for a caller that
        is about to delete the generation itself (the row goes with it by
        foreign-key cascade, the bytes do not)."""
        return self._discard_files(self._repository.get(generation_id))

    def _discard_files(self, report: Optional[Dict[str, Any]]) -> int:
        if self._artifacts is None:
            return 0
        return self._artifacts.delete_for_report(report)

    def has_reports(self, generation_ids: Iterable[str]) -> Set[str]:
        """Read-side passthrough for admin endpoints - which ids have a persisted report."""
        return self._repository.exists_bulk(generation_ids)

    def artifact_bytes(self, generation_id: str, name: str):
        """`(bytes, mime)` for one reference the persisted report records, or
        `None`. Resolution goes through the report, never through the caller's
        string, so nothing outside the report is reachable."""
        if self._artifacts is None:
            return None
        ref = find_ref(self._repository.get(generation_id), name)
        if ref is None:
            return None
        try:
            return self._artifacts.read(ref["path"]), ref.get("mime", "application/octet-stream")
        except Exception:
            logger.warning(
                "[RUN_REPORT] Missing artifact bytes for %s/%s", generation_id, name, exc_info=True
            )
            return None

    def sweep(self, max_age_s: float = _DEFAULT_MAX_AGE_S) -> int:
        """Drop accumulators older than `max_age_s` that were never flushed.

        Never persists anything for a swept generation - a crashed run has
        no terminal status to report, and a partial report would be
        misleading. The storage those accumulators wrote goes with them.
        Returns the number evicted.
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
            acc = self._accumulators.pop(gid)
            self._discard_files({"artifacts": acc.artifacts})
        self._last_sweep = now
        if stale:
            logger.warning(f"[RUN_REPORT] Swept {len(stale)} stale in-memory run report(s)")
        return len(stale)
