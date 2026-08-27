"""
Tests for the Video Director wire document being normalized inside
GenerationOrchestrator.start_generation(), and for prompt expansion being
bypassed once a document is present.

The normalizer (src.features.video_director.normalize_video_director) already owns
all validation/canonicalization rules and is unit-tested on its own
(tests/core/video_director/). These tests only cover the orchestrator's wiring:
where it's called, what it's called with, and what happens when it rejects a
document.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.video_director import VideoDirectorValidationError


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    """See tests/core/generation/test_orchestrator.py::_bind_form_passthrough.
    These tests exercise video_director normalization wiring specifically, so
    bind_form (a separate boundary that runs just before it) is stubbed out
    rather than given a real preset form-schema tree."""
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
    loader = Mock()
    preset = Mock()
    preset.engine = 'native'
    preset.vars = {'video_director': {'modes': {'t2v': {}}, 'limits': {}}}
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


def _request(form_data=None):
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {} if form_data is None else form_data
    request.prompts = None
    request.prompt_state = None
    request.mode = 'txt2img'
    request.backend_id = None
    request.tag_ids = None
    request.segments = None
    request.variables = None
    return request


class TestNormalizationWiring:
    @pytest.mark.asyncio
    async def test_document_present_is_normalized_and_replaces_form_data(
        self, orchestrator, mock_preset_template_loader, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 't2v', 'segments': [{'id': 'seg-1', 'prompt': 'a cat'}]}
        canonical = {'schema_version': 1, 'mode': 't2v', 'segments': [], 'normalized': True}
        request = _request({'video_director': raw_doc})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value=canonical,
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_vd_1'
        ):
            await orchestrator.start_generation(request, 'user_123')

        # `request.form_data` is passed through as the 4th (form_data) arg and
        # then mutated in place by the very next line
        # (`request.form_data['video_director'] = normalize_video_director(...)`)
        # -- since Mock captures the argument by reference, not a copy, the
        # dict inspected here already carries the canonical value.
        mock_normalize.assert_called_once_with(
            raw_doc,
            {'modes': {'t2v': {}}, 'limits': {}},
            '/storage',
            {'video_director': canonical},
        )
        assert request.form_data['video_director'] == canonical

        gen_arg = mock_generation_repo.create.call_args[0][0]
        assert gen_arg.form_data['video_director'] == canonical

    @pytest.mark.asyncio
    async def test_invalid_document_raises_and_nothing_is_persisted_or_queued(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        request = _request({'video_director': {'schema_version': 2}})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            side_effect=VideoDirectorValidationError(['bad document']),
        ):
            with pytest.raises(VideoDirectorValidationError):
                await orchestrator.start_generation(request, 'user_123')

        mock_generation_repo.create.assert_not_called()
        mock_backend_registry.select_backend_for_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_video_director_key_leaves_form_data_untouched(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({'steps': 20})

        with patch('src.features.generation.orchestrator.normalize_video_director') as mock_normalize, \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_vd_2'):
            await orchestrator.start_generation(request, 'user_123')

        mock_normalize.assert_not_called()
        assert request.form_data == {'steps': 20}

    @pytest.mark.asyncio
    async def test_non_dict_video_director_value_is_left_alone(
        self, orchestrator, mock_generation_repo
    ):
        """A non-dict value (e.g. already-normalized-elsewhere or malformed) is not our concern here."""
        request = _request({'video_director': 'not-a-dict'})

        with patch('src.features.generation.orchestrator.normalize_video_director') as mock_normalize, \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_vd_3'):
            await orchestrator.start_generation(request, 'user_123')

        mock_normalize.assert_not_called()
        assert request.form_data['video_director'] == 'not-a-dict'


class TestFormSeedOverride:
    """The plain form `seed` field must win over the document's own
    `settings.seed` when the user actually set one -- otherwise the
    frontend's hardcoded -1 in the document silently makes director
    generations unreproducible regardless of what the user picked."""

    @pytest.mark.asyncio
    async def test_explicit_form_seed_overrides_document_seed(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {
            'schema_version': 1, 'mode': 't2v',
            'settings': {'seed': -1, 'fps': 24},
            'segments': [{'id': 'seg-1', 'prompt': 'a cat'}],
        }
        request = _request({'video_director': raw_doc, 'seed': 4242})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value={'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_seed_1'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == 4242
        # fps and every other settings key survive the override untouched.
        assert called_doc['settings']['fps'] == 24
        # The original raw_doc (and its nested settings dict) must not be mutated.
        assert raw_doc['settings']['seed'] == -1

    @pytest.mark.asyncio
    async def test_form_seed_minus_one_leaves_document_seed_alone(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 't2v', 'settings': {'seed': -1}, 'segments': []}
        request = _request({'video_director': raw_doc, 'seed': -1})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value={'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_seed_2'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == -1  # normalizer resolves this itself

    @pytest.mark.asyncio
    async def test_absent_form_seed_leaves_document_seed_alone(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 't2v', 'settings': {'seed': -1}, 'segments': []}
        request = _request({'video_director': raw_doc})  # no 'seed' key at all

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value={'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_seed_3'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == -1

    @pytest.mark.asyncio
    async def test_non_int_form_seed_is_ignored(
        self, orchestrator, mock_generation_repo
    ):
        """A malformed form seed (e.g. a string from a bad client) must not crash
        the override -- it's simply not applied, same as -1/absent."""
        raw_doc = {'schema_version': 1, 'mode': 't2v', 'settings': {'seed': -1}, 'segments': []}
        request = _request({'video_director': raw_doc, 'seed': 'not-a-seed'})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value={'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_seed_4'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == -1

    @pytest.mark.asyncio
    async def test_override_synthesizes_settings_when_document_has_none(
        self, orchestrator, mock_generation_repo
    ):
        """A document that omits `settings` entirely (the normalizer defaults it)
        must still pick up the form seed rather than erroring on a missing key."""
        raw_doc = {'schema_version': 1, 'mode': 't2v', 'segments': []}
        request = _request({'video_director': raw_doc, 'seed': 777})

        with patch(
            'src.features.generation.orchestrator.normalize_video_director',
            return_value={'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_seed_5'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == 777
        assert 'settings' not in raw_doc


class TestPromptExpansionBypass:
    def test_expansion_bypassed_when_document_present(self, orchestrator):
        request = _request({'video_director': {'mode': 't2v'}, 'quantity': 4, 'seed': 1})
        prompts = [{'positive': '{a|b|c}', 'negative': ''}]

        with patch('src.features.generation.prompt_expansion.expand_prompts') as mock_expand:
            result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        mock_expand.assert_not_called()
        assert result is prompts

    def test_expansion_runs_normally_without_document(self, orchestrator):
        request = _request({'quantity': 4, 'seed': 100})
        prompts = [{'positive': '{a|b|c}', 'negative': 'blurry'}]

        result = orchestrator._expand_prompts_per_image('gen1', request, prompts)

        assert len(result) == 4
        assert all(p['positive'] in ('a', 'b', 'c') for p in result)
