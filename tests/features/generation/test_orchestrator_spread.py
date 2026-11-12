import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.backends.backend_registry import BackendRegistry
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder
from src.features.generation.routing.registry import BUILTIN_RULES
from src.features.generation.routing.router import GenerationRouter
from src.features.generation.status_tracker import GenerationState
from tests.fixtures.kits import inert_kit_expander


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo), \
         patch('src.features.generation.backend_balancer.generation_repo', mock_repo):
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock(return_value=None)
        yield mock_repo


def _backend(backend_id, capacity=1, priority=1):
    backend = Mock()
    backend.backend_id = backend_id
    backend.name = backend_id
    backend.engine = 'native'
    backend.max_concurrent_runs = capacity
    backend.config.priority = priority
    backend.config.effective_driver = 'native'
    backend.execution_device = 'this_host_gpu'
    backend.start_generation = AsyncMock()
    backend.cancel_generation = AsyncMock(return_value=True)
    backend.take_cancel_notice = Mock(return_value=None)
    return backend


class Setup:
    def __init__(self, capacities):
        self.backends = {f'b{i}': _backend(f'b{i}', c, 10 - i) for i, c in enumerate(capacities)}
        registry = object.__new__(BackendRegistry)
        registry.get_backends_for_engine = Mock(side_effect=lambda engine: list(self.backends.values()))
        registry.get_backend = Mock(side_effect=lambda bid: self.backends.get(bid))
        registry._backends_cache = self.backends
        registry.backend_config_store = Mock()
        registry.backend_config_store.get_default_backend.return_value = None

        builder = Mock(spec=PipelineBuilder)
        builder.build_pipeline = Mock(side_effect=lambda **kw: BuiltPipeline(
            generation_id='x', preset_id='p', preset_template=Mock(version='1.0.0'),
            pipes=[{'name': 'generator', 'enabled': True, 'config': {}}],
        ))
        loader = Mock()
        loader.load_preset_by_id = Mock(return_value=Mock(engine='native', requirements=[], driver=None))
        processor = Mock()
        processor.process_output = AsyncMock(return_value={})

        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=builder, backend_registry=registry, connection_hub=Mock(), settings=Mock(),
            output_processor=processor, preset_template_loader=loader, kit_expander=inert_kit_expander(),
        )
        router = GenerationRouter(list(BUILTIN_RULES), backend_registry=registry)
        router.bind_load(self.orchestrator.load_ledger)
        router.bind_health(self.orchestrator.backend_health)
        self.orchestrator.router = router
        self.orchestrator.limit_guard = Mock(admit=lambda admission: time.sleep(0.02), refund=Mock())

    def request(self, tab):
        r = Mock()
        r.preset_id = 'p'
        r.form_data = {'steps': 20}
        r.prompt = 'x'
        r.negative_prompt = ''
        r.prompts = None
        r.prompt_state = None
        r.mode = 'txt2img'
        r.backend_id = None
        r.tag_ids = None
        r.segments = None
        r.tab_id = tab
        return r

    async def start(self, tab, gen_id):
        with patch('src.features.generation.orchestrator.generate_ulid', return_value=gen_id):
            return await self.orchestrator.start_generation(self.request(tab), 'user_1')

    async def start_many(self, count):
        ids = iter(f'g{n}' for n in range(count))
        with patch('src.features.generation.orchestrator.generate_ulid', side_effect=lambda: next(ids)):
            return await asyncio.gather(*(
                self.orchestrator.start_generation(self.request(f't{n}'), 'user_1') for n in range(count)
            ))

    def backend_of(self, gen_id):
        return self.orchestrator.status_tracker.get(gen_id).backend_id


@pytest.mark.asyncio
async def test_two_jobs_submitted_at_once_land_on_two_backends_and_both_start(repo):
    setup = Setup([1, 1])

    first, second = await setup.start_many(2)

    assert {setup.backend_of('g0'), setup.backend_of('g1')} == {'b0', 'b1'}
    assert first['status']['status'] == 'running' and second['status']['status'] == 'running'
    assert all(b.start_generation.await_count == 1 for b in setup.backends.values())
    assert setup.orchestrator.load_ledger.load('b0').reserved == 0
    assert setup.orchestrator.load_ledger.load('b1').reserved == 0


@pytest.mark.asyncio
async def test_many_jobs_at_once_fill_every_slot_before_anything_queues(repo):
    setup = Setup([2, 1])

    results = await setup.start_many(3)

    assert [r['queue_position'] for r in results] == [None, None, None]
    assert sorted(setup.backend_of(f'g{n}') for n in range(3)) == ['b0', 'b0', 'b1']


@pytest.mark.asyncio
async def test_the_queue_position_is_the_place_in_the_chosen_backends_own_queue(repo):
    setup = Setup([1, 1])
    await setup.start('t0', 'g0')
    await setup.start('t1', 'g1')

    third = await setup.start('t2', 'g2')
    fourth = await setup.start('t3', 'g3')
    fifth = await setup.start('t4', 'g4')

    assert [third['queue_position'], fourth['queue_position'], fifth['queue_position']] == [0, 0, 1]
    assert setup.backend_of('g2') != setup.backend_of('g3')
    assert setup.backend_of('g4') == setup.backend_of('g2')


@pytest.mark.asyncio
async def test_a_failed_submit_gives_its_reservation_back(repo):
    setup = Setup([1, 1])
    setup.orchestrator.pipeline_builder.build_pipeline = Mock(side_effect=RuntimeError('bad pipeline'))

    with pytest.raises(RuntimeError):
        await setup.start('t0', 'g0')

    assert setup.orchestrator.load_ledger.load('b0').reserved == 0
    assert setup.orchestrator.load_ledger.load('b1').reserved == 0


@pytest.mark.asyncio
async def test_a_down_backend_is_not_chosen_and_the_job_runs_on_the_other(repo):
    setup = Setup([1, 1])
    setup.orchestrator.backend_health.record('b0', False, 'connection refused')

    result = await setup.start('t0', 'g0')

    assert setup.backend_of('g0') == 'b1'
    assert result['status']['status'] == 'running'


@pytest.mark.asyncio
async def test_when_every_backend_is_down_the_job_waits_instead_of_failing(repo):
    setup = Setup([1])
    setup.orchestrator.backend_health.record('b0', False, 'connection refused')

    result = await setup.start('t0', 'g0')
    info = setup.orchestrator.get_queue_info('g0')

    assert result['status']['status'] == 'pending'
    assert result['queue_position'] == 0
    assert info['waiting_reason']
    assert setup.backends['b0'].start_generation.await_count == 0

    setup.orchestrator.backend_health.record('b0', True)
    await setup.orchestrator._balancer._after_health_change('b0', True)

    assert setup.backends['b0'].start_generation.await_count == 1
    assert setup.orchestrator.status_tracker.get('g0').state == GenerationState.RUNNING


@pytest.mark.asyncio
async def test_a_job_moves_to_the_other_backend_when_its_backend_goes_down_while_it_waits(repo):
    setup = Setup([1, 1])
    await setup.start('t0', 'g0')
    await setup.start('t1', 'g1')
    third = await setup.start('t2', 'g2')
    waiting_on = setup.backend_of('g2')
    other = 'b1' if waiting_on == 'b0' else 'b0'
    assert third['status']['status'] == 'pending'

    setup.orchestrator.backend_health.record(waiting_on, False, 'down')
    await setup.orchestrator._balancer.rebalance(waiting_on, gone=False)
    await setup.orchestrator._handle_generation_completion(
        next(g for g in ('g0', 'g1') if setup.backend_of(g) == other), None
    )

    assert setup.backend_of('g2') == other
    assert setup.orchestrator.status_tracker.get('g2').state == GenerationState.RUNNING


@pytest.mark.asyncio
async def test_the_backend_snapshot_reports_queued_and_running(repo):
    setup = Setup([1, 1])
    for n in range(3):
        await setup.start(f't{n}', f'g{n}')

    snapshots = {b: setup.orchestrator.backend_load_snapshot(b) for b in ('b0', 'b1')}

    assert sum(s['running'] for s in snapshots.values()) == 2
    assert sum(s['queued'] for s in snapshots.values()) == 1
    assert all(s['max_concurrent_runs'] == 1 and s['unhealthy_reason'] is None for s in snapshots.values())


@pytest.mark.asyncio
async def test_the_queued_item_remembers_how_it_was_routed_so_it_can_be_moved(repo):
    setup = Setup([1])
    await setup.start('t0', 'g0')
    await setup.start('t1', 'g1')

    item = setup.orchestrator.queue.pending_items()[0]

    assert item.payload['routing'].engine == 'native'
    assert item.payload['routing'].user_id == 'user_1'


@pytest.mark.asyncio
async def test_a_backend_that_fails_to_start_gives_its_reservation_back(repo):
    setup = Setup([1, 1])
    for backend in setup.backends.values():
        backend.start_generation = AsyncMock(side_effect=RuntimeError('worker refused'))

    with pytest.raises(RuntimeError):
        await setup.start('t0', 'g0')

    assert setup.orchestrator.load_ledger.load('b0').reserved == 0
    assert setup.orchestrator.load_ledger.load('b1').reserved == 0


@pytest.mark.asyncio
async def test_the_probe_loop_starts_and_stops_with_the_orchestrator(repo):
    setup = Setup([1])

    setup.orchestrator.start_backend_probes()
    assert setup.orchestrator._balancer._sweeper is not None
    await setup.orchestrator.stop_backend_probes()

    assert setup.orchestrator._balancer._sweeper is None
