"""
Centralized serializer for generation outputs to WebSocket messages.

This module resolves the OutputTypeSpec for a given GenerationOutput (see
src.features.generation.output_types) and uses its message_type/serializer to
build the WebSocket-compatible message, instead of dispatching through a
long isinstance chain.
"""

import logging
from typing import Dict, Any

from src.platform.util.ids import generate_ulid

from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    GenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)
from src.features.generation.output_types import SerializeContext, output_type_registry

logger = logging.getLogger(__name__)

_MEDIA_OUTPUTS = (
    ImageGenerationOutput,
    VideoGenerationOutput,
    GalleryGenerationOutput,
    CompareImagesGenerationOutput,
)

ALLOWED_SUPPRESSED_KEYS = frozenset({
    'type', 'generation_id', 'pipe_id', 'pipe_name', 'output_type', 'index',
    'temporary', 'is_final', 'file_type', 'artifact_type', 'artifact_data',
    'nsfw', 'content_flagged', 'preview_suppressed',
})


def _content_flags(output: GenerationOutput) -> Dict[str, bool]:
    return {
        'nsfw': bool(getattr(output, '_content_nsfw', False)),
        'content_flagged': bool(getattr(output, '_content_flagged', False)),
        'preview_suppressed': bool(getattr(output, '_preview_suppressed', False)),
    }


def _reduce_to_allowlist(message: Dict[str, Any]) -> Dict[str, Any]:
    kept = {key: value for key, value in message.items() if key in ALLOWED_SUPPRESSED_KEYS}
    if 'artifact_data' in kept:
        artifact = kept['artifact_data']
        label = artifact.get('label') if isinstance(artifact, dict) else None
        kept['artifact_data'] = {'label': label if isinstance(label, str) and '/api/' not in label else None}
    return kept


class GenerationOutputSerializer:
    """Centralized serializer for generation outputs to WebSocket messages."""

    def __init__(self, generation_id: str = None, preset_id: str = None):
        """
        Initialize the serializer.

        Args:
            generation_id: Current generation ID for organizing images
            preset_id: Current preset ID for image naming
        """
        self.generation_id = generation_id or generate_ulid()
        self.preset_id = preset_id

    def serialize_output(self, output: GenerationOutput) -> Dict[str, Any]:
        """Serialize a generation output to a WebSocket-compatible dictionary."""
        try:
            spec = output_type_registry.spec_for(output)
            message_type = spec.resolve_message_type(output) if spec else "generation_update"

            # Create base message structure with pipe tracking
            base_message = {
                'type': message_type,
                'generation_id': self.generation_id,
                'pipe_id': getattr(output, 'pipe_id', None),
                'pipe_name': getattr(output, 'pipe_name', None),
                'output_type': spec.key if spec else 'unknown',
            }

            # Add index field for artifact outputs if present
            if hasattr(output, 'index') and getattr(output, 'index', None) is not None:
                base_message['index'] = output.index

            # Merge type-specific payload, if a serializer is registered
            if spec is not None and spec.serializer is not None:
                ctx = SerializeContext(generation_id=self.generation_id, preset_id=self.preset_id)
                base_message.update(spec.serializer(output, ctx))

            if isinstance(output, _MEDIA_OUTPUTS) or hasattr(output, '_preview_suppressed'):
                flags = _content_flags(output)
                base_message.update(flags)
                if flags['preview_suppressed']:
                    base_message = _reduce_to_allowlist(base_message)

            return base_message

        except Exception as e:
            logger.error(f"Failed to serialize output {type(output).__name__}: {str(e)}")
            return {
                'type': 'generation_error',
                'generation_id': self.generation_id,
                'message': "Serialization failed"
            }
