"""
Queue behaviour through the real orchestrator path.

`test_queue.py` pins the queue data structure. These tests pin the thing the
user actually asked for: two tabs can both enqueue, the second waits for the
backend rather than corrupting the first, and completing one starts the next.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.features.generation.status_tracker import GenerationState


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """See tests/core/generation/test_orchestrator.py::_bind_form_passthrough."""
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def backends():
    """A native backend and a comfy backend, each idle."""
    def _backend(backend_id, engine):
        b = Mock()
        b.backend_id = backend_id
        b.name = backend_id
        b.engine = engine
        b.start_generation = AsyncMock()
        b.cancel_generation = AsyncMock(return_value=True)
        return b

    return {
        'native': _backend('native_1', 'native'),
        'comfy': _backend('comfy_1', 'comfyui'),
    }


@pytest.fixture
def orchestrator(backends):
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.generation.orchestrator import GenerationOrchestrator
    from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder

    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='x',
        preset_id='p',
        preset_template=Mock(version='1.0.0'),
        pipes=[{'name': 'generator', 'config': {}}],
    ))

    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(
        side_effect=lambda engine, **kw: backends['native'] if engine == 'native' else backends['comfy']
    )
    registry.get_backend = Mock(side_effect=lambda bid: next(
        (b for b in backends.values() if b.backend_id == bid), None
    ))

    loader = Mock()
    preset = Mock()
    preset.engine = 'native'
    loader.load_preset_by_id = Mock(return_value=preset)

    processor = Mock()
    processor.process_output = AsyncMock(return_value={'processed': True})

    return GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=Mock(),
        settings=Mock(),
        output_processor=processor,
        preset_template_loader=loader,
    )


def _request(tab_id, preset_id='p'):
    r = Mock()
    r.preset_id = preset_id
    r.form_data = {'steps': 20}
    r.prompt = 'x'
    r.negative_prompt = ''
    r.prompts = None
    r.prompt_state = None
    r.mode = 'txt2img'
    r.backend_id = None
    r.tag_ids = None
    r.segments = None
    r.tab_id = tab_id
    return r


@pytest.fixture
def repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock(return_value=None)
        yield mock_repo


async def _start(orchestrator, tab, gen_id):
    with patch('src.features.generation.orchestrator.generate_ulid', return_value=gen_id):
        return await orchestrator.start_generation(_request(tab), 'user_1')


@pytest.mark.asyncio
class TestQueueingThroughTheOrchestrator:
    async def test_a_second_generation_on_a_busy_backend_waits(self, orchestrator, backends, repo):
        first = await _start(orchestrator, 'tab_a', 'gen_1')
        second = await _start(orchestrator, 'tab_b', 'gen_2')

        assert first['status']['status'] == 'running'
        assert first['queue_position'] is None

        assert second['status']['status'] == 'pending'
        assert second['queue_position'] == 0

        # The crux: the busy backend was only ever driven once.
        assert backends['native'].start_generation.await_count == 1

    async def test_completing_the_first_dispatches_the_second(self, orchestrator, backends, repo):
        await _start(orchestrator, 'tab_a', 'gen_1')
        await _start(orchestrator, 'tab_b', 'gen_2')

        await orchestrator._handle_generation_completion('gen_1', None)

        assert backends['native'].start_generation.await_count == 2
        assert orchestrator.status_tracker.get('gen_2').state == GenerationState.RUNNING
        assert orchestrator.queue.position('gen_2') is None

    async def test_different_backends_run_in_parallel(self, orchestrator, backends, repo):
        native_preset, comfy_preset = Mock(engine='native'), Mock(engine='comfyui')
        orchestrator.preset_template_loader.load_preset_by_id = Mock(
            side_effect=lambda pid: native_preset if pid == 'p' else comfy_preset
        )

        await _start(orchestrator, 'tab_a', 'gen_1')
        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_2'):
            second = await orchestrator.start_generation(_request('tab_b', preset_id='comfy'), 'user_1')

        assert second['queue_position'] is None, "a different backend must not queue behind native"
        assert backends['native'].start_generation.await_count == 1
        assert backends['comfy'].start_generation.await_count == 1

    async def test_cancelling_a_queued_generation_never_reaches_the_backend(
        self, orchestrator, backends, repo
    ):
        await _start(orchestrator, 'tab_a', 'gen_1')
        await _start(orchestrator, 'tab_b', 'gen_2')

        assert await orchestrator.cancel_generation('gen_2') is True

        # It never ran, so the backend must not be asked to cancel it - that is
        # what used to abort the *other* tab's generation.
        backends['native'].cancel_generation.assert_not_awaited()
        assert orchestrator.status_tracker.get('gen_2').state == GenerationState.CANCELLED
        assert orchestrator.queue.position('gen_2') is None

    async def test_cancelling_the_running_generation_goes_to_the_backend(
        self, orchestrator, backends, repo
    ):
        await _start(orchestrator, 'tab_a', 'gen_1')

        assert await orchestrator.cancel_generation('gen_1') is True

        backends['native'].cancel_generation.assert_awaited_once_with('gen_1')

    async def test_a_cancelled_queued_generation_is_skipped_on_dispatch(
        self, orchestrator, backends, repo
    ):
        await _start(orchestrator, 'tab_a', 'gen_1')
        await _start(orchestrator, 'tab_b', 'gen_2')
        await _start(orchestrator, 'tab_b', 'gen_3')

        await orchestrator.cancel_generation('gen_2')
        await orchestrator._handle_generation_completion('gen_1', None)

        # gen_3 runs, gen_2 stays dead.
        assert orchestrator.status_tracker.get('gen_3').state == GenerationState.RUNNING
        assert orchestrator.status_tracker.get('gen_2').state == GenerationState.CANCELLED

    async def test_clear_tab_queue_drops_only_that_tabs_pending_work(
        self, orchestrator, backends, repo
    ):
        await _start(orchestrator, 'tab_a', 'gen_1')  # runs
        await _start(orchestrator, 'tab_b', 'gen_2')  # pending
        await _start(orchestrator, 'tab_a', 'gen_3')  # pending
        await _start(orchestrator, 'tab_b', 'gen_4')  # pending

        cleared = await orchestrator.clear_tab_queue('user_1', 'tab_b')

        assert sorted(cleared) == ['gen_2', 'gen_4']
        assert orchestrator.status_tracker.get('gen_3').state == GenerationState.PENDING
        assert orchestrator.status_tracker.get('gen_1').state == GenerationState.RUNNING

    async def test_clear_tab_queue_leaves_the_running_generation_alone(
        self, orchestrator, backends, repo
    ):
        await _start(orchestrator, 'tab_a', 'gen_1')

        cleared = await orchestrator.clear_tab_queue('user_1', 'tab_a')

        assert cleared == []
        assert orchestrator.status_tracker.get('gen_1').state == GenerationState.RUNNING

    async def test_tab_id_is_persisted_on_the_generation_row(self, orchestrator, repo):
        await _start(orchestrator, 'tab_a', 'gen_1')

        created = repo.create.call_args[0][0]
        assert created.tab_id == 'tab_a'

    async def test_queue_snapshot_is_scoped_to_the_calling_user(self, orchestrator, repo):
        await _start(orchestrator, 'tab_a', 'gen_1')  # user_1, runs
        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_2'):
            await orchestrator.start_generation(_request('tab_x'), 'user_2')  # queued

        mine = orchestrator.get_queue_snapshot('user_1')
        theirs = orchestrator.get_queue_snapshot('user_2')

        assert [r['generation_id'] for r in mine['running']] == ['gen_1']
        assert mine['running'][0]['tab_id'] == 'tab_a'
        assert mine['pending'] == [], "user_1 must not see user_2's queued generation"
        assert [p['generation_id'] for p in theirs['pending']] == ['gen_2']

    async def test_queue_update_is_published_for_pending_and_running(self, orchestrator, repo):
        seen = []
        orchestrator.set_queue_listener(AsyncMock(side_effect=lambda gid, msg: seen.append(msg)))

        await _start(orchestrator, 'tab_a', 'gen_1')
        await _start(orchestrator, 'tab_b', 'gen_2')

        running = [m for m in seen if m['status'] == 'running']
        pending = [m for m in seen if m['status'] == 'pending']

        assert running[0]['generation_id'] == 'gen_1'
        assert running[0]['queue_position'] is None
        assert pending[-1] == {
            'type': 'queue_update',
            'generation_id': 'gen_2',
            'tab_id': 'tab_b',
            'status': 'pending',
            'queue_position': 0,
        }

    async def test_started_at_is_only_set_on_dispatch(self, orchestrator, repo):
        await _start(orchestrator, 'tab_a', 'gen_1')
        await _start(orchestrator, 'tab_b', 'gen_2')

        # gen_2 is still queued: it has not started, so it has no start time and
        # its eventual duration must not include the wait.
        assert orchestrator.status_tracker.get('gen_1').started_at is not None
        assert orchestrator.status_tracker.get('gen_2').started_at is None
