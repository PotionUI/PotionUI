"""RemoteExecutionRepository against a real migrated sqlite file.

The repository resolves `db` at call time from `src.platform.database.database`,
so patching that one canonical name redirects it to the test database below.
"""

import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.features.remote_execution.records import (
    IllegalStateTransition,
    RemoteExecution,
    RemoteExecutionState,
)
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner
from src.platform.worker_protocol import JobErrorV1, JobEventKind, JobEventV1

S = RemoteExecutionState


class RemoteExecutionRepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        self._run_migrations()
        self.repo = RemoteExecutionRepository()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        for leftover in Path(self.temp_dir).iterdir():
            leftover.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _run_migrations(self):
        manager = MigrationRunner()
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            manager.run_migrations()
        finally:
            sys.stdout = old_stdout

    def _new(self, key: str = "idem-1", **overrides) -> RemoteExecution:
        record = RemoteExecution(
            id="",
            provider="example-provider",
            state=S.PENDING,
            idempotency_key=key,
            request_digest="sha256:" + "a" * 64,
            metadata={"generation_id": "gen-1"},
            **overrides,
        )
        return self.repo.create(record)

    def _event(self, execution_id: str, cursor: int, kind: str = JobEventKind.RUNNING.value, **overrides) -> JobEventV1:
        return JobEventV1(
            execution_id=execution_id,
            worker_id="worker-1",
            cursor=cursor,
            emitted_at=datetime(2026, 8, 15, 0, 0, 0, tzinfo=timezone.utc),
            kind=kind,
            **overrides,
        )


class TestCreateAndRead(RemoteExecutionRepositoryTestCase):
    def test_create_round_trips_every_column(self):
        created = self._new()
        loaded = self.repo.get_by_id(created.id)

        self.assertEqual(loaded.provider, "example-provider")
        self.assertEqual(loaded.state, S.PENDING)
        self.assertEqual(loaded.metadata, {"generation_id": "gen-1"})
        self.assertEqual(loaded.event_cursor, 0)
        self.assertEqual(loaded.lease_epoch, 0)
        self.assertIsNone(loaded.lease_owner)
        self.assertIsNotNone(loaded.created_at)

    def test_an_id_is_minted_when_absent(self):
        self.assertTrue(self._new().id)

    def test_resubmitting_the_same_key_returns_the_original_row(self):
        first = self._new(key="same")
        second = self._new(key="same")

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.repo.list_by_state(S.PENDING)), 1)

    def test_the_unique_key_is_enforced_by_the_schema_not_only_by_the_check(self):
        """A concurrent insert that slips past the read must still be refused."""
        self._new(key="same")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO remote_executions
                        (id, provider, state, idempotency_key, request_digest)
                    VALUES ('other', 'p', 'pending', 'same', 'd')
                    """
                )

    def test_lookup_by_provider_job_id(self):
        created = self._new()
        self.repo.apply_state(
            created.id, S.DISPATCHING, provider_job_id="provider-job-9"
        )

        found = self.repo.get_by_provider_job("example-provider", "provider-job-9")
        self.assertEqual(found.id, created.id)
        self.assertIsNone(self.repo.get_by_provider_job("other", "provider-job-9"))

    def test_two_rows_cannot_claim_one_provider_job(self):
        first = self._new(key="a")
        second = self._new(key="b")
        self.repo.apply_state(first.id, S.DISPATCHING, provider_job_id="job-1")

        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.apply_state(second.id, S.DISPATCHING, provider_job_id="job-1")

    def test_many_rows_may_have_no_provider_job_yet(self):
        self._new(key="a")
        self._new(key="b")
        self.assertEqual(len(self.repo.list_by_state(S.PENDING)), 2)


class TestDispatchLease(RemoteExecutionRepositoryTestCase):
    def test_claiming_moves_the_row_and_takes_the_lease(self):
        created = self._new()
        claimed = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)

        self.assertEqual(claimed.id, created.id)
        self.assertEqual(claimed.state, S.DISPATCHING)
        self.assertEqual(claimed.lease_owner, "dispatcher-a")
        self.assertEqual(claimed.lease_expires_at_ms, 61_000)
        self.assertEqual(claimed.lease_epoch, 1)
        self.assertEqual(claimed.attempt, 1)

    def test_only_one_of_two_dispatchers_gets_the_row(self):
        self._new()

        first = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)
        second = self.repo.claim_for_dispatch("dispatcher-b", 60, now=1_000)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_claims_are_taken_oldest_first(self):
        first = self._new(key="a")
        second = self._new(key="b")

        self.assertEqual(
            self.repo.claim_for_dispatch("d", 60, now=1_000).id, first.id
        )
        self.assertEqual(
            self.repo.claim_for_dispatch("d", 60, now=1_000).id, second.id
        )

    def test_nothing_to_claim_returns_none(self):
        self.assertIsNone(self.repo.claim_for_dispatch("d", 60, now=1_000))

    def test_a_lapsed_lease_returns_the_row_to_the_queue(self):
        created = self._new()
        self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)

        self.assertEqual(self.repo.requeue_expired_leases(now=60_999), 0)
        self.assertEqual(self.repo.requeue_expired_leases(now=61_000), 1)

        requeued = self.repo.get_by_id(created.id)
        self.assertEqual(requeued.state, S.PENDING)
        self.assertIsNone(requeued.lease_owner)
        self.assertEqual(requeued.attempt, 1)

    def test_a_requeued_row_can_be_claimed_by_someone_else(self):
        self._new()
        self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)
        self.repo.requeue_expired_leases(now=61_000)

        retaken = self.repo.claim_for_dispatch("dispatcher-b", 60, now=62_000)
        self.assertEqual(retaken.lease_owner, "dispatcher-b")
        self.assertEqual(retaken.lease_epoch, 2)
        self.assertEqual(retaken.attempt, 2)

    def test_a_stale_epoch_cannot_renew_a_lease_it_no_longer_holds(self):
        """The fencing token: a paused dispatcher must not resurrect its claim."""
        created = self._new()
        first = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)
        self.repo.requeue_expired_leases(now=61_000)
        self.repo.claim_for_dispatch("dispatcher-b", 60, now=62_000)

        self.assertFalse(
            self.repo.renew_lease(
                created.id, "dispatcher-a", first.lease_epoch, 60, now=62_000
            )
        )
        self.assertEqual(self.repo.get_by_id(created.id).lease_owner, "dispatcher-b")

    def test_the_current_holder_can_renew(self):
        created = self._new()
        claimed = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)

        self.assertTrue(
            self.repo.renew_lease(
                created.id, "dispatcher-a", claimed.lease_epoch, 120, now=30_000
            )
        )
        self.assertEqual(self.repo.get_by_id(created.id).lease_expires_at_ms, 150_000)

    def test_releasing_frees_the_row_without_changing_state(self):
        created = self._new()
        claimed = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)

        self.assertTrue(
            self.repo.release_lease(created.id, "dispatcher-a", claimed.lease_epoch)
        )
        released = self.repo.get_by_id(created.id)
        self.assertIsNone(released.lease_owner)
        self.assertEqual(released.state, S.DISPATCHING)

    def test_a_non_holder_cannot_release(self):
        created = self._new()
        claimed = self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)

        self.assertFalse(
            self.repo.release_lease(created.id, "dispatcher-b", claimed.lease_epoch)
        )

    def test_reaching_a_terminal_state_drops_the_lease(self):
        created = self._new()
        self.repo.claim_for_dispatch("dispatcher-a", 60, now=1_000)
        self.repo.apply_state(created.id, S.RUNNING)
        done = self.repo.apply_state(created.id, S.SUCCEEDED)

        self.assertIsNone(done.lease_owner)
        self.assertIsNone(done.lease_expires_at_ms)
        self.assertIsNotNone(done.completed_at)


class TestStateTransitions(RemoteExecutionRepositoryTestCase):
    def test_a_legal_transition_is_persisted(self):
        created = self._new()
        moved = self.repo.apply_state(created.id, S.DISPATCHING, worker_id="worker-1")

        self.assertEqual(moved.state, S.DISPATCHING)
        self.assertEqual(moved.worker_id, "worker-1")

    def test_an_illegal_transition_is_refused_and_changes_nothing(self):
        created = self._new()

        with self.assertRaises(IllegalStateTransition):
            self.repo.apply_state(created.id, S.SUCCEEDED)

        self.assertEqual(self.repo.get_by_id(created.id).state, S.PENDING)

    def test_a_terminal_row_cannot_be_moved_again(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        self.repo.apply_state(created.id, S.RUNNING)
        self.repo.apply_state(created.id, S.SUCCEEDED)

        with self.assertRaises(IllegalStateTransition):
            self.repo.apply_state(created.id, S.RUNNING)
        with self.assertRaises(IllegalStateTransition):
            self.repo.apply_state(created.id, S.SUCCEEDED)

    def test_running_stamps_started_at_once(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        first = self.repo.apply_state(created.id, S.RUNNING)
        again = self.repo.apply_state(created.id, S.RUNNING)

        self.assertIsNotNone(first.started_at)
        self.assertEqual(first.started_at, again.started_at)

    def test_a_failure_records_its_reason(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        failed = self.repo.apply_state(
            created.id, S.FAILED, error_code="cuda_oom", error_message="out of memory"
        )

        self.assertEqual(failed.error_code, "cuda_oom")
        self.assertEqual(failed.error_message, "out of memory")

    def test_transitioning_a_missing_row_is_an_error(self):
        with self.assertRaises(ValueError):
            self.repo.apply_state("no-such-execution", S.DISPATCHING)


class TestEventCursor(RemoteExecutionRepositoryTestCase):
    def test_contiguous_events_advance_the_cursor(self):
        created = self._new()

        for expected in (1, 2, 3):
            self.assertTrue(self.repo.advance_event_cursor(created.id, expected))
            self.assertEqual(self.repo.get_by_id(created.id).event_cursor, expected)

    def test_a_replayed_event_is_refused(self):
        created = self._new()
        self.repo.advance_event_cursor(created.id, 1)

        self.assertFalse(self.repo.advance_event_cursor(created.id, 1))
        self.assertEqual(self.repo.get_by_id(created.id).event_cursor, 1)

    def test_a_gap_is_never_closed_silently(self):
        """Accepting cursor 5 after 1 would discard events 2-4 without a trace."""
        created = self._new()
        self.repo.advance_event_cursor(created.id, 1)

        self.assertFalse(self.repo.advance_event_cursor(created.id, 5))
        self.assertEqual(self.repo.get_by_id(created.id).event_cursor, 1)

    def test_the_cursor_is_the_resume_point(self):
        created = self._new()
        self.repo.advance_event_cursor(created.id, 1)
        self.repo.advance_event_cursor(created.id, 2)

        self.assertEqual(self.repo.get_by_id(created.id).next_expected_cursor, 3)


class TestDelete(RemoteExecutionRepositoryTestCase):
    def test_delete_removes_the_row(self):
        created = self._new()

        self.assertTrue(self.repo.delete(created.id))
        self.assertIsNone(self.repo.get_by_id(created.id))
        self.assertFalse(self.repo.delete(created.id))


class TestRecordEvent(RemoteExecutionRepositoryTestCase):
    def test_a_contiguous_event_is_persisted_and_advances_the_cursor(self):
        created = self._new()
        event = self._event(created.id, 1)

        self.assertTrue(self.repo.record_event(created.id, event))
        self.assertEqual(self.repo.get_by_id(created.id).event_cursor, 1)
        self.assertEqual(self.repo.list_events(created.id), [event])

    def test_a_replayed_event_is_rejected_and_not_stored_twice(self):
        created = self._new()
        first = self._event(created.id, 1)
        self.assertTrue(self.repo.record_event(created.id, first))

        replay = self._event(created.id, 1, detail="a different retelling")
        self.assertFalse(self.repo.record_event(created.id, replay))

        self.assertEqual(len(self.repo.list_events(created.id)), 1)
        self.assertEqual(self.repo.list_events(created.id)[0], first)

    def test_an_out_of_order_event_is_rejected_and_not_stored(self):
        created = self._new()
        self.repo.record_event(created.id, self._event(created.id, 1))

        gap = self._event(created.id, 5)
        self.assertFalse(self.repo.record_event(created.id, gap))

        self.assertEqual(self.repo.get_by_id(created.id).event_cursor, 1)
        self.assertEqual(len(self.repo.list_events(created.id)), 1)

    def test_list_events_replays_in_cursor_order(self):
        created = self._new()
        third = self._event(created.id, 3, kind=JobEventKind.PIPE_PROGRESS.value)
        second = self._event(created.id, 2, kind=JobEventKind.STAGING.value)
        first = self._event(created.id, 1, kind=JobEventKind.ACCEPTED.value)

        # Deliberately fed in cursor order (out-of-order arrival is covered
        # above) - this test is about the read side, not acceptance order.
        for event in (first, second, third):
            self.assertTrue(self.repo.record_event(created.id, event))

        self.assertEqual(self.repo.list_events(created.id), [first, second, third])

    def test_list_events_after_a_cursor_resumes_from_there(self):
        created = self._new()
        for cursor in (1, 2, 3):
            self.repo.record_event(created.id, self._event(created.id, cursor))

        resumed = self.repo.list_events(created.id, after_cursor=1)
        self.assertEqual([e.cursor for e in resumed], [2, 3])

    def test_a_rejection_event_round_trips_through_the_repository(self):
        created = self._new()
        rejected = self._event(
            created.id, 1, kind=JobEventKind.REJECTED.value,
            error=JobErrorV1(code="cuda_oom", message="oom", retryable=True),
        )
        self.assertTrue(self.repo.record_event(created.id, rejected))
        self.assertEqual(self.repo.list_events(created.id)[0].error.code, "cuda_oom")


class TestExpireOverdue(RemoteExecutionRepositoryTestCase):
    def test_a_row_past_its_deadline_expires(self):
        created = self._new(expires_at_ms=1_000)

        self.assertEqual(self.repo.expire_overdue(now=999), 0)
        self.assertEqual(self.repo.expire_overdue(now=1_000), 1)

        expired = self.repo.get_by_id(created.id)
        self.assertEqual(expired.state, S.EXPIRED)
        self.assertIsNotNone(expired.completed_at)

    def test_a_row_without_a_deadline_never_expires(self):
        self._new()
        self.assertEqual(self.repo.expire_overdue(now=10**15), 0)

    def test_expiry_sweeps_every_live_state(self):
        for state, key in (
            (S.PENDING, "a"), (S.DISPATCHING, "b"), (S.STAGING, "c"), (S.RUNNING, "d"),
        ):
            created = self._new(key=key, expires_at_ms=500)
            if state is not S.PENDING:
                self.repo.apply_state(created.id, S.DISPATCHING)
            if state in (S.STAGING, S.RUNNING):
                self.repo.apply_state(created.id, state)

        self.assertEqual(self.repo.expire_overdue(now=1_000), 4)
        for row in self.repo.list_by_state(S.EXPIRED, limit=10):
            self.assertIsNone(row.lease_owner)

    def test_a_terminal_row_is_left_alone(self):
        created = self._new(expires_at_ms=500)
        self.repo.apply_state(created.id, S.DISPATCHING)
        self.repo.apply_state(created.id, S.CANCELLING)
        self.repo.apply_state(created.id, S.CANCELLED)

        self.assertEqual(self.repo.expire_overdue(now=1_000), 0)
        self.assertEqual(self.repo.get_by_id(created.id).state, S.CANCELLED)

    def test_an_expired_rows_lease_is_cleared(self):
        created = self._new(expires_at_ms=500)
        self.repo.claim_for_dispatch("dispatcher-a", 60, now=100)

        self.repo.expire_overdue(now=1_000)

        expired = self.repo.get_by_id(created.id)
        self.assertIsNone(expired.lease_owner)
        self.assertIsNone(expired.lease_expires_at_ms)


class TestAttemptCapAndExhaustion(RemoteExecutionRepositoryTestCase):
    def test_claim_skips_a_row_at_the_attempt_cap(self):
        created = self._new()
        self.repo.claim_for_dispatch("d", 60, now=1_000, max_attempts=1)
        self.repo.requeue_expired_leases(now=61_000)  # back to PENDING, attempt=1

        self.assertEqual(self.repo.get_by_id(created.id).attempt, 1)
        self.assertIsNone(
            self.repo.claim_for_dispatch("d", 60, now=62_000, max_attempts=1)
        )

    def test_claim_still_takes_a_row_under_the_cap(self):
        self._new()
        claimed = self.repo.claim_for_dispatch("d", 60, now=1_000, max_attempts=3)
        self.assertIsNotNone(claimed)

    def test_fail_exhausted_moves_capped_pending_rows_to_failed(self):
        created = self._new()
        self.repo.claim_for_dispatch("d", 60, now=1_000)
        self.repo.requeue_expired_leases(now=61_000)  # PENDING, attempt=1

        self.assertEqual(self.repo.fail_exhausted(max_attempts=1), 1)

        failed = self.repo.get_by_id(created.id)
        self.assertEqual(failed.state, S.FAILED)
        self.assertEqual(failed.error_code, "attempts_exhausted")

    def test_fail_exhausted_leaves_rows_under_the_cap_alone(self):
        self._new()
        self.assertEqual(self.repo.fail_exhausted(max_attempts=3), 0)

    def test_requeue_expired_leases_bumps_lease_lapses_not_attempt(self):
        created = self._new()
        self.repo.claim_for_dispatch("d", 60, now=1_000)

        self.repo.requeue_expired_leases(now=61_000)

        requeued = self.repo.get_by_id(created.id)
        self.assertEqual(requeued.lease_lapses, 1)
        self.assertEqual(requeued.attempt, 1)  # bumped by claim_for_dispatch, not this


class TestRequeueForRetry(RemoteExecutionRepositoryTestCase):
    def test_requeues_from_running_to_pending(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING, worker_id="w1", provider_job_id="job-1")
        self.repo.apply_state(created.id, S.RUNNING)

        requeued = self.repo.requeue_for_retry(created.id)

        self.assertEqual(requeued.state, S.PENDING)
        self.assertIsNone(requeued.worker_id)
        self.assertIsNone(requeued.provider_job_id)
        self.assertIsNone(requeued.lease_owner)

    def test_requeues_from_staging_to_pending(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        self.repo.apply_state(created.id, S.STAGING)

        requeued = self.repo.requeue_for_retry(created.id)
        self.assertEqual(requeued.state, S.PENDING)

    def test_attempt_is_left_untouched(self):
        created = self._new()
        self.repo.claim_for_dispatch("d", 60, now=1_000)
        self.repo.apply_state(created.id, S.RUNNING)

        requeued = self.repo.requeue_for_retry(created.id)
        self.assertEqual(requeued.attempt, 1)

    def test_illegal_from_a_terminal_state(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        self.repo.apply_state(created.id, S.RUNNING)
        self.repo.apply_state(created.id, S.SUCCEEDED)

        with self.assertRaises(IllegalStateTransition):
            self.repo.requeue_for_retry(created.id)

    def test_a_missing_row_is_an_error(self):
        with self.assertRaises(ValueError):
            self.repo.requeue_for_retry("no-such-execution")


class TestRejectionMovesToFailed(RemoteExecutionRepositoryTestCase):
    def test_a_rejected_event_moves_the_row_to_failed(self):
        from src.features.remote_execution.records import state_for_event

        created = self._new()
        target = state_for_event(JobEventKind.REJECTED.value)

        failed = self.repo.apply_state(
            created.id, target, error_code="fingerprint_mismatch",
            error_message="pipe catalog mismatch",
        )
        self.assertEqual(failed.state, S.FAILED)
        self.assertEqual(failed.error_code, "fingerprint_mismatch")


class TestClaimSpecific(RemoteExecutionRepositoryTestCase):
    """The single-slot dispatch path's own claim: a KNOWN row id, not
    claim_for_dispatch's oldest-first pool selection."""

    def test_claims_the_named_row_and_takes_the_lease(self):
        created = self._new()
        claimed = self.repo.claim_specific(created.id, "backend-a", 60, now=1_000)

        self.assertEqual(claimed.state, S.DISPATCHING)
        self.assertEqual(claimed.lease_owner, "backend-a")
        self.assertEqual(claimed.lease_expires_at_ms, 61_000)
        self.assertEqual(claimed.lease_epoch, 1)
        self.assertEqual(claimed.attempt, 1)

    def test_never_claims_a_different_pending_row(self):
        """The whole reason this exists instead of claim_for_dispatch: a
        second, older PENDING row must be left completely alone."""
        older = self._new(key="older")
        target = self._new(key="target")

        claimed = self.repo.claim_specific(target.id, "backend-a", 60, now=1_000)

        self.assertEqual(claimed.id, target.id)
        self.assertEqual(self.repo.get_by_id(older.id).state, S.PENDING)
        self.assertIsNone(self.repo.get_by_id(older.id).lease_owner)

    def test_a_row_not_in_pending_cannot_be_claimed(self):
        created = self._new()
        self.repo.claim_specific(created.id, "backend-a", 60, now=1_000)

        self.assertIsNone(self.repo.claim_specific(created.id, "backend-b", 60, now=2_000))

    def test_an_unknown_id_returns_none(self):
        self.assertIsNone(self.repo.claim_specific("no-such-execution", "backend-a", 60))


class TestApplyJobEvent(RemoteExecutionRepositoryTestCase):
    """The one place a worker JobEventV1 becomes row state - shared by the
    live dispatch path and the restart reconciler."""

    def test_a_state_carrying_event_both_persists_and_transitions(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        event = self._event(created.id, 1, kind=JobEventKind.RUNNING.value)

        self.assertTrue(self.repo.apply_job_event(created.id, event))

        row = self.repo.get_by_id(created.id)
        self.assertEqual(row.state, S.RUNNING)
        self.assertEqual(row.event_cursor, 1)
        self.assertEqual(self.repo.list_events(created.id), [event])

    def test_an_event_with_no_state_mapping_only_persists(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        event = self._event(created.id, 1, kind=JobEventKind.PIPE_PROGRESS.value)

        self.assertTrue(self.repo.apply_job_event(created.id, event))
        self.assertEqual(self.repo.get_by_id(created.id).state, S.DISPATCHING)

    def test_a_failure_event_carries_its_error_onto_the_row(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        event = self._event(
            created.id, 1, kind=JobEventKind.FAILED.value,
            error=JobErrorV1(code="cuda_oom", message="out of memory", retryable=True),
        )

        self.repo.apply_job_event(created.id, event)

        row = self.repo.get_by_id(created.id)
        self.assertEqual(row.state, S.FAILED)
        self.assertEqual(row.error_code, "cuda_oom")
        self.assertEqual(row.error_message, "out of memory")

    def test_a_replayed_event_is_a_no_op(self):
        created = self._new()
        self.repo.apply_state(created.id, S.DISPATCHING)
        event = self._event(created.id, 1, kind=JobEventKind.RUNNING.value)
        self.repo.apply_job_event(created.id, event)

        self.assertFalse(self.repo.apply_job_event(created.id, event))
        self.assertEqual(self.repo.get_by_id(created.id).state, S.RUNNING)
