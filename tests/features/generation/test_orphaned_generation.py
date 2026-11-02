import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.backends.in_process_backend import InProcessBackend
from src.features.generation.dto import GenerationRequest
from src.features.generation.status_tracker import GenerationState
from src.pipelines.outputs import ImageGenerationOutput


class _Backend(InProcessBackend):
    async def health_check(self):
        return {"status": "healthy", "active_generations": len(self._active)}

    async def get_system_info(self):
        return {}


class _Engine:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, pipes, emit, generation_id=None, cache_owner=None):
        self.started.set()
        self.release.wait(10)
        emit(ImageGenerationOutput(image=None, temporary=False))

    def cancel(self, generation_id):
        return False

    def cancel_on_start(self, generation_id):
        pass

    def discard_cancel_on_start(self, generation_id):
        pass


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
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.get_by_id = Mock(return_value=None)
        yield mock_repo


@pytest.fixture
def engine():
    engine = _Engine()
    yield engine
    engine.release.set()


@pytest.fixture
def backend(engine):
    return _Backend(SimpleNamespace(id='native_1', name='Local Generation', engine='native'), engine)


@pytest.fixture
def orchestrator(backend):
    from src.features.backends.backend_registry import BackendRegistry
    from src.features.generation.orchestrator import GenerationOrchestrator
    from src.features.generation.pipeline_builder import BuiltPipeline, PipelineBuilder

    def _build_pipeline(*, generation_id=None, **_kwargs):
        return BuiltPipeline(
            generation_id=generation_id,
            preset_id='p',
            preset_template=Mock(version='1.0.0'),
            pipes=[{'name': 'generator', 'enabled': True, 'config': {}}],
        )

    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(side_effect=_build_pipeline)
    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine='native'))
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


def _request():
    return GenerationRequest(preset_id='p', mode='txt2img', form_data={'seed': 1})


async def _start(orchestrator, generation_id):
    with patch('src.features.generation.orchestrator.generate_ulid', return_value=generation_id):
        return await orchestrator.start_generation(_request(), 'user_1')


async def _settled(orchestrator, generation_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = orchestrator.status_tracker.get(generation_id)
        if record.state.value in ('completed', 'failed', 'cancelled') and generation_id not in (
            orchestrator.queue.running_generation_ids('native_1')
        ):
            return record.state
        await asyncio.sleep(0.02)
    return None


@pytest.mark.asyncio
async def test_a_run_whose_starting_loop_closed_is_cancelled_and_frees_its_backend(
    orchestrator, backend, engine, repo
):
    async def start_then_leave():
        await _start(orchestrator, 'gen_1')
        await asyncio.to_thread(engine.started.wait, 5)

    abandoned = threading.Event()

    def run_and_close():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(start_then_leave())
            left = asyncio.all_tasks(loop)
            for task in left:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
            abandoned.set()
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()

    starter = threading.Thread(target=run_and_close)
    starter.start()
    await asyncio.to_thread(engine.started.wait, 5)
    await asyncio.to_thread(abandoned.wait, 5)
    engine.release.set()
    await asyncio.to_thread(starter.join, 10)

    assert orchestrator.status_tracker.get('gen_1').state == GenerationState.RUNNING
    assert (await backend.health_check())['active_generations'] == 0
    queued = await _start(orchestrator, 'gen_2')
    assert queued['queue_position'] == 0

    assert await orchestrator.cancel_generation('gen_1') is True

    assert orchestrator.status_tracker.get('gen_1').state == GenerationState.CANCELLED
    assert 'gen_1' not in orchestrator.queue.running_generation_ids('native_1')
    assert 'gen_1' not in orchestrator._run_backends
    assert await _settled(orchestrator, 'gen_2') == GenerationState.COMPLETED


@pytest.mark.asyncio
async def test_a_run_still_finishing_on_a_live_loop_is_not_settled_early(
    orchestrator, backend, engine, repo
):
    await _start(orchestrator, 'gen_1')
    await asyncio.to_thread(engine.started.wait, 5)
    backend._active.discard('gen_1')

    assert await orchestrator.cancel_generation('gen_1') is False
    assert orchestrator.status_tracker.get('gen_1').state == GenerationState.RUNNING

    engine.release.set()
    assert await _settled(orchestrator, 'gen_1') == GenerationState.COMPLETED


@pytest.fixture
def app_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_a_timed_out_smoke_on_the_app_loop_leaves_its_backend_free(
    orchestrator, backend, engine, repo, app_loop, monkeypatch
):
    import src.features.recipes.executors.generation_smoke as generation_smoke
    from src.features.recipes.executors.base import StepContext
    from src.features.recipes.executors.generation_smoke import GenerationSmokeExecutor
    from src.features.recipes.records import RecipeRun, RecipeRunStatus
    from src.features.recipes.schema import Recipe, RecipeSmokeRef, RecipeStep

    monkeypatch.setattr(generation_smoke, "build_fixture_form_data", lambda *a, **k: {"seed": 1})
    recipe = Recipe(
        id="x",
        schema_version=1,
        version=1,
        name="X",
        engine="native",
        smoke=RecipeSmokeRef(preset_id="p", mode="txt2img", timeout_seconds=1),
        steps=[RecipeStep(key="generation.smoke", kind="generation.smoke", title="Smoke", params={"preset_id": "p", "mode": "txt2img"})],
    )
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING, created_by="owner-1")
    template = SimpleNamespace(name="Video", modes={"txt2img": object()})
    executor = GenerationSmokeExecutor(
        SimpleNamespace(load_preset_by_id=lambda preset_id: template),
        object(),
        orchestrator,
        SimpleNamespace(get_generation_files=lambda *a, **k: []),
        app_loop=lambda: app_loop,
    )

    with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_smoke'):
        result = executor.execute(StepContext(run=run, recipe=recipe, step=recipe.steps[0]))
    engine.release.set()

    assert result.success is False
    assert "timed out waiting for completion after 1s" in result.safe_error_detail
    assert asyncio.run_coroutine_threadsafe(
        _settled(orchestrator, 'gen_smoke'), app_loop
    ).result(10) == GenerationState.CANCELLED
    later = asyncio.run_coroutine_threadsafe(_start(orchestrator, 'gen_next'), app_loop).result(10)
    assert later['queue_position'] is None
    assert asyncio.run_coroutine_threadsafe(
        _settled(orchestrator, 'gen_next'), app_loop
    ).result(10) == GenerationState.COMPLETED
