"""
Handler/spec for ErrorGenerationOutput.

Emitted by GenerationEngine right before it re-raises an unhandled pipe
exception. There is no persistence step (the failure itself is recorded by
GenerationStatusTracker.transition via the orchestrator) - this spec exists
purely to give the output a WebSocket message_type so the frontend learns
about the failure in real time.
"""

import logging
from typing import Any, Dict

from src.pipelines.outputs import ErrorGenerationOutput, GenerationOutput
from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry

logger = logging.getLogger(__name__)


def serialize_error_output(output: ErrorGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    """Serialize ErrorGenerationOutput for generation_error messages."""
    payload = {
        'status': 'failed',
        'error': output.error,
    }
    if output.detail:
        payload['detail'] = output.detail
    return payload


output_type_registry.register(OutputTypeSpec(
    output_cls=ErrorGenerationOutput,
    key='error',
    message_type='generation_error',
    serializer=serialize_error_output,
    handler_cls=None,
))
