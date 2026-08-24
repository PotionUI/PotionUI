"""The worker -> core event stream.

**Cursor semantics.** ``cursor`` is per-execution, starts at 1, increases by
exactly 1 per event, and never repeats. That density is the point: core stores
the highest *contiguous* cursor it has applied, so a gap is proof that events
were lost rather than merely delayed, and a reconnecting core resumes with
"everything after N" instead of replaying a stream it has already applied (which
would duplicate artifacts) or skipping ahead (which would lose them).

A worker must be able to re-send an event with its original cursor after a
reconnect, so events are retained worker-side until core acknowledges them.
Cursor is assigned by the worker, not by arrival order at core - two transports
delivering out of order still reconstruct one sequence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Optional

from pydantic import Field, JsonValue, field_validator, model_validator

from src.platform.worker_protocol.artifact import ArtifactRefV1
from src.platform.worker_protocol.common import (
    Identifier,
    NonEmptyText,
    ProtocolModel,
)
from src.platform.worker_protocol.worker_info import FINGERPRINT_DOMAINS


class JobEventKind(str, Enum):
    """Event names core itself understands.

    ``JobEventV1.kind`` is a free string, not this enum: pipes (including
    plugin-supplied ones) emit progress vocabulary core has no list of, and an
    unknown event must be forwardable rather than fatal. These are the names
    that drive the core-side state machine and must not be reused for anything
    else.
    """

    ACCEPTED = "accepted"
    STAGING = "staging"
    RUNNING = "running"
    PIPE_STARTED = "pipe_started"
    PIPE_PROGRESS = "pipe_progress"
    ARTIFACT = "artifact"
    LOG = "log"
    HEARTBEAT = "heartbeat"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: A worker refused a package outright because a required fingerprint
    #: (see ``FINGERPRINT_DOMAINS``) didn't match. Kept distinct from FAILED
    #: rather than folded into it because a dashboard needs to tell "the job
    #: ran and blew up" from "the job never ran on this worker" - the state
    #: machine still maps it onto FAILED (see EVENT_STATES).
    REJECTED = "rejected"


#: The kinds after which no further event may arrive for an execution.
TERMINAL_EVENT_KINDS = frozenset(
    {
        JobEventKind.SUCCEEDED,
        JobEventKind.FAILED,
        JobEventKind.CANCELLED,
        JobEventKind.REJECTED,
    }
)


class FingerprintMismatchV1(ProtocolModel):
    """What a worker compared when it refused a package on REJECTED."""

    domain: Identifier
    expected: NonEmptyText
    actual: NonEmptyText

    @field_validator("domain")
    @classmethod
    def _known_domain(cls, value: str) -> str:
        if value not in FINGERPRINT_DOMAINS:
            raise ValueError(
                f"unknown fingerprint domain {value!r}; "
                f"expected one of {', '.join(FINGERPRINT_DOMAINS)}"
            )
        return value


class JobErrorV1(ProtocolModel):
    """Why an execution failed, in a form core can act on."""

    code: Identifier
    message: NonEmptyText
    #: Whether core may re-dispatch the same package. Set by the worker because
    #: only it knows whether the failure was environmental (OOM under load,
    #: transient fetch) or intrinsic to the package.
    retryable: bool = False
    detail: Optional[str] = None
    #: Populated when code == "fingerprint_mismatch" - see
    #: JobEventV1's validator, which requires the pairing.
    fingerprint_mismatch: Optional[FingerprintMismatchV1] = None


class JobEventV1(ProtocolModel):
    """One item of the worker -> core stream for a single execution."""


    execution_id: Identifier
    worker_id: Identifier
    cursor: Annotated[int, Field(ge=1)]
    emitted_at: datetime
    #: See JobEventKind - core-known names drive the state machine, unknown
    #: names are forwarded as opaque progress.
    kind: Identifier
    pipe_id: Optional[str] = None
    progress: Optional[Annotated[float, Field(ge=0.0, le=1.0)]] = None
    detail: Optional[str] = None
    artifacts: tuple[ArtifactRefV1, ...] = ()
    error: Optional[JobErrorV1] = None
    payload: dict[str, JsonValue] = {}

    @model_validator(mode="after")
    def _failure_carries_an_error(self) -> "JobEventV1":
        if self.kind == JobEventKind.FAILED.value and self.error is None:
            raise ValueError("a 'failed' event must carry an error")
        if self.kind == JobEventKind.REJECTED.value and self.error is None:
            raise ValueError("a 'rejected' event must carry an error")
        if (
            self.error is not None
            and self.error.code == "fingerprint_mismatch"
            and self.error.fingerprint_mismatch is None
        ):
            raise ValueError(
                "a 'fingerprint_mismatch' error must carry the structured "
                "fingerprint_mismatch field"
            )
        return self

    @property
    def known_kind(self) -> JobEventKind | None:
        """The enum member for ``kind``, or None when the worker sent a name
        core does not model."""
        try:
            return JobEventKind(self.kind)
        except ValueError:
            return None

    @property
    def is_terminal(self) -> bool:
        return self.known_kind in TERMINAL_EVENT_KINDS
