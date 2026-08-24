"""Core -> worker: "send me everything after this cursor."

**Retention contract.** A worker must retain an emitted `JobEventV1` until
core has acknowledged it - either by issuing an `EventResumeRequestV1` whose
`after_cursor` has passed that event's cursor, or by the execution reaching a
terminal state core has recorded. Delivery is at-least-once and pull-based:
core may reconnect after any outage and ask for everything past the highest
contiguous cursor it applied (`RemoteExecution.next_expected_cursor`), and a
worker that has already discarded an unacknowledged event has silently lost
it - there is no second source for that event once it's gone.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from src.platform.worker_protocol.common import Identifier, ProtocolModel


class EventResumeRequestV1(ProtocolModel):
    """Core -> worker: replay every event for ``execution_id`` after ``after_cursor``."""

    execution_id: Identifier
    #: 0 means "from the beginning" - a worker has never sent cursor 0
    #: (JobEventV1.cursor starts at 1), so it is a legal, unambiguous floor.
    after_cursor: Annotated[int, Field(ge=0)]
