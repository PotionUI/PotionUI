"""
Comprehensive tests for the canonical PipelineBuilder.

Tests cover:
- Pipeline construction from preset id or an already-loaded preset template
- Form data processing
- Prompt handling
- Error handling
- BuiltPipeline.to_backend_payload() shape
"""

import pytest
from unittest.mock import Mock, patch

from src.features.generation.pipeline_builder import PipelineBuilder, BuiltPipeline
from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.features.presets.templates import PresetTemplate, GenerationMode


@pytest.fixture
def mock_preset_template_loader():
    """Mock PresetTemplateLoader."""
    loader = Mock(spec=PresetTemplateLoader)
    return loader


@pytest.fixture
def mock_preset_processor():
    """Mock PresetProcessor."""
    processor = Mock(spec=PresetProcessor)
    processor.process = Mock(return_value=[
        {
            'name': 'downloader',
            'id': 'downloader',
            'config': {'model': 'test_model'},
            'enabled': True
        },
        {
            'name': 'checkpoint_loader',
            'id': 'checkpoint_loader',
            'config': {},
            'enabled': True
        },
        {
            'name': 'generator',
            'id': 'generator',
            'config': {'steps': 20, 'cfg_scale': 7.5},
            'enabled': True
        }
    ])
    return processor


@pytest.fixture
def pipeline_builder(mock_preset_template_loader, mock_preset_processor):
    """Create PipelineBuilder with mocked dependencies."""
    return PipelineBuilder(
        preset_template_loader=mock_preset_template_loader,
        preset_processor=mock_preset_processor
    )


@pytest.fixture
def sample_preset_template(sample_mode_template):
    """Sample preset template fixture."""
    return PresetTemplate(
        id='test_preset_123',
        name='Test Preset',
        version='1.0.0',
        description='Test preset for testing',
        path='presets/test/sdxl/v1',
        modes={
            GenerationMode.TXT2IMG: sample_mode_template,
            GenerationMode.IMG2IMG: sample_mode_template
        },
        form=None,
        vars=None,
        tags=['test']
    )


class TestPipelineBuilderInitialization:
    """Test cases for PipelineBuilder initialization."""

    def test_initialization_with_dependencies(
        self,
        mock_preset_template_loader,
        mock_preset_processor
    ):
        """Test proper initialization with dependencies."""
        builder = PipelineBuilder(
            preset_template_loader=mock_preset_template_loader,
            preset_processor=mock_preset_processor
        )

        assert builder.preset_template_loader == mock_preset_template_loader
        assert builder.preset_processor == mock_preset_processor


class TestBasicPipelineBuilding:
    """Test cases for basic pipeline building."""

    def test_build_pipeline_basic(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test basic pipeline building with minimal parameters."""
        preset_id = 'test_preset_123'
        form_data = {'steps': 20, 'cfg_scale': 7.5}

        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        result = pipeline_builder.build_pipeline(
            preset_id=preset_id,
            form_data=form_data,
            mode='txt2img'
        )

        assert isinstance(result, BuiltPipeline)
        assert result.generation_id
        assert result.preset_id == preset_id
        assert len(result.pipes) == 3
        assert result.preset_template == sample_preset_template

        mock_preset_template_loader.load_preset_by_id.assert_called_once_with(preset_id)
        mock_preset_processor.process.assert_called_once()

    def test_build_pipeline_with_preloaded_preset_template(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test that passing an already-loaded PresetTemplate skips the loader
        lookup - this is the path the pipeline graph preview uses so it never
        double-loads a preset the caller already resolved."""
        result = pipeline_builder.build_pipeline(
            preset_id=sample_preset_template,
            form_data={},
            mode='txt2img'
        )

        assert result.preset_id == sample_preset_template.id
        assert result.preset_template == sample_preset_template
        mock_preset_template_loader.load_preset_by_id.assert_not_called()

    def test_build_pipeline_with_custom_generation_id(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with provided generation_id."""
        preset_id = 'test_preset_123'
        custom_gen_id = 'custom_gen_id_456'

        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        result = pipeline_builder.build_pipeline(
            preset_id=preset_id,
            form_data={},
            generation_id=custom_gen_id
        )

        assert result.generation_id == custom_gen_id

    def test_build_pipeline_generates_ulid_when_not_provided(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test that ULID is generated when generation_id not provided."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        with patch('src.features.generation.pipeline_builder.generate_ulid', return_value='generated_ulid'):
            result = pipeline_builder.build_pipeline(
                preset_id='test_preset',
                form_data={}
            )

        assert result.generation_id == 'generated_ulid'

    def test_build_pipeline_with_different_modes(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with different generation modes."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        modes = ['txt2img', 'img2img', 'inpaint', 'outpaint']

        for mode in modes:
            pipeline_builder.build_pipeline(
                preset_id='test_preset',
                form_data={},
                mode=mode
            )

            call_args = mock_preset_processor.process.call_args[0]
            generation_data = call_args[1]
            assert generation_data['mode'] == mode


class TestFormDataHandling:
    """Test cases for form data processing."""

    def test_build_pipeline_with_complex_form_data(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with complex form data."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        form_data = {
            'steps': 30,
            'cfg_scale': 8.5,
            'sampler': 'euler_a',
            'tabs': {
                'style': 'realistic',
                'quality': 'high'
            },
            'advanced': {
                'clip_skip': 2,
                'seed': 12345
            }
        }

        pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data=form_data,
            mode='txt2img'
        )

        call_args = mock_preset_processor.process.call_args[0]
        generation_data = call_args[1]
        assert generation_data['form_data'] == form_data

    def test_build_pipeline_with_empty_form_data(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with empty form data."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        result = pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data={}
        )

        assert result.pipes is not None

    def test_build_pipeline_with_none_form_data(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with None form data."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data=None
        )

        call_args = mock_preset_processor.process.call_args[0]
        generation_data = call_args[1]
        assert generation_data['form_data'] == {}


class TestPromptHandling:
    """Test cases for prompt and negative prompt handling."""

    def test_build_pipeline_with_prompts(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with prompts."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data={},
            prompt='beautiful landscape with mountains',
            negative_prompt='ugly, blurry, low quality'
        )

        call_args = mock_preset_processor.process.call_args[0]
        generation_data = call_args[1]
        assert generation_data['prompt'] == 'beautiful landscape with mountains'
        assert generation_data['negative_prompt'] == 'ugly, blurry, low quality'

    def test_build_pipeline_with_prompts_array(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test pipeline building with the new prompts array format."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        prompts = [{'positive': 'p1', 'negative': 'n1'}, {'positive': 'p2', 'negative': 'n2'}]

        pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data={},
            prompts=prompts
        )

        call_args = mock_preset_processor.process.call_args[0]
        generation_data = call_args[1]
        assert generation_data['prompts'] == prompts
        assert generation_data['prompt'] == 'p1'
        assert generation_data['negative_prompt'] == 'n1'


class TestErrorHandling:
    """Test cases for error handling."""

    def test_build_pipeline_preset_not_found(
        self,
        pipeline_builder,
        mock_preset_template_loader
    ):
        """Test error when preset is not found."""
        mock_preset_template_loader.load_preset_by_id.return_value = None

        with pytest.raises(ValueError, match="Preset .* not found"):
            pipeline_builder.build_pipeline(
                preset_id='nonexistent_preset',
                form_data={}
            )

    def test_build_pipeline_processor_error(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Test error handling when processor fails."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template
        mock_preset_processor.process.side_effect = Exception("Processing failed")

        with pytest.raises(ValueError, match="Failed to process preset"):
            pipeline_builder.build_pipeline(
                preset_id='test_preset',
                form_data={}
            )


class TestBuiltPipelinePayload:
    """Test cases for BuiltPipeline.to_backend_payload()."""

    def test_to_backend_payload_shape(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        mock_preset_processor,
        sample_preset_template
    ):
        """Payload must be exactly the dict shape backends receive today."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        result = pipeline_builder.build_pipeline(
            preset_id='test_preset',
            form_data={},
            generation_id='gen-abc'
        )

        payload = result.to_backend_payload()

        assert payload == {
            'generation_id': 'gen-abc',
            'preset_id': 'test_preset',
            'pipes': result.pipes,
        }

    def test_payload_carries_no_preset_template(
        self,
        pipeline_builder,
        mock_preset_template_loader,
        sample_preset_template
    ):
        """The template is a live dataclass graph, not something a worker in
        another process could be handed - it must not ride in the payload."""
        mock_preset_template_loader.load_preset_by_id.return_value = sample_preset_template

        result = pipeline_builder.build_pipeline(preset_id='test_preset', form_data={})

        assert 'preset_template' not in result.to_backend_payload()
        assert result.preset_template is sample_preset_template
