"""The remote-execution state machine and the event mapping that drives it."""

import pytest

from src.features.remote_execution.records import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalStateTransition,
    RemoteExecution,
    RemoteExecutionState,
    assert_transition,
    is_legal_transition,
    state_for_event,
)
from src.platform.worker_protocol import JobEventKind

S = RemoteExecutionState


class TestTransitionTable:
    def test_every_state_has_an_entry(self):
        assert set(LEGAL_TRANSITIONS) == set(S)

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_terminal_states_go_nowhere(self, state):
        assert LEGAL_TRANSITIONS[state] == frozenset()
        for target in S:
            assert not is_legal_transition(state, target)

    @pytest.mark.parametrize(
        "current,target",
        [
            (S.PENDING, S.DISPATCHING),
            (S.DISPATCHING, S.STAGING),
            (S.DISPATCHING, S.RUNNING),
            (S.STAGING, S.RUNNING),
            (S.STAGING, S.PENDING),
            (S.RUNNING, S.SUCCEEDED),
            (S.RUNNING, S.FAILED),
            (S.RUNNING, S.CANCELLING),
            (S.RUNNING, S.PENDING),
            (S.CANCELLING, S.CANCELLED),
            (S.PENDING, S.CANCELLED),
        ],
    )
    def test_legal_transitions(self, current, target):
        assert is_legal_transition(current, target)
        assert_transition(current, target)

    @pytest.mark.parametrize(
        "current,target",
        [
            (S.SUCCEEDED, S.RUNNING),
            (S.SUCCEEDED, S.SUCCEEDED),
            (S.FAILED, S.PENDING),
            (S.CANCELLED, S.CANCELLING),
            (S.EXPIRED, S.RUNNING),
            (S.PENDING, S.RUNNING),
            (S.PENDING, S.STAGING),
            (S.PENDING, S.SUCCEEDED),
            (S.RUNNING, S.DISPATCHING),
            (S.RUNNING, S.STAGING),
            (S.STAGING, S.CANCELLED),
            (S.STAGING, S.DISPATCHING),
            (S.CANCELLING, S.RUNNING),
        ],
    )
    def test_illegal_transitions_are_rejected(self, current, target):
        assert not is_legal_transition(current, target)
        with pytest.raises(IllegalStateTransition) as excinfo:
            assert_transition(current, target)
        assert current.value in str(excinfo.value)
        assert target.value in str(excinfo.value)

    @pytest.mark.parametrize("state", sorted(S, key=lambda s: s.value))
    def test_identity_is_not_a_move(self, state):
        """House convention: a no-op is absorbed by the repository, not an edge."""
        assert not is_legal_transition(state, state)

    def test_a_stalled_dispatch_can_be_requeued(self):
        assert is_legal_transition(S.DISPATCHING, S.PENDING)

    def test_a_worker_may_finish_while_a_cancel_is_in_flight(self):
        assert is_legal_transition(S.CANCELLING, S.SUCCEEDED)

    def test_a_retryable_failure_can_be_requeued_from_running_or_staging(self):
        assert is_legal_transition(S.RUNNING, S.PENDING)
        assert is_legal_transition(S.STAGING, S.PENDING)


class TestEventMapping:
    @pytest.mark.parametrize(
        "kind,expected",
        [
            (JobEventKind.STAGING.value, S.STAGING),
            (JobEventKind.RUNNING.value, S.RUNNING),
            (JobEventKind.SUCCEEDED.value, S.SUCCEEDED),
            (JobEventKind.FAILED.value, S.FAILED),
            (JobEventKind.CANCELLED.value, S.CANCELLED),
        ],
    )
    def test_lifecycle_events_move_the_row(self, kind, expected):
        assert state_for_event(kind) is expected

    def test_a_rejection_moves_the_row_to_failed(self):
        """The wire kind stays distinct from FAILED for dashboards; the state
        machine treats it identically."""
        assert state_for_event(JobEventKind.REJECTED.value) is S.FAILED

    @pytest.mark.parametrize(
        "kind",
        [
            JobEventKind.PIPE_PROGRESS.value,
            JobEventKind.OUTPUT.value,
            JobEventKind.LOG.value,
            JobEventKind.HEARTBEAT.value,
            JobEventKind.ACCEPTED.value,
            "something_a_plugin_pipe_invented",
        ],
    )
    def test_progress_events_leave_the_state_alone(self, kind):
        assert state_for_event(kind) is None

    def test_no_worker_event_can_reach_a_state_core_reserves(self):
        """EXPIRED, DISPATCHING and CANCELLING are core decisions, not worker ones."""
        reachable = set(state_for_event(k.value) for k in JobEventKind)
        reachable.discard(None)
        assert S.EXPIRED not in reachable
        assert S.DISPATCHING not in reachable
        assert S.CANCELLING not in reachable
        assert S.PENDING not in reachable


class TestRecord:
    def _record(self, **overrides) -> RemoteExecution:
        base = dict(
            id="exec-1",
            provider="example-provider",
            state=S.PENDING,
            idempotency_key="idem-1",
            request_digest="sha256:" + "a" * 64,
        )
        base.update(overrides)
        return RemoteExecution(**base)

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_is_terminal(self, state):
        assert self._record(state=state).is_terminal

    def test_is_not_terminal_while_running(self):
        assert not self._record(state=S.RUNNING).is_terminal

    def test_next_expected_cursor_is_the_resume_point(self):
        assert self._record().next_expected_cursor == 1
        assert self._record(event_cursor=7).next_expected_cursor == 8

    def test_to_dict_serializes_the_state_as_its_wire_value(self):
        assert self._record(state=S.RUNNING).to_dict()["state"] == "running"
