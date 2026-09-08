"""Tests for GenerationOrchestrator routing-decision persistence.

Verifies that a router's `RoutingDecision`, when one is wired, is written to
the newly-created generation record via
`GenerationRepository.update_routing_decision`, mirroring the segment/auto-tag
persistence tests (test_orchestrator_segments.py).
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """See test_orchestrator.py::_bind_form_passthrough."""
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or 'custom', coercions=[], stripped=[])

    with patch('src.features.generation.orchestrator.bind_form', side_effect=_passthrough):
        yield


@pytest.fixture
def mock_pipeline_builder():
    from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
    builder = Mock(spec=PipelineBuilder)
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id='gen_routing_test',
        preset_id='test_preset',
        preset_template=Mock(version='1.0.0'),
        pipes=[{'name': 'generator', 'config': {}}]
    ))
    return builder


@pytest.fixture
def mock_backend():
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    backend.start_generation = AsyncMock()
    backend.cancel_generation = AsyncMock(return_value=True)
    return backend


@pytest.fixture
def mock_backend_registry(mock_backend):
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    registry.select_backend_for_generation = Mock(return_value=mock_backend)
    registry.get_backend = Mock(return_value=mock_backend)
    return registry


@pytest.fixture
def mock_router(mock_backend):
    """A `GenerationRouter` stand-in whose `route()` returns a fixed decision
    naming `mock_backend` as chosen, one dropped candidate, and a rule trace -
    exercising the shape the orchestrator persists."""
    from src.features.generation.routing.contracts import Candidate, RoutingDecision, RuleTraceEntry

    dropped_backend = Mock(backend_id='comfy_1')
    dropped_backend.name = 'Comfy'

    chosen_candidate = Candidate(backend=mock_backend, reasons=['default backend for this engine'])
    dropped_candidate = Candidate(backend=dropped_backend)
    dropped_candidate.drop('missing requirement(s): FaceDetailer node')

    decision = RoutingDecision(
        chosen=mock_backend,
        candidates=[chosen_candidate, dropped_candidate],
        rule_trace=[RuleTraceEntry(rule='enabled_for_engine', before=0, after=2, ms=0.05)],
    )
    router = Mock()
    router.route = AsyncMock(return_value=decision)
    return router


@pytest.fixture
def mock_connection_manager():
    from src.platform.websocket.connection_hub import ConnectionHub
    manager = Mock(spec=ConnectionHub)
    manager.broadcast_to_generation = AsyncMock()
    return manager


@pytest.fixture
def mock_settings():
    from src.platform.settings.settings import Settings
    manager = Mock(spec=Settings)
    manager.get_setting = Mock(return_value='/outputs')
    return manager


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    processor = Mock(spec=OutputProcessor)
    processor.process_output = AsyncMock(return_value={'handler': 'TestHandler', 'processed': True})
    return processor


@pytest.fixture
def mock_preset_template_loader():
    loader = Mock()
    mock_preset = Mock()
    mock_preset.engine = 'native'
    loader.load_preset_by_id = Mock(return_value=mock_preset)
    return loader


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo:
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.update_routing_decision = Mock()
        mock_repo.get_by_id = Mock(return_value=Mock(user_id='user_123'))
        yield mock_repo


@pytest.fixture
def mock_db():
    mock_cursor = Mock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=False)
    db = Mock()
    db.get_cursor = Mock(return_value=mock_cursor)
    with patch('src.platform.database.database.db', db):
        yield db


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
    mock_router,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
        router=mock_router,
    )


@pytest.fixture
def orchestrator_no_router(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
):
    """A router-less orchestrator - the existing, pre-router behaviour."""
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _make_request():
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {'steps': 20}
    request.prompts = None
    request.mode = 'txt2img'
    request.tag_ids = []
    request.segments = None
    return request


class TestRoutingDecisionPersistenceOnStartGeneration:
    @pytest.mark.asyncio
    async def test_routing_decision_persisted_with_correct_shape(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request()

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_routing_1'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_routing_1'
        mock_generation_repo.update_routing_decision.assert_called_once()
        gen_id, trace = mock_generation_repo.update_routing_decision.call_args[0]
        assert gen_id == 'gen_routing_1'
        assert trace['chosen'] == {
            'backend_id': 'local_backend_1',
            'backend_name': 'Local Backend',
            'reason': 'default backend for this engine',
        }
        assert [c['backend_id'] for c in trace['candidates']] == ['local_backend_1', 'comfy_1']
        assert trace['candidates'][1]['dropped'] is True
        assert trace['candidates'][1]['reasons'] == ['missing requirement(s): FaceDetailer node']
        assert trace['rule_trace'] == [
            {'rule': 'enabled_for_engine', 'before': 0, 'after': 2, 'ms': 0.05}
        ]

    @pytest.mark.asyncio
    async def test_no_persistence_without_a_router(
        self, orchestrator_no_router, mock_generation_repo, mock_db
    ):
        request = _make_request()

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_no_router'):
            await orchestrator_no_router.start_generation(request, 'user_123')

        mock_generation_repo.update_routing_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_routing_decision_persistence_failure_is_swallowed(
        self, orchestrator, mock_generation_repo, mock_db
    ):
        request = _make_request()
        mock_generation_repo.update_routing_decision.side_effect = Exception("db error")

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_routing_fail'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_routing_fail'
        assert result['status']['status'] == 'running'
