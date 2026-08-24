"""
Tests for the OutputTypeRegistry / OutputTypeSpec declaration system.
"""

import pytest

from src.features.generation.output_types import (
    OutputTypeRegistry,
    OutputTypeSpec,
    SerializeContext,
    DuplicateOutputTypeError,
)
from src.pipelines.outputs import GenerationOutput


class BaseOutput(GenerationOutput):
    pass


class SubOutput(BaseOutput):
    pass


class OtherOutput(GenerationOutput):
    pass


class DummyHandler:
    def __init__(self, generation_id, user_id=None, settings_manager=None):
        self.generation_id = generation_id

    def handle(self, output):
        return {'handler': 'DummyHandler', 'processed': True}


@pytest.fixture
def registry():
    return OutputTypeRegistry()


class TestRegistration:
    def test_register_and_lookup(self, registry):
        spec = OutputTypeSpec(
            output_cls=OtherOutput,
            key='other',
            message_type='generation_update',
            serializer=None,
            handler_cls=DummyHandler,
        )
        registry.register(spec)

        found = registry.spec_for(OtherOutput())
        assert found is spec

    def test_duplicate_key_rejected(self, registry):
        registry.register(OutputTypeSpec(
            output_cls=BaseOutput, key='dup', message_type='x', serializer=None
        ))

        with pytest.raises(DuplicateOutputTypeError):
            registry.register(OutputTypeSpec(
                output_cls=OtherOutput, key='dup', message_type='y', serializer=None
            ))

    def test_duplicate_output_cls_rejected(self, registry):
        registry.register(OutputTypeSpec(
            output_cls=BaseOutput, key='a', message_type='x', serializer=None
        ))

        with pytest.raises(DuplicateOutputTypeError):
            registry.register(OutputTypeSpec(
                output_cls=BaseOutput, key='b', message_type='y', serializer=None
            ))

    def test_all_returns_registered_specs(self, registry):
        spec1 = OutputTypeSpec(output_cls=BaseOutput, key='a', message_type='x', serializer=None)
        spec2 = OutputTypeSpec(output_cls=OtherOutput, key='b', message_type='y', serializer=None)
        registry.register(spec1)
        registry.register(spec2)

        all_specs = registry.all()
        assert set(all_specs) == {spec1, spec2}


class TestSpecFor:
    def test_exact_match(self, registry):
        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=None)
        registry.register(spec)

        assert registry.spec_for(BaseOutput()) is spec

    def test_mro_fallback_for_subclass(self, registry):
        """A subclass instance resolves to its registered ancestor's spec."""
        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=None)
        registry.register(spec)

        found = registry.spec_for(SubOutput())
        assert found is spec

    def test_exact_match_preferred_over_ancestor(self, registry):
        base_spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=None)
        sub_spec = OutputTypeSpec(output_cls=SubOutput, key='sub', message_type='y', serializer=None)
        registry.register(base_spec)
        registry.register(sub_spec)

        assert registry.spec_for(SubOutput()) is sub_spec
        assert registry.spec_for(BaseOutput()) is base_spec

    def test_unregistered_type_returns_none(self, registry):
        assert registry.spec_for(OtherOutput()) is None


class TestMessageTypeResolution:
    def test_string_message_type(self, registry):
        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='workbench_update', serializer=None)
        assert spec.resolve_message_type(BaseOutput()) == 'workbench_update'

    def test_callable_message_type(self, registry):
        def resolver(output):
            return 'pipe_artifact' if getattr(output, 'isArtifact', False) else 'workbench_update'

        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type=resolver, serializer=None)

        plain = BaseOutput()
        assert spec.resolve_message_type(plain) == 'workbench_update'

        artifact = BaseOutput()
        artifact.isArtifact = True
        assert spec.resolve_message_type(artifact) == 'pipe_artifact'


class TestOptionalFields:
    def test_serializer_none_is_allowed(self, registry):
        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=None)
        registry.register(spec)

        found = registry.spec_for(BaseOutput())
        assert found.serializer is None

    def test_handler_cls_none_is_allowed(self, registry):
        spec = OutputTypeSpec(
            output_cls=BaseOutput, key='base', message_type='x', serializer=None, handler_cls=None
        )
        registry.register(spec)

        found = registry.spec_for(BaseOutput())
        assert found.handler_cls is None

    def test_handler_cls_defaults_to_none(self):
        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=None)
        assert spec.handler_cls is None

    def test_serializer_present_and_callable(self, registry):
        def serializer(output, ctx: SerializeContext):
            return {'generation_id': ctx.generation_id}

        spec = OutputTypeSpec(output_cls=BaseOutput, key='base', message_type='x', serializer=serializer)
        registry.register(spec)

        found = registry.spec_for(BaseOutput())
        ctx = SerializeContext(generation_id='gen1')
        assert found.serializer(BaseOutput(), ctx) == {'generation_id': 'gen1'}
