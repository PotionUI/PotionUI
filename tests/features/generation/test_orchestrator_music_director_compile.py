"""Tests for the `compiled_lyrics` attachment `GenerationOrchestrator.start_generation()`
performs on a normalized Music Director document.

`compile_sections_to_lyrics` (src.features.music_director.normalize) is a pure function,
not called by `normalize_music_director` itself (docs/music-director.md: "not wired into
any pipe yet"). The orchestrator is the wiring point a preset's pipeline.yml relies on --
see content/presets/marketplace/MiniMax-Music3/modes/song/pipeline.yml's header comment.
The normalizer's own validation/canonicalization rules are unit-tested on their own
(tests/features/music_director/test_normalize.py); these tests only cover this one
additional attachment.

Sibling of tests/features/generation/test_orchestrator_video_director.py.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
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
    from src.platform.websocket.connection_manager import ConnectionManager
    manager = Mock(spec=ConnectionManager)
    manager.broadcast_to_generation = AsyncMock()
    return manager


@pytest.fixture
def mock_settings_manager():
    from src.platform.settings.settings import SettingsManager
    manager = Mock(spec=SettingsManager)
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
    preset.vars = {
        'music_director': {
            'preset_modes': ['song'],
            'modes': {'t2m': {}, 'song': {}, 'director': {'max_sections': 12, 'compile': 'single_shot'}},
            'settings': {'bpm': False, 'key': False, 'time_signature': False},
            'limits': {'default_duration': 60, 'max_duration': 360},
        },
    }
    loader.load_preset_by_id = Mock(return_value=preset)
    return loader


@pytest.fixture
def orchestrator(
    mock_pipeline_builder,
    mock_backend_registry,
    mock_connection_manager,
    mock_settings_manager,
    mock_output_processor,
    mock_preset_template_loader,
):
    from src.features.generation.orchestrator import GenerationOrchestrator
    return GenerationOrchestrator(
        pipeline_builder=mock_pipeline_builder,
        backend_registry=mock_backend_registry,
        connection_manager=mock_connection_manager,
        settings_manager=mock_settings_manager,
        output_processor=mock_output_processor,
        preset_template_loader=mock_preset_template_loader,
    )


def _request(form_data=None):
    request = Mock()
    request.preset_id = 'test_preset_123'
    request.form_data = {} if form_data is None else form_data
    request.prompts = None
    request.prompt_state = None
    request.mode = 'song'
    request.backend_id = None
    request.tag_ids = None
    request.segments = None
    request.variables = None
    return request


class TestFormSeedOverride:
    """The plain form `seed` field must win over the document's own
    `settings.seed` when the user actually set one -- the editor no longer
    carries its own seed field at all (point 5 of the Music3 redesign), so
    the form field is the ONLY place a seed is ever set. Mirrors
    tests/features/generation/test_orchestrator_video_director.py's
    `TestFormSeedOverride` exactly."""

    @pytest.mark.asyncio
    async def test_explicit_form_seed_overrides_document_seed(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 'song', 'settings': {'seed': -1, 'duration': 30}}
        request = _request({'music_director': raw_doc, 'seed': 4242})

        with patch(
            'src.features.generation.orchestrator.normalize_music_director',
            return_value={'sections': [], 'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_music_seed_1'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == 4242
        assert called_doc['settings']['duration'] == 30
        assert raw_doc['settings']['seed'] == -1  # original untouched

    @pytest.mark.asyncio
    async def test_form_seed_minus_one_leaves_document_seed_alone(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 'song', 'settings': {'seed': -1}}
        request = _request({'music_director': raw_doc, 'seed': -1})

        with patch(
            'src.features.generation.orchestrator.normalize_music_director',
            return_value={'sections': [], 'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_music_seed_2'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == -1  # normalizer resolves this itself

    @pytest.mark.asyncio
    async def test_absent_form_seed_leaves_document_seed_alone(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 'song', 'settings': {'seed': -1}}
        request = _request({'music_director': raw_doc})  # no 'seed' key at all

        with patch(
            'src.features.generation.orchestrator.normalize_music_director',
            return_value={'sections': [], 'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_music_seed_3'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == -1

    @pytest.mark.asyncio
    async def test_override_synthesizes_settings_when_document_has_none(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {'schema_version': 1, 'mode': 't2m'}
        request = _request({'music_director': raw_doc, 'seed': 777})

        with patch(
            'src.features.generation.orchestrator.normalize_music_director',
            return_value={'sections': [], 'normalized': True},
        ) as mock_normalize, patch(
            'src.features.generation.orchestrator.generate_ulid', return_value='gen_music_seed_4'
        ):
            await orchestrator.start_generation(request, 'user_123')

        called_doc = mock_normalize.call_args[0][0]
        assert called_doc['settings']['seed'] == 777
        assert 'settings' not in raw_doc


class TestCompiledLyricsAttachment:
    @pytest.mark.asyncio
    async def test_a_sections_bearing_document_gets_compiled_lyrics(
        self, orchestrator, mock_generation_repo
    ):
        raw_doc = {
            'schema_version': 1, 'mode': 'director',
            'sections': [
                {'id': 'sec-1', 'kind': 'verse', 'lyrics': 'rain on the window'},
                {'id': 'sec-2', 'kind': 'chorus', 'lyrics': 'nowhere to go'},
            ],
        }
        request = _request({'music_director': raw_doc})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_music_1'):
            await orchestrator.start_generation(request, 'user_123')

        document = request.form_data['music_director']
        assert document['compiled_lyrics'] == '[Verse]\nrain on the window\n\n[Chorus]\nnowhere to go'

    @pytest.mark.asyncio
    async def test_a_sectionless_document_gets_an_empty_compiled_lyrics_key(
        self, orchestrator, mock_generation_repo
    ):
        """`t2m` carries no sections -- `compiled_lyrics` must still be PRESENT
        (as "") rather than absent, so a pipeline's `{{ document.compiled_lyrics }}`
        (StrictUndefined) never raises on a document that legitimately has none."""
        raw_doc = {'schema_version': 1, 'mode': 't2m', 'description': 'a mood'}
        request = _request({'music_director': raw_doc})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_music_2'):
            await orchestrator.start_generation(request, 'user_123')

        document = request.form_data['music_director']
        assert 'compiled_lyrics' in document
        assert document['compiled_lyrics'] == ''

    @pytest.mark.asyncio
    async def test_no_music_director_key_leaves_form_data_untouched(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({'steps': 20})

        with patch('src.features.generation.orchestrator.compile_sections_to_lyrics') as mock_compile, \
             patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_music_3'):
            await orchestrator.start_generation(request, 'user_123')

        mock_compile.assert_not_called()
        assert request.form_data == {'steps': 20}
