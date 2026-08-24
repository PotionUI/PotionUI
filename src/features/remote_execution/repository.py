"""Persistence for remote executions.

The methods that matter here are the ones that must be correct under two
dispatchers running at once: :meth:`RemoteExecutionRepository.claim_for_dispatch`,
:meth:`renew_lease`, :meth:`release_lease` and :meth:`advance_event_cursor` are
each a single conditional UPDATE whose WHERE clause carries the precondition.
Read-then-write would leave a window in which both dispatchers believe they own
the row. :meth:`record_event` extends the same shape across two statements on
one connection - the cursor UPDATE and the event INSERT commit or roll back
together.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from src.platform.database import db
from src.platform.util.ids import generate_ulid
from src.platform.worker_protocol import JobEventV1

from src.features.remote_execution.records import (
    TERMINAL_STATES,
    IllegalStateTransition,
    RemoteExecution,
    RemoteExecutionState,
    assert_transition,
    state_for_event,
)


def now_ms() -> int:
    return int(time.time() * 1000)


class RemoteExecutionRepository:
    def create(self, execution: RemoteExecution) -> RemoteExecution:
        """Insert a new execution, or return the one this key already made.

        The idempotency key is the deduplication point: a resubmitted request
        gets the existing row back rather than a second job.
        """
        existing = self.get_by_idempotency_key(execution.idempotency_key)
        if existing is not None:
            return existing

        if not execution.id:
            execution.id = generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remote_executions (
                    id, generation_id, provider, backend_id, state,
                    protocol_version, idempotency_key, request_digest,
                    provider_job_id, worker_id, event_cursor,
                    lease_owner, lease_expires_at_ms, lease_epoch, lease_lapses,
                    attempt, expires_at_ms, error_code, error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.id,
                    execution.generation_id,
                    execution.provider,
                    execution.backend_id,
                    execution.state.value,
                    execution.protocol_version,
                    execution.idempotency_key,
                    execution.request_digest,
                    execution.provider_job_id,
                    execution.worker_id,
                    execution.event_cursor,
                    execution.lease_owner,
                    execution.lease_expires_at_ms,
                    execution.lease_epoch,
                    execution.lease_lapses,
                    execution.attempt,
                    execution.expires_at_ms,
                    execution.error_code,
                    execution.error_message,
                    json.dumps(execution.metadata or {}),
                ),
            )

        return self.get_by_id(execution.id)

    def get_by_id(self, execution_id: str) -> Optional[RemoteExecution]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM remote_executions WHERE id = ?", (execution_id,)
            )
            row = cursor.fetchone()
            return RemoteExecution.from_row(row) if row else None

    def get_by_idempotency_key(self, key: str) -> Optional[RemoteExecution]:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM remote_executions WHERE idempotency_key = ?", (key,)
            )
            row = cursor.fetchone()
            return RemoteExecution.from_row(row) if row else None

    def get_by_provider_job(
        self, provider: str, provider_job_id: str
    ) -> Optional[RemoteExecution]:
        """Route an inbound provider callback back to its row."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM remote_executions
                WHERE provider = ? AND provider_job_id = ?
                """,
                (provider, provider_job_id),
            )
            row = cursor.fetchone()
            return RemoteExecution.from_row(row) if row else None

    def list_by_state(
        self, state: RemoteExecutionState, limit: int = 100
    ) -> List[RemoteExecution]:
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM remote_executions
                WHERE state = ? ORDER BY created_at, id LIMIT ?
                """,
                (state.value, limit),
            )
            return [RemoteExecution.from_row(row) for row in cursor.fetchall()]

    def claim_for_dispatch(
        self,
        owner: str,
        lease_seconds: int,
        now: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> Optional[RemoteExecution]:
        """Take the oldest unleased pending execution, or return None.

        Moves it to DISPATCHING and bumps the fencing epoch in the same
        statement that tests the lease, so two dispatchers calling this
        concurrently cannot both come away with the same row.

        A row already at ``max_attempts`` is skipped rather than claimed - it
        is the dispatcher's job to call :meth:`fail_exhausted` for those, not
        this method's, so a row stuck at the cap doesn't block every row
        behind it in the oldest-first queue.
        """
        current = now_ms() if now is None else now
        deadline = current + lease_seconds * 1000
        attempt_filter = "AND attempt < ?" if max_attempts is not None else ""
        params = [
            RemoteExecutionState.DISPATCHING.value,
            owner,
            deadline,
            RemoteExecutionState.PENDING.value,
            current,
        ]
        if max_attempts is not None:
            params.append(max_attempts)

        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE remote_executions
                SET state = ?,
                    lease_owner = ?,
                    lease_expires_at_ms = ?,
                    lease_epoch = lease_epoch + 1,
                    attempt = attempt + 1,
                    dispatched_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM remote_executions
                    WHERE state = ?
                      AND (lease_expires_at_ms IS NULL OR lease_expires_at_ms <= ?)
                      {attempt_filter}
                    ORDER BY created_at, id
                    LIMIT 1
                )
                RETURNING id
                """,
                tuple(params),
            )
            row = cursor.fetchone()

        return self.get_by_id(row["id"]) if row else None

    def claim_specific(
        self, execution_id: str, owner: str, lease_seconds: int, now: Optional[int] = None,
    ) -> Optional[RemoteExecution]:
        """Lease a KNOWN PENDING row directly, bypassing :meth:`claim_for_dispatch`'s
        oldest-first pool selection.

        The single-slot dispatch path (the PotionUI generation queue already
        serializes per backend, so an execution is claimed the instant it is
        created and never queued behind another) has no pool to select from -
        using the generic query here would risk claiming a *different*
        PENDING row than the one this caller just created. Same atomicity and
        field semantics as :meth:`claim_for_dispatch` otherwise: bumps the
        fencing epoch and attempt count in the one conditional UPDATE.
        """
        current = now_ms() if now is None else now
        deadline = current + lease_seconds * 1000
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET state = ?,
                    lease_owner = ?,
                    lease_expires_at_ms = ?,
                    lease_epoch = lease_epoch + 1,
                    attempt = attempt + 1,
                    dispatched_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = ?
                RETURNING id
                """,
                (
                    RemoteExecutionState.DISPATCHING.value, owner, deadline,
                    execution_id, RemoteExecutionState.PENDING.value,
                ),
            )
            row = cursor.fetchone()

        return self.get_by_id(row["id"]) if row else None

    def renew_lease(
        self,
        execution_id: str,
        owner: str,
        epoch: int,
        lease_seconds: int,
        now: Optional[int] = None,
    ) -> bool:
        """Extend a lease. False means the caller no longer owns the row."""
        current = now_ms() if now is None else now
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET lease_expires_at_ms = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND lease_owner = ? AND lease_epoch = ?
                """,
                (current + lease_seconds * 1000, execution_id, owner, epoch),
            )
            return cursor.rowcount == 1

    def release_lease(self, execution_id: str, owner: str, epoch: int) -> bool:
        """Drop a lease without changing state. False if it was already taken."""
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET lease_owner = NULL,
                    lease_expires_at_ms = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND lease_owner = ? AND lease_epoch = ?
                """,
                (execution_id, owner, epoch),
            )
            return cursor.rowcount == 1

    def requeue_expired_leases(self, now: Optional[int] = None) -> int:
        """Return DISPATCHING rows whose lease lapsed to PENDING.

        This is what stops a dispatcher crash from stranding an execution.
        Increments ``lease_lapses``, not ``attempt`` - no worker necessarily
        ever saw this claim (see migration 118).
        """
        current = now_ms() if now is None else now
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET state = ?,
                    lease_owner = NULL,
                    lease_expires_at_ms = NULL,
                    lease_lapses = lease_lapses + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE state = ?
                  AND lease_expires_at_ms IS NOT NULL
                  AND lease_expires_at_ms <= ?
                """,
                (
                    RemoteExecutionState.PENDING.value,
                    RemoteExecutionState.DISPATCHING.value,
                    current,
                ),
            )
            return cursor.rowcount

    def apply_state(
        self,
        execution_id: str,
        target: RemoteExecutionState,
        worker_id: Optional[str] = None,
        provider_job_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> RemoteExecution:
        """Move a row to *target*, refusing a transition the machine forbids.

        Legality is checked against the row as it is in the database, not
        against whatever the caller last read.

        Re-arriving at the state the row is already in is absorbed here rather
        than modelled as an edge in LEGAL_TRANSITIONS, because event delivery is
        at-least-once and a redundant "still running" must be a no-op. A repeat
        into an *already terminal* state is not absorbed: the event cursor is
        what deduplicates, and a second `succeeded` means that failed.
        """
        current = self.get_by_id(execution_id)
        if current is None:
            raise ValueError(f"no remote execution {execution_id!r}")

        if current.state is target and not current.is_terminal:
            return current

        assert_transition(current.state, target)

        assignments = [
            "state = ?",
            "worker_id = COALESCE(?, worker_id)",
            "provider_job_id = COALESCE(?, provider_job_id)",
            "error_code = COALESCE(?, error_code)",
            "error_message = COALESCE(?, error_message)",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        if target is RemoteExecutionState.RUNNING:
            assignments.append("started_at = COALESCE(started_at, CURRENT_TIMESTAMP)")
        if target in TERMINAL_STATES:
            assignments.append("completed_at = CURRENT_TIMESTAMP")
            assignments.append("lease_owner = NULL")
            assignments.append("lease_expires_at_ms = NULL")

        with db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE remote_executions SET {', '.join(assignments)} "
                f"WHERE id = ? AND state = ?",
                (
                    target.value,
                    worker_id,
                    provider_job_id,
                    error_code,
                    error_message,
                    execution_id,
                    current.state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"remote execution {execution_id!r} changed state underneath "
                    f"this transition (expected {current.state.value})"
                )

        return self.get_by_id(execution_id)

    def advance_event_cursor(self, execution_id: str, cursor_value: int) -> bool:
        """Record that the next contiguous worker event was applied.

        Only accepts exactly ``event_cursor + 1``. An out-of-order or replayed
        event returns False and is dropped by the caller; a gap is never closed
        silently, because closing it would mean losing whatever fell in it.

        :meth:`record_event` is the transactional composite that also persists
        the event row - prefer it on the inbound path. This is kept for
        callers that only need the cursor moved (or that already have their
        own record of the event).
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET event_cursor = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND event_cursor = ?
                """,
                (cursor_value, execution_id, cursor_value - 1),
            )
            return cursor.rowcount == 1

    def record_event(
        self, execution_id: str, event: JobEventV1, now: Optional[int] = None
    ) -> bool:
        """Persist one worker event and advance the cursor, atomically.

        The cursor UPDATE and the event INSERT share one connection/transaction,
        so a crash between them cannot leave a persisted event whose cursor was
        never applied (or vice versa). Returns False - nothing written - on a
        replayed or out-of-order cursor, same contract as
        :meth:`advance_event_cursor`, which this makes the transactional entry
        point for on the inbound path.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET event_cursor = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND event_cursor = ?
                """,
                (event.cursor, execution_id, event.cursor - 1),
            )
            if cursor.rowcount != 1:
                return False

            cursor.execute(
                """
                INSERT INTO remote_execution_events
                    (execution_id, cursor, kind, pipe_id, emitted_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    event.cursor,
                    event.kind,
                    event.pipe_id,
                    event.emitted_at.isoformat(),
                    json.dumps(event.model_dump(mode="json")),
                ),
            )
            return True

    def apply_job_event(self, execution_id: str, event: JobEventV1) -> bool:
        """Persist ``event`` and, if it implies a state, move the row - the
        one place that turns a worker :class:`JobEventV1` into row state.

        Both the live dispatch path (:mod:`src.features.backends.native_remote_backend`)
        and the restart reconciler (:mod:`src.features.remote_execution.reconciler`)
        drive the row through this, so what a given event kind means for the
        row is decided in exactly one place. Returns what :meth:`record_event`
        returned - ``False`` for a replayed/out-of-order cursor, in which case
        the state transition (already applied when the event was first seen)
        is skipped rather than re-attempted.

        A worker keeps emitting its own progress (``staging``/``running``/...)
        right up until it notices a cancel, so one of those can legitimately
        arrive - and be journaled here - *after* :meth:`apply_state` has
        already moved the row to ``CANCELLING`` (core reacts to the worker's
        cancel acknowledgement immediately, not to a specific cursor). Such an
        event's *state implication* is silently dropped rather than raising:
        it is stale relative to a row that has already moved on, not a
        genuine protocol violation. The event itself is still persisted -
        only the state transition is skipped.
        """
        applied = self.record_event(execution_id, event)
        if not applied:
            return False

        target = state_for_event(event.kind)
        if target is not None:
            error = event.error
            try:
                self.apply_state(
                    execution_id, target, worker_id=event.worker_id,
                    error_code=error.code if error else None,
                    error_message=error.message if error else None,
                )
            except IllegalStateTransition:
                from src.platform.observability.logger import logger

                logger.warning(
                    f"[REMOTE_EXECUTION] Dropped a stale state implication from event "
                    f"cursor={event.cursor} kind={event.kind!r} for {execution_id!r} - "
                    f"the row already moved past it; the event itself is still recorded"
                )
        return True

    def list_events(self, execution_id: str, after_cursor: int = 0) -> List[JobEventV1]:
        """Every persisted event for *execution_id* with cursor > after_cursor, in order.

        Replayed from the stored payload rather than reconstructed from the
        summary columns, so a resume (EventResumeRequestV1) returns exactly
        what the worker sent, not a lossy projection of it.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT payload FROM remote_execution_events
                WHERE execution_id = ? AND cursor > ?
                ORDER BY cursor
                """,
                (execution_id, after_cursor),
            )
            rows = cursor.fetchall()
        return [JobEventV1.model_validate(json.loads(row["payload"])) for row in rows]

    def expire_overdue(self, now: Optional[int] = None) -> int:
        """Move any non-terminal row past its package deadline to EXPIRED.

        One SQL sweep across the four live states rather than four
        :meth:`apply_state` calls, mirroring :meth:`requeue_expired_leases`:
        the WHERE clause is what selects the rows in the first place, so a
        loop over states would still need to run this query per state.
        CANCELLING is deliberately excluded - EXPIRED is not a legal target
        from it (see LEGAL_TRANSITIONS), a cancel already in flight runs to
        its own conclusion.
        """
        current = now_ms() if now is None else now
        live_states = (
            RemoteExecutionState.PENDING.value,
            RemoteExecutionState.DISPATCHING.value,
            RemoteExecutionState.STAGING.value,
            RemoteExecutionState.RUNNING.value,
        )
        placeholders = ", ".join("?" for _ in live_states)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE remote_executions
                SET state = ?,
                    lease_owner = NULL,
                    lease_expires_at_ms = NULL,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE state IN ({placeholders})
                  AND expires_at_ms IS NOT NULL
                  AND expires_at_ms <= ?
                """,
                (RemoteExecutionState.EXPIRED.value, *live_states, current),
            )
            return cursor.rowcount

    def fail_exhausted(self, max_attempts: int) -> int:
        """Fail PENDING rows that have used up every dispatch attempt.

        A row lands in PENDING after a lease lapse or a retryable-failure
        requeue; once its ``attempt`` count reaches the cap,
        :meth:`claim_for_dispatch`'s own filter will skip it forever, so
        something must move it to FAILED explicitly rather than leaving it to
        starve in the queue.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET state = ?,
                    error_code = 'attempts_exhausted',
                    error_message = COALESCE(error_message, 'dispatch attempts exhausted'),
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE state = ? AND attempt >= ?
                """,
                (
                    RemoteExecutionState.FAILED.value,
                    RemoteExecutionState.PENDING.value,
                    max_attempts,
                ),
            )
            return cursor.rowcount

    def requeue_for_retry(
        self, execution_id: str, now: Optional[int] = None
    ) -> RemoteExecution:
        """Send a row back to PENDING after a retryable worker failure.

        Unlike :meth:`apply_state`, this clears ``worker_id``/
        ``provider_job_id``: the next dispatch may land on an entirely
        different worker, and a stale binding would misroute
        :meth:`get_by_provider_job`. ``attempt`` is left untouched -
        :meth:`claim_for_dispatch` is what counts attempts, not this method -
        so a caller must still consult :func:`policy.should_requeue` before
        calling this rather than after.
        """
        current = self.get_by_id(execution_id)
        if current is None:
            raise ValueError(f"no remote execution {execution_id!r}")

        assert_transition(current.state, RemoteExecutionState.PENDING)

        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE remote_executions
                SET state = ?,
                    worker_id = NULL,
                    provider_job_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at_ms = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = ?
                """,
                (
                    RemoteExecutionState.PENDING.value,
                    execution_id,
                    current.state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"remote execution {execution_id!r} changed state underneath "
                    f"this transition (expected {current.state.value})"
                )

        return self.get_by_id(execution_id)

    def delete(self, execution_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM remote_executions WHERE id = ?", (execution_id,)
            )
            return cursor.rowcount == 1
