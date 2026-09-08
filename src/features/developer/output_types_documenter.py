"""Output-type documentation generator."""
import dataclasses
import inspect
import typing
from typing import Any, Dict


def _first_paragraph(doc: str) -> str:
    """First paragraph of a docstring, dedented and folded to one line."""
    if not doc:
        return ""
    first = inspect.cleandoc(doc).split("\n\n", 1)[0]
    return " ".join(line.strip() for line in first.splitlines()).strip()


def _own_docstring(output_cls: type) -> str:
    """The class's own docstring, ignoring `@dataclass`'s auto-generated one.

    A dataclass with no docstring in its body gets `__doc__` filled in by the
    decorator with its constructor signature (``"ClassName(field: type, ...)"``)
    - most of `outputs.py`'s classes carry no docstring, so surfacing
    `__doc__` unfiltered would show that signature as if it were prose.
    """
    doc = output_cls.__doc__
    if not doc or doc.startswith(f"{output_cls.__qualname__}("):
        return ""
    return doc


def _type_name(annotation: Any) -> str:
    """Human-readable name for a dataclass field's annotation.

    ``annotation`` is a real object here (outputs.py carries no
    ``from __future__ import annotations``), so a generic like
    ``List[ImageGenerationOutput]`` arrives as a ``typing.List`` with the
    fully-qualified class as its argument, and a same-module forward
    reference (``List["AudioGenerationOutput"]``) arrives as a
    ``ForwardRef``. Both are unwrapped to their bare class name.
    """
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, typing.ForwardRef):
        return annotation.__forward_arg__

    origin = typing.get_origin(annotation)
    if origin is not None:
        args = ", ".join(_type_name(arg) for arg in typing.get_args(annotation))
        origin_name = getattr(origin, "__name__", str(origin)).title()
        return f"{origin_name}[{args}]" if args else origin_name

    name = getattr(annotation, "__name__", None)
    return name if name else str(annotation)


class OutputTypesDocumenter:
    """Generates documentation for every registered GenerationOutput type."""

    def __init__(self, output_type_registry):
        self.output_type_registry = output_type_registry

    @staticmethod
    def _field_default(f: "dataclasses.Field") -> Any:
        if f.default is not dataclasses.MISSING:
            return f.default
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            try:
                return f.default_factory()
            except Exception:
                return None
        return None

    def _document_output_type(self, spec) -> Dict[str, Any]:
        output_cls = spec.output_cls

        fields = [
            {
                "name": f.name,
                "type": _type_name(f.type),
                "default": self._field_default(f),
            }
            for f in dataclasses.fields(output_cls)
        ]

        return {
            "key": spec.key,
            "output_class": output_cls.__name__,
            "message_type": (
                spec.message_type if isinstance(spec.message_type, str) else "<dynamic>"
            ),
            "has_handler": spec.handler_cls is not None,
            "has_serializer": spec.serializer is not None,
            "description": _first_paragraph(_own_docstring(output_cls)),
            "fields": fields,
        }

    def generate_documentation(self) -> Dict[str, Any]:
        """Generate documentation for all registered output types.

        Returns:
            Dict with 'output_types' list and 'total' count
        """
        specs = self.output_type_registry.all()
        output_types = [self._document_output_type(spec) for spec in specs]

        return {
            "output_types": output_types,
            "total": len(output_types),
        }
