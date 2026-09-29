from dataclasses import dataclass
from typing import Any, Dict

from src.features.generation.output_types import OutputTypeSpec, SerializeContext, output_type_registry
from src.pipelines.outputs import GenerationOutput


@dataclass(kw_only=True)
class ContentBlockedGenerationOutput(GenerationOutput):
    blocked_count: int
    total: int


def serialize_content_blocked_output(output: ContentBlockedGenerationOutput, ctx: SerializeContext) -> Dict[str, Any]:
    return {'blocked_count': output.blocked_count, 'total': output.total}


output_type_registry.register(OutputTypeSpec(
    output_cls=ContentBlockedGenerationOutput,
    key='content_blocked',
    message_type='content_blocked',
    serializer=serialize_content_blocked_output,
))
