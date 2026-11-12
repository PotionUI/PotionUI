from tests.fixtures.kits import inert_kit_expander
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from src.features.content_safety.errors import BannedPromptRefused
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.presets.fixed_prompt import FixedPrompt
from src.pipelines.outputs import ImageGenerationOutput, ParamGenerationOutput, RenderedPromptGenerationOutput
from tests.features.content_safety.fakes import build

PREFIX = "kodachrome film still,"
SUFFIX = "warm grain"
NEGATIVE = "cartoonish"
SECRETS = ("kodachrome", "warm grain", "cartoonish")
FILM = FixedPrompt(prefix=PREFIX, suffix=SUFFIX, negative=NEGATIVE)


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=passthrough):
        yield


@pytest.fixture(autouse=True)
def repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='u1'))
        yield mock_repo


class FixedPrompts:
    def __init__(self, fixed):
        self.fixed = fixed

    def get(self, preset_id):
        return self.fixed


class AppliedPrompts:
    def __init__(self):
        self.saved = {}

    def save(self, generation_id, applied):
        self.saved[generation_id] = applied

    def get(self, generation_id):
        return self.saved.get(generation_id)


class Collector:
    def __init__(self):
        self.outputs = []

    async def __call__(self, generation_id, output):
        self.outputs.append(output)


def make_orchestrator(fixed=FILM, template_vars=None, content_safety=None, batch=False):
    backend = Mock()
    backend.backend_id = 'b1'
    backend.name = 'Local'
    backend.engine = 'native'
    backend.supports_prompt_batch = batch
    backend.start_generation = AsyncMock()
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    builder = Mock()
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='g', preset_id='p', preset_template=Mock(version='1'), pipes=[],
    ))
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine='native', vars=template_vars or {}))
    processor = Mock()
    processed = []

    async def process_output(generation_id, output, user_id=None):
        processed.append(output)
        return {'handler': 'H', 'processed': True}

    processor.process_output = process_output
    settings = Mock()
    settings.get_setting = Mock(return_value='/out')
    applied = AppliedPrompts()
    orchestrator = GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=Mock(),
        settings=settings,
        output_processor=processor,
        preset_template_loader=loader,
        content_safety=content_safety,
        fixed_prompt_repository=FixedPrompts(fixed),
        applied_prompt_repository=applied, kit_expander=inert_kit_expander(),
    )
    return orchestrator, applied, processed


def make_request(prompts, form_data=None, mode='txt2img', batch=False):
    request = Mock()
    request.preset_id = 'p'
    request.form_data = form_data or {'quantity': 1, 'seed': 5}
    request.prompts = prompts
    request.variables = None
    request.prompt_state = None
    request.mode = mode
    request.tag_ids = None
    request.source_prompt_id = None
    request.segments = None
    request.collection_ids = None
    request.batch_prompts = batch
    return request


async def start(orchestrator, request, generation_id='g1'):
    with patch('src.features.generation.orchestrator.generate_ulid', return_value=generation_id):
        await orchestrator.start_generation(request, 'u1')
    return generation_id


def pipeline_prompts(orchestrator):
    return orchestrator.pipeline_builder.build_pipeline.call_args.kwargs['prompts']


def assert_user_version(payload):
    text = repr(payload)
    for secret in SECRETS:
        assert secret not in text, secret


@pytest.mark.parametrize('negative, applied_negative', [('blurry', f'blurry, {NEGATIVE}'), ('', NEGATIVE)])
async def test_the_pipeline_gets_the_applied_prompts_and_they_are_recorded(negative, applied_negative):
    orchestrator, applied, _ = make_orchestrator()

    await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': negative}]))

    expected = [{'positive': f'{PREFIX} a red fox {SUFFIX}', 'negative': applied_negative}]
    assert pipeline_prompts(orchestrator) == expected
    assert applied.saved['g1'] == expected


async def test_content_safety_checks_the_applied_text():
    manager, _, _ = build('allowed', words=['kodachrome'])
    orchestrator, _, _ = make_orchestrator(content_safety=manager)

    with pytest.raises(BannedPromptRefused) as refused:
        await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}]))

    assert 'kodachrome' not in str(refused.value)
    orchestrator.pipeline_builder.build_pipeline.assert_not_called()


async def test_recorded_parameters_and_rendered_prompts_are_the_users_version():
    orchestrator, _, processed = make_orchestrator()
    generation_id = await start(orchestrator, make_request(
        [{'positive': 'a red fox', 'negative': 'blurry'}], form_data={'quantity': 2, 'seed': 5},
    ))
    applied = pipeline_prompts(orchestrator)
    callback = Collector()

    outputs = [
        ParamGenerationOutput(name='positive_prompt', values=[pair['positive'] for pair in applied]),
        ParamGenerationOutput(name='negative_prompt', values=[pair['negative'] for pair in applied]),
        RenderedPromptGenerationOutput(index=0, positive=applied[0]['positive'], negative=applied[0]['negative']),
        RenderedPromptGenerationOutput(index=1, positive=applied[1]['positive'], negative=applied[1]['negative']),
    ]
    for output in outputs:
        await orchestrator._handle_generation_output(generation_id, output, 'native', callback)

    assert processed[0].values == ['a red fox', 'a red fox']
    assert processed[1].values == ['blurry', 'blurry']
    assert [(o.positive, o.negative) for o in processed[2:]] == [('a red fox', 'blurry')] * 2
    assert_user_version(processed)
    assert_user_version(callback.outputs)


async def test_batch_prompt_text_is_the_users_version():
    orchestrator, _, processed = make_orchestrator(batch=True)
    generation_id = await start(orchestrator, make_request(
        [{'positive': 'a red fox', 'negative': ''}, {'positive': 'a grey wolf', 'negative': ''}],
        form_data={'quantity': 1, 'seed': 5}, batch=True,
    ))
    callback = Collector()

    for seed in (5, 6):
        image = ImageGenerationOutput(image=Image.new('RGB', (2, 2)), temporary=False, seed=seed)
        await orchestrator._handle_generation_output(generation_id, image, 'native', callback)

    assert [output.prompt_text for output in processed] == ['a red fox', 'a grey wolf']
    assert all(PREFIX in pair['positive'] for pair in pipeline_prompts(orchestrator))


async def _complete(orchestrator, generation_id):
    await orchestrator._handle_generation_output(generation_id, None, 'native', Collector())


async def _drop(orchestrator, generation_id):
    orchestrator._forget_run(generation_id)


@pytest.mark.parametrize('finish', [_complete, _drop], ids=['completed', 'dropped'])
async def test_the_run_is_forgotten_when_the_generation_ends(finish):
    orchestrator, _, _ = make_orchestrator()
    generation_id = await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}]))
    assert generation_id in orchestrator._fixed_prompt_runs

    await finish(orchestrator, generation_id)

    assert generation_id not in orchestrator._fixed_prompt_runs


@pytest.mark.parametrize('fixed, form_data, mode, template_vars', [
    (FixedPrompt(), {'quantity': 1, 'seed': 5}, 'txt2img', {}),
    (FixedPrompt(prefix=PREFIX, modes=['edit']), {'quantity': 1, 'seed': 5}, 'txt2img', {}),
    (FILM, {'quantity': 1, 'seed': 5}, 'upscale', {'promptless_modes': ['upscale']}),
], ids=['empty', 'mode not listed', 'promptless mode'])
async def test_skipped_runs_keep_the_users_prompts_and_record_nothing(fixed, form_data, mode, template_vars):
    orchestrator, applied, _ = make_orchestrator(fixed=fixed, template_vars=template_vars)

    await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}], form_data=form_data, mode=mode))

    assert pipeline_prompts(orchestrator) == [{'positive': 'a red fox', 'negative': ''}]
    assert applied.saved == {}
    assert orchestrator._fixed_prompt_runs == {}


@pytest.mark.parametrize('document', [
    {'video_director': {'segments': [{'prompt': 'a red fox'}], 'settings': {'seed': 1}}},
    {'music_director': {'description': 'a red fox', 'sections': [], 'settings': {'seed': 1}}},
], ids=['video director', 'music director'])
async def test_a_director_document_is_left_alone(document):
    orchestrator, applied, _ = make_orchestrator()
    form_data = {'quantity': 1, 'seed': 5, **document}

    with patch('src.features.generation.orchestrator._prepare_director_form_data',
               side_effect=lambda template, mode, form, user, settings, **kwargs: form):
        await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}], form_data=form_data))

    assert_user_version(pipeline_prompts(orchestrator))
    assert_user_version(orchestrator.pipeline_builder.build_pipeline.call_args.kwargs['form_data'])
    assert applied.saved == {}
    assert orchestrator._fixed_prompt_runs == {}


async def test_without_the_repository_nothing_changes():
    orchestrator, _, _ = make_orchestrator()
    orchestrator.fixed_prompt_repository = None

    await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}]))

    assert pipeline_prompts(orchestrator) == [{'positive': 'a red fox', 'negative': ''}]


async def test_a_failing_fixed_prompt_step_fails_the_generation_and_leaves_nothing_behind():
    from src.features.generation.status_tracker import GenerationState

    orchestrator, applied, _ = make_orchestrator()
    orchestrator._prompt_expander.expand_fixed = Mock(side_effect=RuntimeError('expansion broke'))
    prune = Mock(wraps=orchestrator._queue_dispatcher.prune_finished)
    orchestrator._queue_dispatcher.prune_finished = prune

    with pytest.raises(RuntimeError, match='expansion broke'):
        await start(orchestrator, make_request([{'positive': 'a red fox', 'negative': ''}]))

    assert orchestrator.status_tracker.get('g1').state == GenerationState.FAILED
    prune.assert_called()
    orchestrator.pipeline_builder.build_pipeline.assert_not_called()
    assert applied.saved == {}
    assert orchestrator._fixed_prompt_runs == {}
