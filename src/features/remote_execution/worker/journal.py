"""Per-execution journal: one append-only JSONL file per execution under
``POTIONUI_WORKER_DIR/executions``.

Line 0 is a header (``{"request_digest": "..."}``); every following line is a
``JobEventV1``. Append-only and read-on-demand rather than kept only in memory
so a resubmit after a process restart can still answer idempotently, and so a
reconnecting core can resume a cursor-ordered replay without the worker having
kept the stream anywhere else.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.platform.worker_protocol import JobEventV1
from src.platform.worker_protocol.job_event import TERMINAL_EVENT_KINDS, JobEventKind


@dataclass
class ExecutionRecord:
    execution_id: str
    request_digest: str
    events: List[JobEventV1] = field(default_factory=list)

    @property
    def next_cursor(self) -> int:
        return (self.events[-1].cursor if self.events else 0) + 1

    @property
    def is_terminal(self) -> bool:
        if not self.events:
            return False
        try:
            return JobEventKind(self.events[-1].kind) in TERMINAL_EVENT_KINDS
        except ValueError:
            return False

    @property
    def latest_kind(self) -> Optional[str]:
        return self.events[-1].kind if self.events else None


class WorkerJournal:
    def __init__(self, work_dir: Path):
        self._dir = Path(work_dir) / "executions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: Dict[str, ExecutionRecord] = {}

    def _path(self, execution_id: str) -> Path:
        return self._dir / f"{execution_id}.jsonl"

    def get(self, execution_id: str) -> Optional[ExecutionRecord]:
        with self._lock:
            return self._get_locked(execution_id)

    def _get_locked(self, execution_id: str) -> Optional[ExecutionRecord]:
        cached = self._cache.get(execution_id)
        if cached is not None:
            return cached
        record = self._load(execution_id)
        if record is not None:
            self._cache[execution_id] = record
        return record

    def _load(self, execution_id: str) -> Optional[ExecutionRecord]:
        path = self._path(execution_id)
        if not path.exists():
            return None

        request_digest: Optional[str] = None
        events: List[JobEventV1] = []
        with path.open("r") as fh:
            for line_number, line in enumerate(fh):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    doc = json.loads(stripped)
                except ValueError:
                    break  # a torn last write from a crash; stop, don't lose earlier lines
                if line_number == 0:
                    request_digest = doc.get("request_digest")
                    continue
                events.append(JobEventV1.model_validate(doc))

        if request_digest is None:
            return None
        return ExecutionRecord(execution_id, request_digest, events)

    def start(self, execution_id: str, request_digest: str) -> ExecutionRecord:
        """Create the journal for a new execution, or return the existing one
        untouched - callers use this to learn "was this id already started?"
        in the same call that starts it."""
        with self._lock:
            existing = self._get_locked(execution_id)
            if existing is not None:
                return existing

            path = self._path(execution_id)
            with path.open("w") as fh:
                fh.write(json.dumps({"request_digest": request_digest}) + "\n")

            record = ExecutionRecord(execution_id, request_digest, [])
            self._cache[execution_id] = record
            return record

    def append(self, execution_id: str, event: JobEventV1) -> None:
        with self._lock:
            path = self._path(execution_id)
            with path.open("a") as fh:
                fh.write(json.dumps(event.model_dump(mode="json")) + "\n")

            record = self._get_locked(execution_id)
            if record is not None:
                record.events.append(event)

    def events_after(self, execution_id: str, after_cursor: int) -> List[JobEventV1]:
        record = self.get(execution_id)
        if record is None:
            return []
        return [event for event in record.events if event.cursor > after_cursor]
