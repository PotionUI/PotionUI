"""RenderedPromptGenerationOutput is registered and serializes as a pipe_artifact.

Mirrors the seed artifact wiring: the rendered per-image prompt reaches the
frontend through the same output_type_registry mechanism, as a `pipe_artifact`
carrying `artifact_type: 'rendered_prompt'`.
"""

import src.features.generation.handlers.artifact_handlers  # noqa: F401  (registers specs)
from src.features.generation.output_types import output_type_registry, SerializeContext
from src.pipelines.outputs import RenderedPromptGenerationOutput


def _spec():
    output = RenderedPromptGenerationOutput(index=0, positive="p", negative="n")
    return output_type_registry.spec_for(output), output


class TestRegistration:
    def test_spec_is_registered(self):
        spec, _ = _spec()
        assert spec is not None
        assert spec.key == "rendered_prompt"

    def test_delivered_as_a_pipe_artifact(self):
        spec, output = _spec()
        assert spec.resolve_message_type(output) == "pipe_artifact"

    def test_has_a_transport_only_handler(self):
        spec, _ = _spec()
        assert spec.handler_cls is not None


class TestSerializer:
    def test_serializes_positive_negative_and_index(self):
        spec, output = _spec()
        payload = spec.serializer(output, SerializeContext(generation_id="gen1"))
        assert payload == {
            "artifact_type": "rendered_prompt",
            "artifact_data": {"index": 0, "positive": "p", "negative": "n"},
        }
