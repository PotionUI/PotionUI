"""
Artifact-specific generation output handlers.

This module contains handlers for artifact-type generation outputs that are
primarily used for transport and display purposes, not for persistent storage.
These handlers process outputs like progress updates, timers, comparisons,
model information, and seeds - all of which are meant to provide real-time
feedback and metadata during the generation process.

Handler Categories:
- CompareImagesGenerationOutputHandler: Handles image comparison artifacts
- ProgressGenerationOutputHandler: Handles generation progress updates
- TimerGenerationOutputHandler: Handles timing information
- ModelsGenerationOutputHandler: Handles model metadata
- SeedGenerationOutputHandler: Handles seed artifact outputs
- ComfyUIWorkflowGenerationOutputHandler: Handles ComfyUI workflow artifacts

All handlers inherit from BaseGenerationOutputHandler and implement
transport-only behavior (no file saving).
"""

import logging
from typing import Dict, Any
from abc import ABC, abstractmethod

from src.pipelines.outputs import (
    GenerationOutput,
    CompareImagesGenerationOutput,
    ProgressGenerationOutput,
    TimerGenerationOutput,
    ModelsGenerationOutput,
    SeedGenerationOutput,
    RenderedPromptGenerationOutput,
    WarmStartGenerationOutput,
    ComfyUIWorkflowGenerationOutput,
    DiffTextGenerationOutput,
)

# Import base handler from core
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.features.generation.media_utils import create_base64_image

logger = logging.getLogger(__name__)


class CompareImagesGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for CompareImagesGenerationOutput - processes comparison images."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process CompareImagesGenerationOutput."""
        return isinstance(output, CompareImagesGenerationOutput)

    def handle(self, output: CompareImagesGenerationOutput) -> Dict[str, Any]:
        """
        Process CompareImagesGenerationOutput - transport only, no saving.

        Args:
            output: CompareImagesGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'CompareImagesGenerationOutputHandler',
            'processed': True
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling CompareImagesGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


class ProgressGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for ProgressGenerationOutput - processes progress updates."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process ProgressGenerationOutput."""
        return isinstance(output, ProgressGenerationOutput)

    def handle(self, output: ProgressGenerationOutput) -> Dict[str, Any]:
        """
        Process ProgressGenerationOutput - transport only, no saving.

        Args:
            output: ProgressGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'ProgressGenerationOutputHandler',
            'processed': True
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling ProgressGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


class TimerGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for TimerGenerationOutput - processes timer updates."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process TimerGenerationOutput."""
        return isinstance(output, TimerGenerationOutput)

    def handle(self, output: TimerGenerationOutput) -> Dict[str, Any]:
        """
        Process TimerGenerationOutput - transport only, no saving.

        Args:
            output: TimerGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'TimerGenerationOutputHandler',
            'processed': True
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling TimerGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


class ModelsGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for ModelsGenerationOutput - processes model information."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process ModelsGenerationOutput."""
        return isinstance(output, ModelsGenerationOutput)

    def handle(self, output: ModelsGenerationOutput) -> Dict[str, Any]:
        """
        Process ModelsGenerationOutput - transport only, no saving.

        Args:
            output: ModelsGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'ModelsGenerationOutputHandler',
            'processed': True
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling ModelsGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


class SeedGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for SeedGenerationOutput - processes seed artifact outputs."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process SeedGenerationOutput."""
        return isinstance(output, SeedGenerationOutput)

    def handle(self, output: SeedGenerationOutput) -> Dict[str, Any]:
        """
        Process SeedGenerationOutput - transport only, no saving.

        Args:
            output: SeedGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'SeedGenerationOutputHandler',
            'processed': True,
            'index': output.index,
            'seed': output.seed
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling SeedGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


class RenderedPromptGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for RenderedPromptGenerationOutput - transport-only rendered-prompt provenance.

    Mirrors SeedGenerationOutputHandler: no file saving. The serializer delivers
    the resolved per-image prompt to the frontend as a pipe_artifact; the
    handler exists so OutputProcessor doesn't log an "unhandled output" warning
    on every emission (same reason DiffTextGenerationOutputHandler exists)."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process RenderedPromptGenerationOutput."""
        return isinstance(output, RenderedPromptGenerationOutput)

    def handle(self, output: RenderedPromptGenerationOutput) -> Dict[str, Any]:
        """Process RenderedPromptGenerationOutput - transport only, no saving."""
        return {
            'handler': 'RenderedPromptGenerationOutputHandler',
            'processed': True,
            'index': output.index,
        }


class WarmStartGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for WarmStartGenerationOutput - transport-only iterate-mode telemetry."""

    def can_handle(self, output: GenerationOutput) -> bool:
        return isinstance(output, WarmStartGenerationOutput)

    def handle(self, output: WarmStartGenerationOutput) -> Dict[str, Any]:
        """Process WarmStartGenerationOutput - transport only, no saving."""
        return {
            'handler': 'WarmStartGenerationOutputHandler',
            'processed': True,
            'index': output.index,
            'resume_step': output.resume_step,
        }


class DiffTextGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for DiffTextGenerationOutput - transport-only prompt-diff artifact.

    Without a handler_cls, OutputProcessor._process_via_spec() treats the
    spec as unhandled ("No handler registered for DiffTextGenerationOutput")
    and logs a warning on every single emission, even though the serializer
    already delivers it to the frontend fine as a pipe_artifact message. This
    mirrors every other transport-only artifact handler in this file.
    """

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process DiffTextGenerationOutput."""
        return isinstance(output, DiffTextGenerationOutput)

    def handle(self, output: DiffTextGenerationOutput) -> Dict[str, Any]:
        """Process DiffTextGenerationOutput - transport only, no saving."""
        return {
            'handler': 'DiffTextGenerationOutputHandler',
            'processed': True,
            'name': output.name,
        }


class ComfyUIWorkflowGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for ComfyUIWorkflowGenerationOutput - processes workflow artifacts for display."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process ComfyUIWorkflowGenerationOutput."""
        return isinstance(output, ComfyUIWorkflowGenerationOutput)

    def handle(self, output: ComfyUIWorkflowGenerationOutput) -> Dict[str, Any]:
        """
        Process ComfyUIWorkflowGenerationOutput - transport only, no saving.

        Args:
            output: ComfyUIWorkflowGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'ComfyUIWorkflowGenerationOutputHandler',
            'processed': True,
            'node_count': output.node_count,
            'workflow_file': output.workflow_file
        }

        try:
            return metadata

        except Exception as e:
            logger.error(f"Error handling ComfyUIWorkflowGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata


def serialize_compare_images_output(output: CompareImagesGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize CompareImagesGenerationOutput for pipe_artifact messages."""
    result = {
        'artifact_type': 'compare_images',
        'artifact_data': {}
    }

    try:
        compare_data = {}

        if output.compare[1] is not None:
            # Image saving is now handled by core handlers, just create base64 for display
            compare_data['compare_image'] = create_base64_image(output.compare[1])
            compare_data['compare_label'] = output.compare[0]

        if output.to[1] is not None:
            # Image saving is now handled by core handlers, just create base64 for display
            compare_data['to_image'] = create_base64_image(output.to[1])
            compare_data['to_label'] = output.to[0]

        result['artifact_data'] = compare_data
    except Exception as e:
        logger.error(f"Failed to serialize compare images: {str(e)}")
        result['artifact_data'] = {'error': str(e)}

    return result


def serialize_progress_output(output: ProgressGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize ProgressGenerationOutput for generation_status messages."""
    result = {
        'status': 'running',  # Progress outputs indicate generation is running
        'current_step': getattr(output, 'state', ''),
        'message': getattr(output, 'title', ''),
    }

    # Handle Progress objects to get progress percentage. A stage that hasn't
    # reported a fraction (e.g. a cold model load with no per-component
    # breakdown) stays `None` rather than collapsing to 0.0 - a real 0% and
    # "unknown, still running" are different states, and coercing both to the
    # same number is what made the frontend bar look stuck/reset instead of
    # indeterminate (see generate/+page.svelte's indeterminate handling).
    if hasattr(output, 'progress') and output.progress is not None:
        try:
            if output.progress.max > 0:
                result['progress'] = output.progress.current / output.progress.max
            else:
                result['progress'] = 0.0
        except Exception as e:
            logger.error(f"Failed to serialize progress: {str(e)}")
            result['progress'] = None
    else:
        result['progress'] = None

    # Copy other fields if they exist
    if hasattr(output, 'current_step_num'):
        result['current_step_num'] = output.current_step_num
    if hasattr(output, 'total_steps'):
        result['total_steps'] = output.total_steps
    if hasattr(output, 'created_at'):
        result['created_at'] = output.created_at
    if hasattr(output, 'completed_at'):
        result['completed_at'] = output.completed_at

    return result


def serialize_seed_output(output: SeedGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize SeedGenerationOutput for pipe_artifact messages."""
    return {
        'artifact_type': 'seed',
        'artifact_data': {
            'seed': output.seed
        }
    }


def serialize_rendered_prompt_output(output: RenderedPromptGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize RenderedPromptGenerationOutput for pipe_artifact messages."""
    return {
        'artifact_type': 'rendered_prompt',
        'artifact_data': {
            'index': output.index,
            'positive': output.positive,
            'negative': output.negative,
        }
    }


def serialize_warm_start_output(output: WarmStartGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize WarmStartGenerationOutput for pipe_artifact messages."""
    return {
        'artifact_type': 'warm_start',
        'artifact_data': {
            'resume_step': output.resume_step,
            'total_steps': output.total_steps,
            'steps_skipped': output.steps_skipped,
            'similarity': output.similarity,
        }
    }


def serialize_diff_text_output(output: DiffTextGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize DiffTextGenerationOutput for pipe_artifact messages."""
    return {
        'artifact_type': 'diff_text',
        'artifact_data': {
            'name': output.name,
            'diff': output.diff,
            'negative_applied': output.negative_applied
        }
    }


def serialize_models_output(output: ModelsGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize ModelsGenerationOutput for pipe_artifact messages."""
    result = {
        'artifact_type': 'models',
        'artifact_data': {'models': []}
    }

    try:
        models_data = []
        for model in output.models:
            model_data = {
                'name': str(model.name),
                'type': str(model.type)
            }
            if hasattr(model, 'weight') and model.weight is not None:
                model_data['weight'] = float(model.weight)
            models_data.append(model_data)

        result['artifact_data']['models'] = models_data
    except Exception as e:
        logger.error(f"Failed to serialize models: {str(e)}")
        result['artifact_data'] = {
            'models': [],
            'error': str(e)
        }

    return result


def serialize_timer_output(output: TimerGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize TimerGenerationOutput for timer_update messages."""
    return {
        'timer_name': output.name,
        'timer_value': output.value,
        'timer_unit': output.unit,
        'formatted_time': f"{output.value:.2f}{output.unit}"
    }


def serialize_workflow_output(output: ComfyUIWorkflowGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize ComfyUIWorkflowGenerationOutput for pipe_artifact messages."""
    return {
        'artifact_type': 'workflow',
        'artifact_data': {
            'workflow': output.workflow,
            'node_count': output.node_count,
            'workflow_file': output.workflow_file
        }
    }


output_type_registry.register(OutputTypeSpec(
    output_cls=CompareImagesGenerationOutput,
    key='compare_images',
    message_type='pipe_artifact',
    serializer=serialize_compare_images_output,
    handler_cls=CompareImagesGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=ProgressGenerationOutput,
    key='progress',
    message_type='generation_status',
    serializer=serialize_progress_output,
    handler_cls=ProgressGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=TimerGenerationOutput,
    key='timer',
    message_type='timer_update',
    serializer=serialize_timer_output,
    handler_cls=TimerGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=ModelsGenerationOutput,
    key='models',
    message_type='pipe_artifact',
    serializer=serialize_models_output,
    handler_cls=ModelsGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=SeedGenerationOutput,
    key='seed',
    message_type='pipe_artifact',
    serializer=serialize_seed_output,
    handler_cls=SeedGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=RenderedPromptGenerationOutput,
    key='rendered_prompt',
    message_type='pipe_artifact',
    serializer=serialize_rendered_prompt_output,
    handler_cls=RenderedPromptGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=WarmStartGenerationOutput,
    key='warm_start',
    message_type='pipe_artifact',
    serializer=serialize_warm_start_output,
    handler_cls=WarmStartGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=ComfyUIWorkflowGenerationOutput,
    key='comfyui_workflow',
    message_type='pipe_artifact',
    serializer=serialize_workflow_output,
    handler_cls=ComfyUIWorkflowGenerationOutputHandler,
))

output_type_registry.register(OutputTypeSpec(
    output_cls=DiffTextGenerationOutput,
    key='diff_text',
    message_type='pipe_artifact',
    serializer=serialize_diff_text_output,
    handler_cls=DiffTextGenerationOutputHandler,
))
