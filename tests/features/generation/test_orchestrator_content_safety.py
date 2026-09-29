import pytest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from src.features.content_safety.errors import BannedPromptRefused, ContentCheckUnavailable
from src.features.generation.content_blocked_output import ContentBlockedGenerationOutput
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.status_tracker import GenerationState
from src.pipelines.outputs import ErrorGenerationOutput, GalleryGenerationOutput, ImageGenerationOutput
from tests.features.content_safety.fakes import build


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=passthrough):
        yield


@pytest.fixture
def repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='u1'))
        yield mock_repo


@pytest.fixture
def backend():
    backend = Mock()
    backend.backend_id = 'b1'
    backend.name = 'Local'
    backend.engine = 'native'
    backend.start_generation = AsyncMock()
    return backend


def make_orchestrator(backend, manager):
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    builder = Mock()
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='g', preset_id='p', preset_template=Mock(version='1'), pipes=[],
    ))
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine='native'))
    processor = Mock()
    processor.process_output = AsyncMock(return_value={'handler': 'H', 'processed': True})
    settings = Mock()
    settings.get_setting = Mock(return_value='/out')
    return GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=Mock(),
        settings=settings,
        output_processor=processor,
        preset_template_loader=loader,
        content_safety=manager,
    )


def make_request(prompts=None, form_data=None, variables=None):
    request = Mock()
    request.preset_id = 'p'
    request.form_data = form_data or {'quantity': 1, 'seed': 5}
    request.prompts = prompts
    request.variables = variables
    request.prompt_state = None
    request.mode = 'txt2img'
    request.tag_ids = None
    request.source_prompt_id = None
    request.segments = None
    request.collection_ids = None
    return request


async def running(orchestrator, generation_id='g1'):
    orchestrator.status_tracker.create(
        id=generation_id, preset_id='p', backend_id='b1', user_id='u1', tab_id=None,
    )
    await orchestrator.status_tracker.transition_async(generation_id, GenerationState.RUNNING)
    return generation_id


def final_image():
    return ImageGenerationOutput(image=Image.new('RGB', (2, 2)), temporary=False)


class Collector:
    def __init__(self):
        self.outputs = []

    async def __call__(self, generation_id, output):
        self.outputs.append(output)


async def finish(orchestrator, generation_id, callback):
    record = orchestrator.status_tracker.get(generation_id)
    await orchestrator._finish_generation(generation_id, record, callback)
    return orchestrator.status_tracker.get(generation_id)


@pytest.mark.asyncio
async def test_banned_word_produced_by_variable_expansion_is_refused_before_any_backend_call(repo, backend):
    manager, _, _ = build('allowed', words=['forbidden'])
    orchestrator = make_orchestrator(backend, manager)
    request = make_request(
        prompts=[{'positive': 'a ${thing} scene', 'negative': ''}],
        variables={'thing': 'forbidden'},
    )

    with patch('src.features.generation.orchestrator.generate_ulid', return_value='g1'):
        with pytest.raises(BannedPromptRefused) as refused:
            await orchestrator.start_generation(request, 'u1')

    backend.start_generation.assert_not_awaited()
    assert not any(
        'generation_id' in call.kwargs for call in orchestrator.pipeline_builder.build_pipeline.call_args_list
    )
    assert 'forbidden' not in str(refused.value)
    record = orchestrator.status_tracker.get('g1')
    assert record.state == GenerationState.FAILED
    assert record.error_code == 'banned_prompt'


@pytest.mark.asyncio
async def test_every_image_of_a_batch_is_scanned(repo, backend):
    manager, _, _ = build('allowed', words=['forbidden'])
    orchestrator = make_orchestrator(backend, manager)
    request = make_request(
        prompts=[{'positive': '{ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|ok|forbidden}', 'negative': ''}],
        form_data={'quantity': 40, 'seed': 3},
    )

    with patch('src.features.generation.orchestrator.generate_ulid', return_value='g2'):
        with pytest.raises(BannedPromptRefused):
            await orchestrator.start_generation(request, 'u1')

    backend.start_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_document_is_scanned(repo, backend):
    manager, _, _ = build('allowed', words=['forbidden'])
    orchestrator = make_orchestrator(backend, manager)
    request = make_request(
        prompts=[{'positive': 'fine', 'negative': ''}],
        form_data={'quantity': 1, 'seed': 1, 'video_director': {
            'segments': [{'prompt': 'a fine shot'}, {'prompt': 'then a Forbidden one'}],
        }},
    )

    with patch('src.features.generation.orchestrator.generate_ulid', return_value='g3'), \
            patch('src.features.generation.orchestrator._prepare_director_form_data',
                  side_effect=lambda template, mode, form, user, settings: form):
        with pytest.raises(BannedPromptRefused):
            await orchestrator.start_generation(request, 'u1')

    backend.start_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_prompt_reaches_the_backend(repo, backend):
    manager, _, _ = build('allowed', words=['forbidden'])
    orchestrator = make_orchestrator(backend, manager)
    request = make_request(prompts=[{'positive': 'a quiet lake', 'negative': ''}])

    with patch('src.features.generation.orchestrator.generate_ulid', return_value='g4'):
        await orchestrator.start_generation(request, 'u1')

    backend.start_generation.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocked_policy_without_the_tagger_is_refused_before_a_generation_exists(repo, backend):
    manager, _, _ = build('blocked', present=False)
    orchestrator = make_orchestrator(backend, manager)

    with pytest.raises(ContentCheckUnavailable):
        await orchestrator.start_generation(make_request(), 'u1')

    repo.create.assert_not_called()
    backend.start_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_image_never_reaches_the_output_processor(repo, backend):
    manager, _, _ = build('blocked', [0.95])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()

    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)

    orchestrator.output_processor.process_output.assert_not_awaited()
    assert len(callback.outputs) == 1
    notice = callback.outputs[0]
    assert isinstance(notice, ContentBlockedGenerationOutput)
    assert (notice.blocked_count, notice.total) == (1, 1)


@pytest.mark.asyncio
async def test_safe_image_is_processed_and_delivered_with_flags_off(repo, backend):
    manager, _, _ = build('blocked', [0.05])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()
    output = final_image()

    await orchestrator._handle_generation_output(generation_id, output, 'native', callback)

    orchestrator.output_processor.process_output.assert_awaited_once()
    assert callback.outputs == [output]
    assert output._content_flagged is False


@pytest.mark.asyncio
async def test_allowed_user_is_never_rated(repo, backend):
    manager, tagger, _ = build('allowed', [0.99])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()

    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)

    assert tagger.calls == []
    orchestrator.output_processor.process_output.assert_awaited_once()


@pytest.mark.asyncio
async def test_partially_blocked_batch_completes_with_a_notice(repo, backend):
    manager, _, _ = build('blocked', [0.95, 0.05])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()

    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)
    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)
    record = await finish(orchestrator, generation_id, callback)

    assert record.state == GenerationState.COMPLETED
    notices = [o for o in callback.outputs if isinstance(o, ContentBlockedGenerationOutput)]
    assert (notices[-1].blocked_count, notices[-1].total) == (1, 2)
    assert orchestrator.output_processor.process_output.await_count == 1


@pytest.mark.asyncio
async def test_fully_blocked_generation_fails_with_content_blocked(repo, backend):
    manager, _, _ = build('blocked', [0.95, 0.9])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()

    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)
    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)
    record = await finish(orchestrator, generation_id, callback)

    assert record.state == GenerationState.FAILED
    errors = [o for o in callback.outputs if isinstance(o, ErrorGenerationOutput)]
    assert errors and errors[-1].error_code == 'content_blocked'
    orchestrator.output_processor.process_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_tagger_failure_fails_the_generation_and_withholds_the_output(repo, backend):
    manager, _, _ = build('blocked', error=RuntimeError('cuda oom'))
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()

    await orchestrator._handle_generation_output(generation_id, final_image(), 'native', callback)

    assert orchestrator.status_tracker.get(generation_id).state == GenerationState.FAILED
    orchestrator.output_processor.process_output.assert_not_awaited()
    assert callback.outputs[-1].error_code == 'content_check_unavailable'


@pytest.mark.asyncio
async def test_blur_saves_and_flags_a_flagged_image(repo, backend):
    manager, _, _ = build('blur', [0.9])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()
    output = final_image()

    await orchestrator._handle_generation_output(generation_id, output, 'native', callback)

    orchestrator.output_processor.process_output.assert_awaited_once()
    assert callback.outputs == [output]
    assert output._content_flagged is True
    assert output._content_nsfw is True


@pytest.mark.asyncio
async def test_gallery_only_delivers_the_safe_images(repo, backend):
    manager, _, _ = build('blocked', [0.95, 0.05])
    orchestrator = make_orchestrator(backend, manager)
    generation_id = await running(orchestrator)
    callback = Collector()
    keep, drop = final_image(), final_image()
    gallery = GalleryGenerationOutput(images=[drop, keep])

    await orchestrator._handle_generation_output(generation_id, gallery, 'native', callback)

    processed = orchestrator.output_processor.process_output.await_args.args[1]
    assert processed.images == [keep]
