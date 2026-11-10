import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.generation.applied_prompt_repository import GenerationAppliedPromptRepository
from src.features.generation.history_archive import GenerationHistoryArchive
from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.repository import GenerationRepository
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.generation.run_report_repository import GenerationRunReportRepository
from src.features.presets.fixed_prompt import FixedPrompt
from src.features.presets.fixed_prompt_repository import PresetFixedPromptRepository
from src.pipelines.outputs import ParamGenerationOutput, ProgressGenerationOutput, RenderedPromptGenerationOutput
from src.platform.websocket.connection_hub import ConnectionHub
from tests.fixtures.seed import seed_user

SECRETS = ("kodachrome", "warm grain", "cartoonish")
FILM = FixedPrompt(prefix="kodachrome still,", suffix="warm grain", negative="cartoonish")


class _Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class _Names:
    preset_loader = None

    def resolve(self, preset_id, default=None):
        return preset_id


def assert_user_version(payload):
    text = json.dumps(payload, default=str)
    for secret in SECRETS:
        assert secret not in text, secret


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=passthrough):
        yield


@pytest.fixture
def finished_run(mock_db):
    seed_user(mock_db, 'u1')
    with mock_db.get_cursor() as cursor:
        cursor.execute("INSERT INTO presets (id, preset_id) VALUES ('row-1', 'film')")
    PresetFixedPromptRepository().set('film', FILM)

    backend = Mock(backend_id='b1', engine='native', supports_prompt_batch=False)
    backend.name = 'Local'
    backend.start_generation = AsyncMock()
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    builder = Mock()
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='g1', preset_id='film', preset_template=Mock(version='1'), pipes=[],
    ))
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine='native', vars={}))
    settings = Mock()
    settings.get_setting = Mock(return_value='/out')
    orchestrator = GenerationOrchestrator(
        pipeline_builder=builder,
        backend_registry=registry,
        connection_hub=Mock(),
        settings=settings,
        output_processor=OutputProcessor(Mock()),
        preset_template_loader=loader,
        fixed_prompt_repository=PresetFixedPromptRepository(),
        applied_prompt_repository=GenerationAppliedPromptRepository(),
    )
    hub = ConnectionHub()
    broadcaster = GenerationOutputBroadcaster(
        hub, orchestrator.status_tracker, RunReportRecorder(GenerationRunReportRepository()),
    )
    socket = _Socket()
    request = SimpleNamespace(
        preset_id='film', form_data={'prompt': 'a red fox', 'quantity': 2, 'seed': 5},
        prompts=[{'positive': 'a red fox', 'negative': 'blurry'}], variables=None, prompt_state=None,
        mode='txt2img', tag_ids=None, source_prompt_id=None, segments=None, collection_ids=None,
        batch_prompts=False, form_name=None, tab_id=None, idempotency_key=None, grid_id=None,
    )

    async def run():
        await hub.connect(socket, 'owner-client')
        with patch('src.features.generation.orchestrator.generate_ulid', return_value='g1'):
            await orchestrator.start_generation(request, 'u1', output_callback=broadcaster.handle_output)
        await hub.subscribe_to_generation('owner-client', 'g1')
        applied = builder.build_pipeline.call_args.kwargs['prompts']
        outputs = [
            ProgressGenerationOutput(state='Resolving prompts'),
            ParamGenerationOutput(name='positive_prompt', values=[pair['positive'] for pair in applied]),
            ParamGenerationOutput(name='negative_prompt', values=[pair['negative'] for pair in applied]),
            ParamGenerationOutput(name='steps', values=[30, 30]),
            *[
                RenderedPromptGenerationOutput(index=i, positive=pair['positive'], negative=pair['negative'])
                for i, pair in enumerate(applied)
            ],
        ]
        for output in outputs:
            await orchestrator._handle_generation_output('g1', output, 'native', broadcaster.handle_output)
        await orchestrator._handle_generation_output('g1', None, 'native', broadcaster.handle_output)
        return applied

    applied = asyncio.run(run())
    return SimpleNamespace(db=mock_db, applied=applied, socket=socket)


def test_the_pipeline_ran_with_the_fixed_text(finished_run):
    assert all('kodachrome still,' in pair['positive'] for pair in finished_run.applied)
    assert all(pair['negative'] == 'blurry, cartoonish' for pair in finished_run.applied)


def test_no_user_readable_table_holds_the_fixed_text(finished_run):
    leaks = []
    with finished_run.db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = [row[0] for row in cursor.fetchall() if row[0] not in ('generation_applied_prompts', 'presets')]
        for table in tables:
            cursor.execute(f'SELECT * FROM "{table}"')
            for row in cursor.fetchall():
                text = json.dumps([str(value) for value in tuple(row)])
                leaks.extend(f'{table}: {secret}' for secret in SECRETS if secret in text)

    assert leaks == []
    assert GenerationAppliedPromptRepository().get('g1')[0]['positive'] == finished_run.applied[0]['positive']


def test_the_live_socket_never_carries_the_fixed_text(finished_run):
    assert finished_run.socket.sent
    assert_user_version(finished_run.socket.sent)


def test_history_details_and_parameters_are_the_users_version(finished_run):
    query = GenerationHistoryQuery(generation_repo=GenerationRepository(), preset_name_resolver=_Names())

    details = query.get_by_id('g1', 'u1', include_files=False)
    by_index = [query.get_params('g1', index, 'u1') for index in (0, 1)]

    assert details['parameters']['positive_prompt'] == ['a red fox', 'a red fox']
    assert details['parameters']['negative_prompt'] == ['blurry', 'blurry']
    assert_user_version(details)
    assert_user_version(by_index)


def test_the_export_bundle_is_the_users_version(finished_run):
    repository = GenerationRepository()
    archive = GenerationHistoryArchive(
        repository, Mock(), Mock(),
        GenerationHistoryQuery(generation_repo=repository, preset_name_resolver=_Names()), Mock(),
    )

    envelope = archive._build_bundle_envelope(repository.get_by_id('g1', user_id='u1'), 'u1')

    assert_user_version(envelope)
    assert envelope['generation']['parameters'][0]['positive_prompt'] == 'a red fox'
