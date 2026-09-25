from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.generation.hooks import GENERATION_HOOKS
from src.features.generation.status_tracker import GenerationState
from src.pipelines.outputs import ErrorGenerationOutput


@pytest.fixture
def generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as repo, \
         patch('src.features.generation.status_tracker.generation_repo', repo):
        repo.get_by_id = Mock(return_value=Mock(user_id='user_1', preset_id='sdxl/base'))
        yield repo


@pytest.fixture
def plugin_registry():
    registry = Mock()
    registry.execute_hook = Mock(side_effect=lambda name, context=None, **_: (context, True))
    return registry


@pytest.fixture
def orchestrator(plugin_registry, generation_repo):
    from src.features.generation.orchestrator import GenerationOrchestrator
    processor = Mock()
    processor.process_output = AsyncMock(return_value={'handler': 'H', 'processed': True})
    backend_registry = Mock()
    backend_registry.get_backend = Mock(return_value=None)
    instance = GenerationOrchestrator(
        pipeline_builder=Mock(),
        backend_registry=backend_registry,
        connection_hub=Mock(),
        settings=Mock(),
        output_processor=processor,
        preset_template_loader=Mock(),
        plugin_registry=plugin_registry,
    )
    with patch('src.platform.plugins.runtime_registries.get_global_notification_manager', return_value=Mock()):
        yield instance


def _failed_calls(plugin_registry):
    return [c for c in plugin_registry.execute_hook.call_args_list if c.args[0] == GENERATION_HOOKS.failed]


def _running(orchestrator, generation_id):
    orchestrator.status_tracker.create(id=generation_id, preset_id='sdxl/base', user_id='user_1')
    orchestrator.status_tracker.transition(generation_id, GenerationState.RUNNING)


def test_hook_is_declared_in_the_catalog_with_its_payload():
    from src.platform.plugins.hooks import hooks_registry
    spec = next(s for s in hooks_registry.all() if s.name == 'generation.failed')
    assert spec.type == 'backend'
    assert set(spec.payload) == {
        'generation_id', 'user_id', 'preset_id', 'error_code', 'category', 'message', 'failed_pipe',
    }


@pytest.mark.asyncio
async def test_error_output_fires_the_hook_once_with_the_exact_payload(orchestrator, plugin_registry):
    _running(orchestrator, 'gen_1')
    output = ErrorGenerationOutput(
        error='KSampler: CUDA out of memory', pipe_id=3, pipe_name='generator', pipe_key='sampler',
    )

    await orchestrator._handle_generation_output('gen_1', output, 'native', None)

    calls = _failed_calls(plugin_registry)
    assert len(calls) == 1
    assert calls[0].args[1].data == {
        'generation_id': 'gen_1',
        'user_id': 'user_1',
        'preset_id': 'sdxl/base',
        'error_code': 'cuda_oom',
        'category': 'cuda_oom',
        'message': 'Ran out of GPU memory (VRAM) during generation.',
        'failed_pipe': 'sampler',
    }


@pytest.mark.asyncio
async def test_a_second_error_output_does_not_fire_again(orchestrator, plugin_registry):
    _running(orchestrator, 'gen_2')

    await orchestrator._handle_generation_output('gen_2', ErrorGenerationOutput(error='boom'), 'native', None)
    await orchestrator._handle_generation_output('gen_2', ErrorGenerationOutput(error='boom again'), 'native', None)
    await orchestrator._handle_generation_output('gen_2', None, 'native', None)

    assert len(_failed_calls(plugin_registry)) == 1


@pytest.mark.asyncio
async def test_error_after_cancel_does_not_fire(orchestrator, plugin_registry):
    _running(orchestrator, 'gen_3')
    orchestrator.status_tracker.transition('gen_3', GenerationState.CANCELLED)

    await orchestrator._handle_generation_output('gen_3', ErrorGenerationOutput(error='cancelled'), 'native', None)
    await orchestrator._handle_generation_output('gen_3', None, 'native', None)

    assert _failed_calls(plugin_registry) == []


@pytest.mark.asyncio
async def test_successful_completion_does_not_fire(orchestrator, plugin_registry):
    _running(orchestrator, 'gen_4')

    await orchestrator._handle_generation_output('gen_4', None, 'native', None)

    assert orchestrator.status_tracker.get('gen_4').state == GenerationState.COMPLETED
    assert _failed_calls(plugin_registry) == []
    assert any(c.args[0] == GENERATION_HOOKS.after_complete for c in plugin_registry.execute_hook.call_args_list)


@pytest.mark.asyncio
async def test_failed_final_save_fires_once(orchestrator, plugin_registry):
    from types import SimpleNamespace
    _running(orchestrator, 'gen_5')
    orchestrator.output_processor.process_output = AsyncMock(
        return_value={'handler': 'H', 'processed': False, 'save_error': 'No space left on device'}
    )
    with patch.object(type(orchestrator), '_final_save_error', return_value='No space left on device'):
        await orchestrator._handle_generation_output('gen_5', SimpleNamespace(pipe_id=None, pipe_name=None), 'native', None)

    calls = _failed_calls(plugin_registry)
    assert len(calls) == 1
    assert calls[0].args[1].data['category'] == 'disk_full'


@pytest.mark.asyncio
async def test_a_raising_hook_does_not_break_failure_handling(orchestrator, plugin_registry):
    plugin_registry.execute_hook.side_effect = RuntimeError('plugin exploded')
    _running(orchestrator, 'gen_6')

    await orchestrator._handle_generation_output('gen_6', ErrorGenerationOutput(error='boom'), 'native', None)

    assert orchestrator.status_tracker.get('gen_6').state == GenerationState.FAILED
