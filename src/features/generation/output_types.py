"""
Output type registry.

This module provides a single declaration point for each GenerationOutput type:
a stable string key, its handler class (for side-effect processing), its
WebSocket message type (or a callable that derives it from the output
instance), and its serializer function (for WebSocket payload construction).

Instead of scanning a linear list of handler classes (calling ``can_handle``
on each) or dispatching through a long ``isinstance`` chain, callers look up
the ``OutputTypeSpec`` for a given output once via ``spec_for`` and use its
fields directly. Plugins can extend the system by registering additional
specs on the shared ``output_type_registry`` singleton (e.g. via the
``output_type.register`` hook).
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, Union

from src.pipelines.outputs import GenerationOutput


class DuplicateOutputTypeError(ValueError):
    """Raised when registering an OutputTypeSpec whose key or output_cls already exists."""


@dataclass
class SerializeContext:
    """Context passed to serializer functions when building WebSocket messages."""
    generation_id: str
    preset_id: Optional[str] = None


@dataclass(frozen=True)
class OutputTypeSpec:
    """Declaration for a single GenerationOutput type."""
    output_cls: Type[GenerationOutput]
    key: str
    message_type: Union[str, Callable[[GenerationOutput], str]]
    serializer: Optional[Callable[[GenerationOutput, "SerializeContext"], Dict[str, Any]]]
    handler_cls: Optional[type] = None

    def resolve_message_type(self, output: GenerationOutput) -> str:
        """Resolve the message_type for a concrete output instance."""
        if callable(self.message_type):
            return self.message_type(output)
        return self.message_type


class OutputTypeRegistry:
    """Registry mapping GenerationOutput subclasses to their OutputTypeSpec."""

    def __init__(self):
        self._by_key: Dict[str, OutputTypeSpec] = {}
        self._by_cls: Dict[type, OutputTypeSpec] = {}

    def register(self, spec: OutputTypeSpec) -> None:
        """Register a new OutputTypeSpec. Raises on duplicate key or output_cls."""
        if spec.key in self._by_key:
            raise DuplicateOutputTypeError(f"Output type key already registered: '{spec.key}'")
        if spec.output_cls in self._by_cls:
            raise DuplicateOutputTypeError(
                f"Output class already registered: '{spec.output_cls.__name__}'"
            )
        self._by_key[spec.key] = spec
        self._by_cls[spec.output_cls] = spec

    def spec_for(self, output: GenerationOutput) -> Optional[OutputTypeSpec]:
        """
        Find the OutputTypeSpec for a given output instance.

        Tries an exact type match first, then walks the MRO of the output's
        class to find the first registered ancestor class.
        """
        output_cls = type(output)

        exact = self._by_cls.get(output_cls)
        if exact is not None:
            return exact

        for klass in output_cls.__mro__[1:]:
            spec = self._by_cls.get(klass)
            if spec is not None:
                return spec

        return None

    def all(self) -> List[OutputTypeSpec]:
        """Return all registered specs."""
        return list(self._by_key.values())


# Module-level singleton used across the application and by plugins.
output_type_registry = OutputTypeRegistry()
