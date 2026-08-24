"""
Test fixtures for preset-related data.

Provides fixtures for creating sample preset templates, modes,
forms, and other preset-related test data.
"""

import pytest
from typing import Dict, Any

from src.pipelines.contracts import IOType
from src.features.presets.templates import PresetTemplate, ModeTemplate, FormTemplate, FieldTemplate, PipeTemplate
from src.pipelines.models import BaseModel


@pytest.fixture
def sample_field_template() -> FieldTemplate:
    """
    Create a sample field template for forms.

    Returns:
        FieldTemplate: Sample text field template
    """
    return FieldTemplate(
        name='prompt',
        label='Prompt',
        type='text',
        default='A beautiful landscape',
        required=True,
        description='Enter your image description'
    )


@pytest.fixture
def sample_form_template(sample_field_template) -> FormTemplate:
    """
    Create a sample form template with common fields.

    Args:
        sample_field_template: Sample field template fixture

    Returns:
        FormTemplate: Sample form with multiple fields
    """
    return FormTemplate(
        name='generation_form',
        fields=[
            sample_field_template,
            FieldTemplate(
                name='negative_prompt',
                label='Negative Prompt',
                type='text',
                default='blurry, low quality',
                required=False,
                description='What to avoid in the image'
            ),
            FieldTemplate(
                name='steps',
                label='Steps',
                type='number',
                default=30,
                required=True,
                description='Number of inference steps'
            ),
            FieldTemplate(
                name='cfg_scale',
                label='CFG Scale',
                type='number',
                default=7.5,
                required=True,
                description='Classifier-free guidance scale'
            )
        ]
    )


@pytest.fixture
def sample_pipe_template() -> PipeTemplate:
    """
    Create a sample pipe template for generation pipeline.

    Returns:
        PipeTemplate: Sample generator pipe template
    """
    return PipeTemplate(
        name='generator',
        id='generator',
        enabled=True,
        configuration={
            'sampler': 'euler_a',
            'scheduler': 'normal'
        },
        input=[
            ('model', 'checkpoint_loader', 'model'),
            ('prompt', 'prompt_encoder', 'positive_conditioning'),
            ('negative_prompt', 'prompt_encoder', 'negative_conditioning')
        ]
    )


@pytest.fixture
def sample_mode_template(sample_form_template, sample_pipe_template) -> ModeTemplate:
    """
    Create a sample mode template with forms and pipes.

    Args:
        sample_form_template: Sample form template fixture
        sample_pipe_template: Sample pipe template fixture

    Returns:
        ModeTemplate: Sample mode with txt2img configuration
    """
    return ModeTemplate(
        forms=[sample_form_template],
        pipes=[
            PipeTemplate(
                name='downloader',
                id='downloader',
                enabled=True,
                configuration={}
            ),
            PipeTemplate(
                name='checkpoint_loader',
                id='checkpoint_loader',
                enabled=True,
                configuration={},
                input=[('model', 'downloader', 'model')]
            ),
            PipeTemplate(
                name='prompt_encoder',
                id='prompt_encoder',
                enabled=True,
                configuration={},
                input=[
                    ('clip', 'checkpoint_loader', 'clip'),
                    ('prompt', 'form', 'prompt'),
                    ('negative_prompt', 'form', 'negative_prompt')
                ]
            ),
            sample_pipe_template
        ]
    )


@pytest.fixture
def sample_preset_template(
    sample_mode_template
) -> PresetTemplate:
    """
    Create a sample preset template instance.

    Provides a complete preset configuration with model, modes, and forms.
    This is useful for testing preset loading and processing.

    Args:
        sample_mode_template: Sample mode template fixture

    Returns:
        PresetTemplate: Complete sample preset template
    """
    from src.features.presets.templates import GenerationMode

    return PresetTemplate(
        id='workbench/sdxl/realistic',
        name='SDXL Realistic',
        version='1.0.0',
        description='Realistic image generation with SDXL',
        path='presets/workbench/sdxl/realistic',
        modes={
            GenerationMode.TXT2IMG: sample_mode_template,
            GenerationMode.IMG2IMG: sample_mode_template
        },
        form=None,
        vars=None,
        tags=['realistic', 'sdxl']
    )


@pytest.fixture
def sample_flux_preset_template(sample_mode_template) -> PresetTemplate:
    """
    Create a sample FLUX preset template.

    Args:
        sample_mode_template: Sample mode template fixture

    Returns:
        PresetTemplate: FLUX preset template
    """
    from src.features.presets.templates import GenerationMode


    return PresetTemplate(
        id='workbench/flux/dev',
        name='FLUX Dev',
        version='1.0.0',
        description='FLUX.1 development model',
        path='presets/workbench/flux/dev',
        modes={
            GenerationMode.TXT2IMG: sample_mode_template
        },
        form=None,
        vars=None,
        tags=['flux']
    )
