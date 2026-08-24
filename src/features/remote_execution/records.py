"""The core-side record of one off-box execution, and its state machine.

This is core state, not wire format: a worker never sees a
``RemoteExecutionState``. It reports events (see
``src.platform.worker_protocol.job_event``) and core decides what those mean for
the row. Keeping the mapping one-directional means a buggy or hostile worker can
drive the row only through transitions core considers legal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from src.platform.database.rows import dt_column
from src.platform.worker_protocol.job_event import JobEventKind


class RemoteExecutionState(str, Enum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    STAGING = "staging"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATES = frozenset(
    {
        RemoteExecutionState.SUCCEEDED,
        RemoteExecutionState.FAILED,
        RemoteExecutionState.CANCELLED,
        RemoteExecutionState.EXPIRED,
    }
)

#: Legal state transitions. A terminal state maps to the empty set, so any
#: move out of succeeded/failed/cancelled/expired is rejected. Identity is not
#: a move: at-least-once event delivery does repeat, but a redundant "still
#: running" is a no-op handled by the repository, not an edge in this table.
LEGAL_TRANSITIONS: Dict[RemoteExecutionState, frozenset] = {
    RemoteExecutionState.PENDING: frozenset(
        {
            RemoteExecutionState.DISPATCHING,
            RemoteExecutionState.CANCELLED,
            RemoteExecutionState.EXPIRED,
            RemoteExecutionState.FAILED,
        }
    ),
    # DISPATCHING -> PENDING is the lease-expiry path: a dispatcher died mid
    # flight, its lease lapsed, and the row goes back on the queue. Without it
    # a crashed dispatcher would strand the execution forever.
    RemoteExecutionState.DISPATCHING: frozenset(
        {
            RemoteExecutionState.PENDING,
            RemoteExecutionState.STAGING,
            RemoteExecutionState.RUNNING,
            RemoteExecutionState.CANCELLING,
            RemoteExecutionState.CANCELLED,
            RemoteExecutionState.FAILED,
            RemoteExecutionState.EXPIRED,
        }
    ),
    # STAGING -> PENDING and RUNNING -> PENDING exist only for the
    # retryable-failure path: a worker reported a JobErrorV1 with
    # retryable=True and the dispatcher chose to requeue it
    # (repository.requeue_for_retry / policy.should_requeue), rather than for
    # any lease- or event-delivery mechanism.
    RemoteExecutionState.STAGING: frozenset(
        {
            RemoteExecutionState.PENDING,
            RemoteExecutionState.RUNNING,
            RemoteExecutionState.CANCELLING,
            RemoteExecutionState.FAILED,
            RemoteExecutionState.EXPIRED,
        }
    ),
    RemoteExecutionState.RUNNING: frozenset(
        {
            RemoteExecutionState.PENDING,
            RemoteExecutionState.CANCELLING,
            RemoteExecutionState.SUCCEEDED,
            RemoteExecutionState.FAILED,
            RemoteExecutionState.EXPIRED,
        }
    ),
    # SUCCEEDED is reachable from CANCELLING: the worker can finish between
    # core deciding to cancel and the cancel arriving. Treating that as illegal
    # would reject a result that genuinely exists.
    RemoteExecutionState.CANCELLING: frozenset(
        {
            RemoteExecutionState.CANCELLED,
            RemoteExecutionState.SUCCEEDED,
            RemoteExecutionState.FAILED,
        }
    ),
    RemoteExecutionState.SUCCEEDED: frozenset(),
    RemoteExecutionState.FAILED: frozenset(),
    RemoteExecutionState.CANCELLED: frozenset(),
    RemoteExecutionState.EXPIRED: frozenset(),
}

#: Which worker events move the row, and where to. Events absent from this map
#: (progress, logs, artifacts, heartbeats, and anything a plugin pipe invents)
#: are recorded without touching the state column.
EVENT_STATES: Dict[str, RemoteExecutionState] = {
    JobEventKind.STAGING.value: RemoteExecutionState.STAGING,
    JobEventKind.RUNNING.value: RemoteExecutionState.RUNNING,
    JobEventKind.SUCCEEDED.value: RemoteExecutionState.SUCCEEDED,
    JobEventKind.FAILED.value: RemoteExecutionState.FAILED,
    JobEventKind.CANCELLED.value: RemoteExecutionState.CANCELLED,
    #: A rejection is a distinct wire kind (see JobEventKind.REJECTED) but the
    #: same terminal outcome as any other failure to this state machine.
    JobEventKind.REJECTED.value: RemoteExecutionState.FAILED,
}


class IllegalStateTransition(Exception):
    """A transition the state machine does not permit."""

    def __init__(self, current: RemoteExecutionState, target: RemoteExecutionState):
        self.current = current
        self.target = target
        allowed = ", ".join(sorted(s.value for s in LEGAL_TRANSITIONS[current]))
        super().__init__(
            f"cannot move a remote execution from {current.value} to "
            f"{target.value}; allowed: {allowed or 'none (terminal)'}"
        )


def is_legal_transition(
    src: RemoteExecutionState, dst: RemoteExecutionState
) -> bool:
    """Whether an execution may move from ``src`` to ``dst`` (identity is not a move)."""
    return dst in LEGAL_TRANSITIONS.get(src, frozenset())


def assert_transition(
    current: RemoteExecutionState, target: RemoteExecutionState
) -> None:
    if not is_legal_transition(current, target):
        raise IllegalStateTransition(current, target)


def state_for_event(kind: str) -> Optional[RemoteExecutionState]:
    """The state a worker event implies, or None when it implies no change."""
    return EVENT_STATES.get(kind)


@dataclass
class RemoteExecution:
    """One execution dispatched to a remote worker."""

    id: str
    provider: str
    state: RemoteExecutionState
    idempotency_key: str
    request_digest: str
    protocol_version: int = 1
    generation_id: Optional[str] = None
    backend_id: Optional[str] = None
    provider_job_id: Optional[str] = None
    worker_id: Optional[str] = None
    #: Highest *contiguous* worker cursor applied. 0 means nothing applied yet,
    #: and a resume asks the worker for everything after this number.
    event_cursor: int = 0
    #: Identity of the dispatcher currently holding the row, with the epoch-
    #: millisecond instant its claim lapses. Epoch-millis rather than a
    #: TIMESTAMP because a lease is compared, and mixing SQLite's
    #: CURRENT_TIMESTAMP format with an ISO string silently compares wrong.
    lease_owner: Optional[str] = None
    lease_expires_at_ms: Optional[int] = None
    #: Fencing token, incremented on every successful claim. A dispatcher that
    #: was paused past its lease and then resumed still holds the old epoch, so
    #: its writes are rejected instead of overwriting the new holder's.
    lease_epoch: int = 0
    #: How many times a lease on this row lapsed and was reclaimed by
    #: requeue_expired_leases - distinct from `attempt`, which counts actual
    #: dispatch attempts (see migration 118).
    lease_lapses: int = 0
    attempt: int = 0
    #: After this instant the row is eligible for expire_overdue() to move it
    #: to EXPIRED, mirroring ExecutionPackageV1.expires_at (epoch
    #: milliseconds - compared, not displayed, same reasoning as
    #: lease_expires_at_ms).
    expires_at_ms: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def next_expected_cursor(self) -> int:
        return self.event_cursor + 1

    @classmethod
    def from_row(cls, row) -> "RemoteExecution":
        return cls(
            id=row["id"],
            provider=row["provider"],
            state=RemoteExecutionState(row["state"]),
            idempotency_key=row["idempotency_key"],
            request_digest=row["request_digest"],
            protocol_version=row["protocol_version"],
            generation_id=row["generation_id"],
            backend_id=row["backend_id"],
            provider_job_id=row["provider_job_id"],
            worker_id=row["worker_id"],
            event_cursor=row["event_cursor"],
            lease_owner=row["lease_owner"],
            lease_expires_at_ms=row["lease_expires_at_ms"],
            lease_epoch=row["lease_epoch"],
            lease_lapses=row["lease_lapses"],
            attempt=row["attempt"],
            expires_at_ms=row["expires_at_ms"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
            dispatched_at=dt_column(row["dispatched_at"]),
            started_at=dt_column(row["started_at"]),
            completed_at=dt_column(row["completed_at"]),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "state": self.state.value,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
            "protocol_version": self.protocol_version,
            "generation_id": self.generation_id,
            "backend_id": self.backend_id,
            "provider_job_id": self.provider_job_id,
            "worker_id": self.worker_id,
            "event_cursor": self.event_cursor,
            "lease_owner": self.lease_owner,
            "lease_expires_at_ms": self.lease_expires_at_ms,
            "lease_epoch": self.lease_epoch,
            "lease_lapses": self.lease_lapses,
            "attempt": self.attempt,
            "expires_at_ms": self.expires_at_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "dispatched_at": (
                self.dispatched_at.isoformat() if self.dispatched_at else None
            ),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }
