"""Tests for output-types documenter."""
from dataclasses import dataclass, field
from typing import List

import pytest

from src.features.developer.output_types_documenter import OutputTypesDocumenter
from src.features.generation.output_types import OutputTypeRegistry, OutputTypeSpec
from src.pipelines.outputs import GenerationOutput


@dataclass
class _NoDocOutput(GenerationOutput):
    name: str
    weight: float = None


@dataclass
class _DocumentedOutput(GenerationOutput):
    """One line summarising what this output carries.

    A second paragraph with implementation detail nobody browsing the
    reference needs - it must not appear in the trimmed description.
    """
    label: str
    count: int = 0
    tags: List[str] = field(default_factory=list)
    child: "_NoDocOutput" = None


class TestOutputTypesDocumenter:
    """Test suite for OutputTypesDocumenter."""

    @pytest.fixture
    def registry(self):
        registry = OutputTypeRegistry()
        registry.register(OutputTypeSpec(
            output_cls=_NoDocOutput,
            key="no_doc",
            message_type="no_doc_update",
            serializer=lambda output, ctx: {},
            handler_cls=None,
        ))
        registry.register(OutputTypeSpec(
            output_cls=_DocumentedOutput,
            key="documented",
            message_type=lambda output: "dynamic_update",
            serializer=lambda output, ctx: {},
            handler_cls=object,
        ))
        return registry

    def test_initialization(self, registry):
        documenter = OutputTypesDocumenter(registry)
        assert documenter is not None
        assert documenter.output_type_registry is registry

    def test_generate_documentation_structure(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        assert result["total"] == 2
        assert len(result["output_types"]) == 2

    def test_missing_docstring_yields_empty_description_not_the_dataclass_signature(self, registry):
        """A dataclass with no docstring in its body still gets one from the
        `@dataclass` decorator (the constructor signature) - that must not
        leak through as if it were prose."""
        assert _NoDocOutput.__doc__ is not None
        assert _NoDocOutput.__doc__.startswith("_NoDocOutput(")

        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        no_doc = next(t for t in result["output_types"] if t["key"] == "no_doc")
        assert no_doc["output_class"] == "_NoDocOutput"
        assert no_doc["description"] == ""
        assert no_doc["message_type"] == "no_doc_update"
        assert no_doc["has_handler"] is False
        assert no_doc["has_serializer"] is True

    def test_docstring_is_trimmed_to_its_first_paragraph(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        documented = next(t for t in result["output_types"] if t["key"] == "documented")
        assert documented["description"] == "One line summarising what this output carries."
        assert "implementation detail" not in documented["description"]

    def test_callable_message_type_is_reported_as_dynamic(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        documented = next(t for t in result["output_types"] if t["key"] == "documented")
        assert documented["message_type"] == "<dynamic>"
        assert documented["has_handler"] is True

    def test_fields_carry_name_type_and_default(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        documented = next(t for t in result["output_types"] if t["key"] == "documented")
        fields_by_name = {f["name"]: f for f in documented["fields"]}

        assert fields_by_name["label"]["type"] == "str"
        assert fields_by_name["label"]["default"] is None

        assert fields_by_name["count"]["type"] == "int"
        assert fields_by_name["count"]["default"] == 0

        # A default_factory field reports the value the factory produces,
        # not the factory callable itself.
        assert fields_by_name["tags"]["default"] == []

    def test_inherited_fields_are_included(self, registry):
        """GenerationOutput's own pipe_id/pipe_name are dataclass fields too -
        they should show up alongside the subclass's own fields."""
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        no_doc = next(t for t in result["output_types"] if t["key"] == "no_doc")
        names = {f["name"] for f in no_doc["fields"]}
        assert {"pipe_id", "pipe_name", "name", "weight"} <= names

    def test_generic_field_type_resolves_to_a_bare_class_name(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        documented = next(t for t in result["output_types"] if t["key"] == "documented")
        fields_by_name = {f["name"]: f for f in documented["fields"]}

        assert fields_by_name["tags"]["type"] == "List[str]"

    def test_forward_ref_field_type_resolves_to_a_bare_class_name(self, registry):
        documenter = OutputTypesDocumenter(registry)
        result = documenter.generate_documentation()

        documented = next(t for t in result["output_types"] if t["key"] == "documented")
        fields_by_name = {f["name"]: f for f in documented["fields"]}

        assert fields_by_name["child"]["type"] == "_NoDocOutput"
