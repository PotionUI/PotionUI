"""Core's side of a remote native execution.

The wire contracts this feature exchanges with a worker live in
``src.platform.worker_protocol`` - they are shared substrate, spoken by the
worker process and by future provider plugins as well as by core. What lives
here is the part only core has: the durable record of an execution, the state
machine that decides which worker events may move it, and the leasing that
keeps two dispatchers from running one job twice.
"""

from src.features.remote_execution.records import (
    LEGAL_TRANSITIONS,
    EVENT_STATES,
    TERMINAL_STATES,
    IllegalStateTransition,
    RemoteExecution,
    RemoteExecutionState,
    assert_transition,
    is_legal_transition,
    state_for_event,
)
from src.features.remote_execution.repository import RemoteExecutionRepository

__all__ = [
    "LEGAL_TRANSITIONS",
    "EVENT_STATES",
    "IllegalStateTransition",
    "RemoteExecution",
    "RemoteExecutionRepository",
    "RemoteExecutionState",
    "TERMINAL_STATES",
    "assert_transition",
    "is_legal_transition",
    "state_for_event",
]
