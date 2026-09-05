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
import hashlib
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

# The ceiling on the serialized report, not on the payloads inside it: an
# entry's own keys, timestamps, identifiers and omission markers are what a
# report full of *rejected* input is made of, so they are what has to be
# counted. Every admission below is checked against this.
_REPORT_BYTES_CAP = 1024 * 1024
# Reserved for the envelope's own scaffold (section keys and the counters),
# which is written at flush and never passes through an admission check.
_ENVELOPE_OVERHEAD_BYTES = 2048

# Identifiers arrive from plugin-defined message types and pipe names, so
# their length is attacker-controlled; text fields carry rendered step
# templates. Both are bounded before they are ever accounted or stored.
_IDENTIFIER_CHARS = 120
_TEXT_CHARS = 2048
# A plain prefix cut aliases two identifiers that share it into one dictionary
# key, which reads as an ordinary latest-wins replacement and loses an entry
# with nothing to show for it. An over-long identifier keeps a prefix plus a
# digest of the whole string instead, so distinct inputs stay distinct.
_IDENTIFIER_DIGEST_CHARS = 12

# Distinct plugin message types keep a dict entry each (latest wins), and
# distinct pipe ids keep a timer each; both dimensions need a count bound of
# their own, since a byte bound alone still admits an unbounded number of
# ever-smaller keys.
_PLUGIN_OUTPUT_TYPES_CAP = 50
_PIPE_TIMERS_CAP = 200

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
    artifacts_dropped: int = 0
    artifacts_bytes: int = 0
    stored_bytes: int = 0
    plugin_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plugin_outputs_omitted: int = 0
    plugin_output_types_dropped: int = 0
    plugin_outputs_bytes: int = 0
    status_history_dropped: int = 0
    pipe_timers_dropped: int = 0
    envelope_bytes: int = _ENVELOPE_OVERHEAD_BYTES
    _last_boundary_key: Optional[tuple] = None

    def admit(self, size: int) -> bool:
        if self.envelope_bytes + size > _REPORT_BYTES_CAP:
            return False
        self.envelope_bytes += size
        return True

    def release(self, size: int) -> None:
        self.envelope_bytes = max(_ENVELOPE_OVERHEAD_BYTES, self.envelope_bytes - size)


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
    normalized.setdefault("artifacts_dropped", 0)
    normalized.setdefault("plugin_output_types_dropped", 0)
    normalized.setdefault("status_history_dropped", 0)
    normalized.setdefault("pipe_timers_dropped", 0)
    return normalized


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _bounded_key(value: str) -> str:
    """A dictionary key of bounded length that two distinct identifiers can
    never share."""
    if len(value) <= _IDENTIFIER_CHARS:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_IDENTIFIER_DIGEST_CHARS]
    return f"{value[:_IDENTIFIER_CHARS - _IDENTIFIER_DIGEST_CHARS - 1]}~{digest}"


def _bounded_identity(value: Any) -> Any:
    return _bounded_key(value) if isinstance(value, str) else value


def _bounded_text(value: Any):
    """`(value, truncated)` - a cut is reported, never left to look like the
    whole string."""
    if isinstance(value, str) and len(value) > _TEXT_CHARS:
        return value[:_TEXT_CHARS], True
    return value, False


def _oversized_identifier(value: Any) -> bool:
    return isinstance(value, str) and len(value) > _IDENTIFIER_CHARS


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
        key = _bounded_key(str(pipe_id))
        timer = acc.pipe_timers.get(key)
        if timer is not None:
            timer["ended_at"] = at
            return
        if len(acc.pipe_timers) >= _PIPE_TIMERS_CAP:
            acc.pipe_timers_dropped += 1
            return
        timer = {"started_at": at, "ended_at": at}
        if not acc.admit(_json_bytes({key: timer})):
            acc.pipe_timers_dropped += 1
            return
        acc.pipe_timers[key] = timer

    def _record_status_boundary(
        self, acc: _Accumulator, message: Dict[str, Any], pipe_id: Any, at: str
    ) -> None:
        step, step_cut = _bounded_text(message.get("current_step"))
        boundary_key = (pipe_id, step)
        if boundary_key == acc._last_boundary_key:
            return
        acc._last_boundary_key = boundary_key
        text, text_cut = _bounded_text(message.get("message"))
        entry = {
            "at": at,
            "pipe_id": _bounded_identity(pipe_id),
            "step": step,
            "message": text,
            "progress": message.get("progress"),
        }
        truncated = [name for name, cut in (("step", step_cut), ("message", text_cut)) if cut]
        if truncated:
            entry["truncated"] = truncated
        self._append_status(acc, entry)

    @staticmethod
    def _append_status(acc: _Accumulator, entry: Dict[str, Any]) -> None:
        if not acc.admit(_json_bytes(entry)):
            acc.status_history_dropped += 1
            return
        acc.status_history.append(entry)
        if len(acc.status_history) > _STATUS_HISTORY_CAP:
            acc.status_history_truncated = True
            acc.release(_json_bytes(acc.status_history.pop(0)))

    def _record_artifact(
        self,
        acc: _Accumulator,
        generation_id: str,
        message: Dict[str, Any],
        pipe_id: Any,
        at: str,
    ) -> None:
        artifact_type = message.get("artifact_type")
        if _oversized_identifier(artifact_type):
            acc.artifacts_dropped += 1
            return

        entry: Dict[str, Any] = {
            "at": at,
            "pipe_id": _bounded_identity(pipe_id),
            "artifact_type": artifact_type,
        }

        data, omission = self._durable_artifact_data(
            acc, generation_id, message.get("artifact_data")
        )
        entry["artifact_data"] = data
        if omission is not None:
            entry["omitted"] = omission

        size = _json_bytes(entry)
        if not acc.admit(size):
            self._release_data(acc, data)
            acc.artifacts_dropped += 1
            return

        acc.artifacts.append(entry)
        acc.artifacts_bytes += size
        if omission is not None:
            acc.artifacts_omitted += 1

        if len(acc.artifacts) > _ARTIFACTS_CAP:
            acc.artifacts_truncated = True
            self._release_entry(acc, acc.artifacts.pop(0))

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
        """Give back exactly what admitting `entry` took - the whole entry,
        so an omitted one (which contributed only its marker) never has a
        payload's worth of bytes subtracted on its behalf."""
        size = _json_bytes(entry)
        acc.release(size)
        acc.artifacts_bytes = max(0, acc.artifacts_bytes - size)
        if entry.get("omitted") is not None:
            acc.artifacts_omitted = max(0, acc.artifacts_omitted - 1)
        self._release_data(acc, entry.get("artifact_data"))

    def _record_plugin_output(self, acc: _Accumulator, message: Dict[str, Any], at: str) -> None:
        key = _bounded_key(message["type"])
        previous = acc.plugin_outputs.pop(key, None)
        if previous is not None:
            released = _json_bytes({key: previous})
            acc.release(released)
            acc.plugin_outputs_bytes = max(0, acc.plugin_outputs_bytes - released)
            if previous.get("omitted") is not None:
                acc.plugin_outputs_omitted = max(0, acc.plugin_outputs_omitted - 1)
        elif len(acc.plugin_outputs) >= _PLUGIN_OUTPUT_TYPES_CAP:
            acc.plugin_output_types_dropped += 1
            return

        entry: Dict[str, Any] = {
            "plugin_id": _bounded_identity(message.get("pipe_name") or message.get("output_type")),
            "at": at,
        }

        payload_bytes = _json_bytes(message)
        if payload_bytes > _PLUGIN_OUTPUT_ITEM_BYTES_CAP:
            entry["message"] = None
            entry["omitted"] = {"reason": "item_bytes", "bytes": payload_bytes}
        elif acc.plugin_outputs_bytes + payload_bytes > _PLUGIN_OUTPUTS_BYTES_CAP:
            entry["message"] = None
            entry["omitted"] = {"reason": "report_bytes", "bytes": payload_bytes}
        else:
            entry["message"] = copy.deepcopy(message)

        size = _json_bytes({key: entry})
        if not acc.admit(size):
            acc.plugin_output_types_dropped += 1
            return

        acc.plugin_outputs[key] = entry
        acc.plugin_outputs_bytes += size
        if entry.get("omitted") is not None:
            acc.plugin_outputs_omitted += 1

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
                self._append_status(acc, {
                    "at": now_iso(),
                    "pipe_id": None,
                    "step": _bounded_text(terminal_status)[0],
                    "message": _bounded_text(terminal_message)[0],
                    "progress": None,
                })

            report = {
                "schema_version": SCHEMA_VERSION,
                "status_history": acc.status_history,
                "status_history_truncated": acc.status_history_truncated,
                "pipe_timers": acc.pipe_timers,
                "artifacts": acc.artifacts,
                "artifacts_truncated": acc.artifacts_truncated,
                "artifacts_omitted": acc.artifacts_omitted,
                "artifacts_dropped": acc.artifacts_dropped,
                "artifacts_bytes": acc.artifacts_bytes,
                "stored_bytes": acc.stored_bytes,
                "plugin_outputs": acc.plugin_outputs,
                "plugin_outputs_omitted": acc.plugin_outputs_omitted,
                "plugin_output_types_dropped": acc.plugin_output_types_dropped,
                "plugin_outputs_bytes": acc.plugin_outputs_bytes,
                "status_history_dropped": acc.status_history_dropped,
                "pipe_timers_dropped": acc.pipe_timers_dropped,
            }

        try:
            superseded = self._repository.get(generation_id)
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
