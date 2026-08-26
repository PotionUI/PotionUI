"""
Field type registry.

This module provides a single declaration point for each form field type: its
canonical type name, the backend `BaseField` subclass that maps/validates it,
an optional dynamic-options provider, and the frontend component that renders
it. It replaces the four divergent hardcoded field-type tables that used to
exist across `FieldFactory`'s ordered `can_handle` scan, `FormManager`'s
`if/elif` chain, `FieldsDocumenter`'s name-mapping dict, and the frontend's
own `FormField.svelte` branches (frontend still branches for now - A4 wires
the frontend registry against `frontend_manifest()`).

Plugins extend the system by registering additional `FieldTypeDefinition`s on
the shared `field_type_registry` singleton when they are enabled, and by
having them removed via `unregister_source` when disabled.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type


class DuplicateFieldTypeError(ValueError):
    """Raised when registering a field type name that is already registered."""


@dataclass(frozen=True)
class FieldTypeDefinition:
    """Declaration for a single form field type."""

    type_name: str
    schema_cls: Optional[type] = None
    options_provider: Optional[Callable[[Dict[str, Any]], Any]] = None
    frontend_component: str = ""
    container: bool = False
    source: str = "core"
    # Default-deny: a field type's value is only eligible for the Inspirations
    # allowlist snapshot (src/features/inspirations/operations/publishing.py) when this is
    # explicitly True. Plain values (text/number/select/...) and public
    # identifiers (model/LoRA refs) opt in; anything carrying a file path,
    # upload, or other user-storage reference must stay False.
    shareable: bool = False


class FieldTypeRegistry:
    """Registry mapping field type name -> `FieldTypeDefinition`."""

    def __init__(self):
        self._by_type: Dict[str, FieldTypeDefinition] = {}
        # Returned by `get()` for any type name that was never registered -
        # `get()` never returns None so callers can always dispatch safely.
        self._default = FieldTypeDefinition(
            type_name="__default__",
            schema_cls=None,
            options_provider=None,
            frontend_component="core:TextInput",
            container=False,
            source="core",
        )

    def register(self, definition: FieldTypeDefinition) -> None:
        """Register a field type. Raises `DuplicateFieldTypeError` on name collision."""
        if definition.type_name in self._by_type:
            raise DuplicateFieldTypeError(
                f"Field type already registered: '{definition.type_name}'"
            )
        self._by_type[definition.type_name] = definition

    def unregister_source(self, source: str) -> None:
        """Remove every field type definition registered by `source` (e.g. a plugin id)."""
        for type_name in [
            type_name for type_name, defn in self._by_type.items() if defn.source == source
        ]:
            del self._by_type[type_name]

    def get(self, type_name: str) -> FieldTypeDefinition:
        """Look up a field type. Unknown type names resolve to the default definition."""
        return self._by_type.get(type_name, self._default)

    def all(self) -> List[FieldTypeDefinition]:
        """Return every registered field type definition."""
        return list(self._by_type.values())

    def frontend_manifest(self) -> List[Dict[str, Any]]:
        """Serialize the registry for the `/api/fields/types` endpoint."""
        return [
            {
                "type": defn.type_name,
                "component": defn.frontend_component,
                "has_options": defn.options_provider is not None,
                "container": defn.container,
                "source": defn.source,
                "configuration_schema": self._configuration_schema(defn.schema_cls),
            }
            for defn in self.all()
        ]

    @staticmethod
    def _configuration_schema(schema_cls: Optional[type]) -> List[Dict[str, Any]]:
        """Self-description of `schema_cls.configuration()` (a list of
        `src.features.fields.specs.FieldConfigSpec`), so a frontend field
        component - e.g. MediaLoaderField's `accepted_types`/`max_resolution`/
        duration limits - can discover and client-side-enforce a field type's
        declarative configuration surface without the backend and frontend
        hardcoding the same key names twice.

        Duck-typed rather than importing `FieldConfigSpec`: this module lives
        in `src.platform`, which must not import `src.features` (layering).
        """
        if schema_cls is None or not hasattr(schema_cls, "configuration"):
            return []
        try:
            specs = schema_cls.configuration()
        except Exception:
            return []
        return [
            {
                "name": spec.name,
                "param_type": spec.param_type.__name__ if isinstance(spec.param_type, type) else str(spec.param_type),
                "default": spec.default,
                "description": spec.description,
                "required": spec.required,
                "choices": spec.choices,
                "example": spec.example,
            }
            for spec in specs
        ]


# Module-level singleton shared by FieldFactory, src.features.forms.operations,
# and the plugin enable/disable path - mirrors `output_type_registry` in
# `src.features.generation.output_types`.
field_type_registry = FieldTypeRegistry()
