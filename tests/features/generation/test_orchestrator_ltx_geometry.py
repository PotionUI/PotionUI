"""Tests for GenerationOrchestrator's LTX two-stage upscale geometry
preflight (`_check_ltx_two_stage_geometry`, wired into `start_generation`
right after the Video Director normalization block).

The geometry math itself (`compute_two_stage_geometry`/
`nearest_achievable_resolution`/`required_axis_divisor`) is unit-tested on its
own in tests/pipelines/pipes/latent_upscaler/ltx/test_geometry.py. These tests
only cover the orchestrator's wiring: when the check fires, when it's a no-op,
and that a failure raises before persistence/backend selection -- same shape
as tests/features/generation/test_orchestrator_video_director.py.

Repro note: the preset's own default resolution ("768x512") is NOT the failing
case -- both axes are already divisible by the 1.5x scale's required 64px
(768/64=12, 512/64=8), so it passes. The preset's picker (content/presets/_shared/
resolutions/video.yml) offers several resolutions that DO fail at 1.5x
(832x480, 960x544, 544x960, 640x480 and their portrait mirrors) -- 960x544
("HQ Landscape") is used here as the concrete failing repro.
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
    """An LTX-tagged native preset -- the only family this preflight ever
    fires for (see `_check_ltx_two_stage_geometry`'s docstring)."""
    loader = Mock()
    preset = Mock()
    preset.engine = 'native'
    preset.tags = ['ltx', 'ltx-2', 'video']
    preset.vars = {}
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


def _request(form_data=None, mode='video'):
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


class TestGeometryPreflightFires:
    @pytest.mark.asyncio
    async def test_mismatched_1_5x_resolution_raises_before_persistence(
        self, orchestrator, mock_generation_repo, mock_backend_registry
    ):
        request = _request({'resolution': '960x544', 'upscale': '1.5x'})

        with pytest.raises(ValueError, match="not achievable"):
            await orchestrator.start_generation(request, 'user_123')

        mock_generation_repo.create.assert_not_called()
        mock_backend_registry.select_backend_for_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_message_names_resolution_and_suggests_alternative(
        self, orchestrator
    ):
        request = _request({'resolution': '960x544', 'upscale': '1.5x'})

        with pytest.raises(ValueError) as exc_info:
            await orchestrator.start_generation(request, 'user_123')

        message = str(exc_info.value)
        assert '960x544' in message
        assert '1.5x' in message
        assert '2.0x' in message
        # The nearest achievable resolution for 960x544 @ 1.5x is 960x512
        # (height snaps down to the nearest 64px multiple) -- see
        # tests/pipelines/pipes/latent_upscaler/ltx/test_geometry.py.
        assert '960x512' in message


class TestGeometryPreflightNoOp:
    @pytest.mark.asyncio
    async def test_2_0x_at_same_resolution_always_passes(
        self, orchestrator, mock_generation_repo
    ):
        """2.0x never disagrees (den=1, no rounding to disagree over) -- the
        maintainer's documented workaround."""
        request = _request({'resolution': '960x544', 'upscale': '2.0x'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_1'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_1'
        mock_generation_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_resolution_passes_at_1_5x(
        self, orchestrator, mock_generation_repo
    ):
        """The preset's own default resolution (768x512) is NOT the failing
        case -- both axes are already 64px-divisible."""
        request = _request({'resolution': '768x512', 'upscale': '1.5x'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_2'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_2'
        mock_generation_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_upscale_off_skips_the_check_entirely(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({'resolution': '960x544', 'upscale': 'off'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_3'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_3'

    @pytest.mark.asyncio
    async def test_no_upscale_field_skips_the_check_entirely(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({'resolution': '960x544'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_4'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_4'

    @pytest.mark.asyncio
    async def test_non_video_mode_skips_the_check_entirely(
        self, orchestrator, mock_generation_repo
    ):
        """The standalone `upscale` mode has no independent stage-2
        resolution to disagree with -- see the preflight's docstring."""
        request = _request({'resolution': '960x544', 'upscale': '1.5x'}, mode='upscale')

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_5'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_5'

    @pytest.mark.asyncio
    async def test_non_ltx_preset_skips_the_check_entirely(
        self, orchestrator, mock_generation_repo, mock_preset_template_loader
    ):
        mock_preset_template_loader.load_preset_by_id.return_value.tags = ['wan', 'video']
        request = _request({'resolution': '960x544', 'upscale': '1.5x'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_6'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_6'

    @pytest.mark.asyncio
    async def test_no_resolution_field_skips_the_check_entirely(
        self, orchestrator, mock_generation_repo
    ):
        request = _request({'upscale': '1.5x'})

        with patch('src.features.generation.orchestrator.generate_ulid', return_value='gen_geom_7'):
            result = await orchestrator.start_generation(request, 'user_123')

        assert result['generation_id'] == 'gen_geom_7'
