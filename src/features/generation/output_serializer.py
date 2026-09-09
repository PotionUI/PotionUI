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

from src.pipelines.outputs import GenerationOutput
from src.features.generation.output_types import SerializeContext, output_type_registry

logger = logging.getLogger(__name__)


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

            return base_message

        except Exception as e:
            logger.error(f"Failed to serialize output {type(output).__name__}: {str(e)}")
            return {
                'type': 'generation_error',
                'generation_id': self.generation_id,
                'error': f"Serialization failed: {str(e)}"
            }
