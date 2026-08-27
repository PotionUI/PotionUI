"""Tests for GenerationOrchestrator's provenance-link submission path.

`_parse_generation_origins`/`_validate_generation_origins` (module-level pure
functions, see orchestrator.py) parse and validate `<field>__origin` sibling
keys - the marker a standalone "enhance" run (e.g. Krea-2's Whole-Frame
Enhance) puts on the wire to say a media field was seeded from a prior
generation's output rather than a bare upload. These tests cover the parsing/
validation functions directly, then the wiring into `start_generation`: a
malformed or foreign reference must reject BEFORE the generation record is
persisted, and a valid link must persist a `generation_sources` row once the
record exists. Same fixture shape as
tests/features/generation/test_orchestrator_ltx_geometry.py.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.generation.orchestrator import (
    _parse_generation_origins,
    _validate_generation_origins,
)
from src.features.generation.exceptions import InvalidGenerationSourceException


class TestParseGenerationOrigins:
    def test_no_origin_keys_returns_empty(self):
        assert _parse_generation_origins({"source_image": "upload.png"}) == []

    def test_well_formed_origin_is_parsed(self):
        form_data = {
            "source_image": "upload.png",
            "source_image__origin": {"generation_id": "gen_1", "file_index": 2},
        }
        assert _parse_generation_origins(form_data) == [{
            "field_name": "source_image",
            "source_generation_id": "gen_1",
            "source_file_index": 2,
        }]

    def test_multiple_origin_keys_are_all_parsed(self):
        form_data = {
            "source_image": "a.png",
            "source_image__origin": {"generation_id": "gen_1", "file_index": 0},
            "reference_image": "b.png",
            "reference_image__origin": {"generation_id": "gen_2", "file_index": 1},
        }
        field_names = {o["field_name"] for o in _parse_generation_origins(form_data)}
        assert field_names == {"source_image", "reference_image"}

    def test_non_dict_origin_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins({"source_image": "x", "source_image__origin": "not-a-dict"})

    def test_missing_generation_id_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins({"source_image": "x", "source_image__origin": {"file_index": 0}})

    def test_non_string_generation_id_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image": "x", "source_image__origin": {"generation_id": 123, "file_index": 0}}
            )

    def test_negative_file_index_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image": "x", "source_image__origin": {"generation_id": "gen_1", "file_index": -1}}
            )

    def test_bool_file_index_raises(self):
        """`isinstance(True, int)` is True in Python - explicitly excluded."""
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image": "x", "source_image__origin": {"generation_id": "gen_1", "file_index": True}}
            )

    def test_missing_file_index_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image": "x", "source_image__origin": {"generation_id": "gen_1"}}
            )

    def test_origin_without_a_field_value_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image": "", "source_image__origin": {"generation_id": "gen_1", "file_index": 0}}
            )

    def test_origin_with_field_absent_entirely_raises(self):
        with pytest.raises(ValueError):
            _parse_generation_origins(
                {"source_image__origin": {"generation_id": "gen_1", "file_index": 0}}
            )


class TestValidateGenerationOrigins:
    def test_existing_owned_source_passes(self):
        repo = Mock()
        repo.get_by_id = Mock(return_value=Mock())
        origins = [{"field_name": "source_image", "source_generation_id": "gen_1", "source_file_index": 0}]

        with patch('src.features.generation.orchestrator.generation_repo', repo):
            _validate_generation_origins(origins, "user_1")

        repo.get_by_id.assert_called_once_with("gen_1", user_id="user_1")

    def test_missing_source_raises_invalid_generation_source(self):
        repo = Mock()
        repo.get_by_id = Mock(return_value=None)
        origins = [{"field_name": "source_image", "source_generation_id": "gen_404", "source_file_index": 0}]

        with patch('src.features.generation.orchestrator.generation_repo', repo):
            with pytest.raises(InvalidGenerationSourceException):
                _validate_generation_origins(origins, "user_1")

    def test_foreign_source_raises_invalid_generation_source(self):
        """`generation_repo.get_by_id(id, user_id=...)` already returns None for a
        generation that exists but belongs to someone else - same 404-not-403
        shape ModelAccessPolicy/GenerationPolicy use elsewhere."""
        repo = Mock()
        repo.get_by_id = Mock(return_value=None)
        origins = [{"field_name": "source_image", "source_generation_id": "gen_other_user", "source_file_index": 0}]

        with patch('src.features.generation.orchestrator.generation_repo', repo):
            with pytest.raises(InvalidGenerationSourceException):
                _validate_generation_origins(origins, "user_1")


# --- start_generation wiring -------------------------------------------------

@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """A real `bind_form` strips any key that isn't a declared preset field
    (see src/features/forms/binding.py) - these tests aren't about form
    binding, so bypass it entirely and forward form_data untouched, the same
    way test_orchestrator_ltx_geometry.py does."""
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
        generation_id='test_gen_123',
        preset_id='test_preset',
        preset_template=Mock(version='1.0.0'),
        pipes=[{'name': 'generator', 'config': {}}],
    ))
    return builder


@pytest.fixture
def mock_backend_registry():
    from src.features.backends.backend_registry import BackendRegistry
    registry = Mock(spec=BackendRegistry)
    backend = Mock()
    backend.backend_id = 'local_backend_1'
    backend.name = 'Local Backend'
    backend.engine = 'native'
    backend.start_generation = AsyncMock()
    backend.cancel_generation = AsyncMock(return_value=True)
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    return registry


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
    manager.get_file_storage_directory = Mock(return_value='/storage')
    return manager


@pytest.fixture
def mock_output_processor():
    from src.features.generation.output_processor import OutputProcessor
    processor = Mock(spec=OutputProcessor)
    processor.process_output = AsyncMock(return_value={'processed': True})
    return processor


@pytest.fixture
def mock_generation_repo():
    with patch('src.features.generation.orchestrator.generation_repo') as mock_repo, \
         patch('src.features.generation.status_tracker.generation_repo', mock_repo):
        mock_repo.create = Mock()
        mock_repo.update_status = Mock()
        mock_repo.get_by_id = Mock()
        yield mock_repo


@pytest.fixture
def mock_preset_template_loader():
    """A plain (non-LTX, non-video) preset, so the LTX geometry preflight and
    the Video Director normalization are both no-ops here - only the
    provenance wiring is under test."""
    loader = Mock()
    preset = Mock()
    preset.engine = 'native'
    preset.tags = []
    preset.vars = {}
    loader.load_preset_by_id = Mock(return_value=preset)
    return loader


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings,
    mock_output_processor,
    mock_preset_template_loader,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_hub=mock_connection_manager,
        settings=mock_settings,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _request(form_data=None, mode='txt2img'):
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {} if form_data is None else form_data
    request.prompts = None
    request.prompt_state = None
    request.mode = mode
    request.backend_id = None
    request.tag_ids = None
    request.segments = None
    request.variables = None
    return request


class TestProvenanceRejectsBeforePersistence:
    @pytest.mark.asyncio
    async def test_foreign_or_missing_source_rejects_before_persistence(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        mock_generation_repo.get_by_id.return_value = None
        request = _request({
            "source_image": "upload.png",
            "source_image__origin": {"generation_id": "gen_404", "file_index": 0},
        })

        with pytest.raises(InvalidGenerationSourceException):
            await orchestrator.start_generation(request, "user_123")

        mock_generation_repo.create.assert_not_called()
        mock_backend_registry.select_backend_for_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_origin_rejects_before_persistence(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        request = _request({
            "source_image": "upload.png",
            "source_image__origin": {"generation_id": "gen_1"},  # missing file_index
        })

        with pytest.raises(ValueError):
            await orchestrator.start_generation(request, "user_123")

        mock_generation_repo.create.assert_not_called()
        mock_backend_registry.select_backend_for_generation.assert_not_called()
        mock_generation_repo.get_by_id.assert_not_called()


class TestProvenancePersistsOnSuccess:
    @pytest.mark.asyncio
    async def test_valid_origin_persists_a_source_link(self, orchestrator, mock_generation_repo):
        mock_generation_repo.get_by_id.return_value = Mock()  # source exists & is owned
        request = _request({
            "source_image": "upload.png",
            "source_image__origin": {"generation_id": "gen_1", "file_index": 3},
        })

        mock_source_repo = Mock()
        with patch('src.features.generation.source_repository.generation_source_repo', mock_source_repo), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_new_1'):
            result = await orchestrator.start_generation(request, "user_123")

        assert result['generation_id'] == 'gen_new_1'
        mock_source_repo.create_for_generation.assert_called_once_with(
            'gen_new_1',
            [{"field_name": "source_image", "source_generation_id": "gen_1", "source_file_index": 3}],
        )

    @pytest.mark.asyncio
    async def test_no_origin_keys_skips_source_persistence_entirely(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({"source_image": "upload.png"})

        mock_source_repo = Mock()
        with patch('src.features.generation.source_repository.generation_source_repo', mock_source_repo), \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_new_2'):
            result = await orchestrator.start_generation(request, "user_123")

        assert result['generation_id'] == 'gen_new_2'
        mock_source_repo.create_for_generation.assert_not_called()
        # No origin keys means _validate_generation_origins never runs either.
        mock_generation_repo.get_by_id.assert_not_called()
