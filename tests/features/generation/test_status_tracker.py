import asyncio
import time
from unittest.mock import Mock, patch

import pytest

from src.features.generation.status_tracker import (
    GenerationState,
    GenerationStatusTracker,
)
from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.outputs import Progress


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.status_tracker.generation_repo') as mock_repo:
        mock_repo.update_status = Mock(return_value=True)
        yield mock_repo


@pytest.fixture
def tracker():
    return GenerationStatusTracker()


class TestCreate:
    def test_create_returns_pending_record(self, tracker):
        record = tracker.create(id='gen1', preset_id='preset_a', backend_id='local_1', user_id='user1')

        assert record.id == 'gen1'
        assert record.preset_id == 'preset_a'
        assert record.backend_id == 'local_1'
        assert record.user_id == 'user1'
        assert record.state == GenerationState.PENDING
        assert record.completed_at is None

    def test_create_does_not_write_to_db(self, tracker, mock_generation_repo):
        """Creation is already reflected by the initial DB insert; transition() is the write path."""
        tracker.create(id='gen1')
        mock_generation_repo.update_status.assert_not_called()


class TestUpdateFromOutput:
    def test_updates_progress_from_progress_object(self, tracker):
        tracker.create(id='gen1')
        output = ProgressGenerationOutput(state='Generating', progress=Progress(current=5, max=10))

        tracker.update_from_output('gen1', output)

        record = tracker.get('gen1')
        assert record.progress == 0.5

    def test_updates_progress_from_float(self, tracker):
        tracker.create(id='gen1')
        output = Mock(progress=0.42, current_step=None, current_step_num=None, total_steps=None)

        tracker.update_from_output('gen1', output)

        assert tracker.get('gen1').progress == 0.42

    def test_updates_step_fields(self, tracker):
        tracker.create(id='gen1')
        output = Mock(progress=None, current_step='Sampling', current_step_num=3, total_steps=10)

        tracker.update_from_output('gen1', output)

        record = tracker.get('gen1')
        assert record.current_step == 'Sampling'
        assert record.current_step_num == 3
        assert record.total_steps == 10

    def test_unknown_generation_is_a_noop(self, tracker):
        # Should not raise
        tracker.update_from_output('missing', Mock(progress=0.5))

    def test_zero_max_progress_is_zero(self, tracker):
        tracker.create(id='gen1')
        output = ProgressGenerationOutput(state='x', progress=Progress(current=5, max=0))

        tracker.update_from_output('gen1', output)

        assert tracker.get('gen1').progress == 0.0


class TestTransition:
    def test_transition_updates_state(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')

        record = tracker.transition('gen1', GenerationState.RUNNING)

        assert record.state == GenerationState.RUNNING
        assert record.completed_at is None

    def test_transition_to_terminal_state_sets_completed_at(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')

        record = tracker.transition('gen1', GenerationState.COMPLETED)

        assert record.completed_at is not None

    def test_transition_writes_db_exactly_once(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')

        tracker.transition('gen1', GenerationState.COMPLETED)

        mock_generation_repo.update_status.assert_called_once_with('gen1', 'completed', error_message=None)

    def test_transition_with_error_persists_message(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')

        record = tracker.transition('gen1', GenerationState.FAILED, error='boom')

        assert record.error == 'boom'
        assert record.message == 'boom'
        mock_generation_repo.update_status.assert_called_once_with('gen1', 'failed', error_message='boom')

    def test_transition_unknown_generation_returns_none(self, tracker, mock_generation_repo):
        result = tracker.transition('missing', GenerationState.COMPLETED)

        assert result is None
        mock_generation_repo.update_status.assert_not_called()

    def test_transition_refuses_cancelled_to_failed(self, tracker, mock_generation_repo):
        """A cancel already transitioned + broadcast; a later FAILED write
        (e.g. from a stray error output) must not overwrite it in memory or
        in the database."""
        tracker.create(id='gen1')
        tracker.transition('gen1', GenerationState.CANCELLED)
        mock_generation_repo.update_status.reset_mock()

        record = tracker.transition('gen1', GenerationState.FAILED, error='boom')

        assert record.state == GenerationState.CANCELLED
        assert tracker.get('gen1').state == GenerationState.CANCELLED
        mock_generation_repo.update_status.assert_not_called()

    def test_transition_allows_same_terminal_state(self, tracker, mock_generation_repo):
        """An idempotent same-state write (e.g. a duplicate CANCELLED call)
        is not a cross-state overwrite and should still persist."""
        tracker.create(id='gen1')
        tracker.transition('gen1', GenerationState.CANCELLED)
        mock_generation_repo.update_status.reset_mock()

        record = tracker.transition('gen1', GenerationState.CANCELLED)

        assert record.state == GenerationState.CANCELLED
        mock_generation_repo.update_status.assert_called_once_with('gen1', 'cancelled', error_message=None)

    def test_db_failure_does_not_raise(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')
        mock_generation_repo.update_status.side_effect = Exception("db down")

        # Should not raise - db failures are logged, not propagated.
        record = tracker.transition('gen1', GenerationState.FAILED, error='x')
        assert record.state == GenerationState.FAILED


class TestTransitionAsync:
    @pytest.mark.asyncio
    async def test_runs_off_the_event_loop(self, tracker, mock_generation_repo, monkeypatch):
        recorded = []
        real_to_thread = asyncio.to_thread

        async def recording_to_thread(func, *args, **kwargs):
            recorded.append(func)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(
            'src.features.generation.status_tracker.asyncio.to_thread',
            recording_to_thread,
        )
        tracker.create(id='gen1')

        record = await tracker.transition_async('gen1', GenerationState.RUNNING)

        assert record.state == GenerationState.RUNNING
        assert tracker.transition in recorded
        mock_generation_repo.update_status.assert_called_once_with('gen1', 'running', error_message=None)

    @pytest.mark.asyncio
    async def test_sequential_transitions_hit_the_repo_in_call_order(self, tracker, mock_generation_repo):
        """Regression guard for the cancelled-never-becomes-failed ordering:
        `await transition_async(...)` must not return until that
        transition's DB write has actually happened, even though the write
        runs on a worker thread. A first call slow enough to still be
        in-flight when the second starts would land its write after the
        second's if the caller didn't really wait for the thread."""
        calls = []

        def record_write(gen_id, state, error_message=None):
            if not calls:
                time.sleep(0.05)
            calls.append(state)

        mock_generation_repo.update_status.side_effect = record_write
        tracker.create(id='gen1')

        await tracker.transition_async('gen1', GenerationState.RUNNING)
        await tracker.transition_async('gen1', GenerationState.COMPLETED)

        assert calls == ['running', 'completed']


class TestListingAndPruning:
    def test_list_active_only_includes_pending_and_running(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')
        tracker.create(id='gen2')
        tracker.transition('gen2', GenerationState.RUNNING)
        tracker.create(id='gen3')
        tracker.transition('gen3', GenerationState.COMPLETED)

        active_ids = {r.id for r in tracker.list_active()}
        assert active_ids == {'gen1', 'gen2'}

    def test_list_all_includes_terminal_states(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')
        tracker.transition('gen1', GenerationState.COMPLETED)

        assert {r.id for r in tracker.list_all()} == {'gen1'}

    def test_prune_finished_removes_old_terminal_records(self, tracker, mock_generation_repo):
        tracker.create(id='gen1')
        record = tracker.transition('gen1', GenerationState.COMPLETED)
        record.completed_at = time.time() - 7200  # 2 hours ago

        removed = tracker.prune_finished(max_age_s=3600)

        assert removed == 1
        assert tracker.get('gen1') is None

    def test_prune_finished_keeps_recent_and_active(self, tracker, mock_generation_repo):
        tracker.create(id='gen_active')
        tracker.create(id='gen_recent')
        tracker.transition('gen_recent', GenerationState.COMPLETED)

        removed = tracker.prune_finished(max_age_s=3600)

        assert removed == 0
        assert tracker.get('gen_active') is not None
        assert tracker.get('gen_recent') is not None


class TestRecordSerialization:
    def test_model_dump_shape(self, tracker, mock_generation_repo):
        tracker.create(id='gen1', preset_id='preset_a')
        record = tracker.get('gen1')

        dumped = record.model_dump()

        assert dumped['id'] == 'gen1'
        assert dumped['status'] == 'pending'
        assert dumped['preset_id'] == 'preset_a'
        assert isinstance(dumped['created_at'], str)
