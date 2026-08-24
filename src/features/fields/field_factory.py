import inspect
from typing import Dict, Any, List, Optional

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldExampleSpec
from src.platform.plugins.field_types import FieldTypeRegistry, field_type_registry
from .container import Container


class DefaultField(BaseField):
    """Default field for fields that don't have specific implementations"""

    def can_handle(self, field_type: str) -> bool:
        return True  # This is the fallback field

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        return self.create_base_schema(field_info)

    @classmethod
    def description(cls) -> str:
        """Return field type description"""
        return "Fallback field that handles all unmatched field types with basic text input"

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Text Input (Default)",
                description="Any field type without specific implementation becomes text input",
                yaml_config="""type: custom_field_type
name: custom_input
label: Custom Input
default: Some value""",
                rendered_output={
                    "type": "custom_field_type",
                    "name": "custom_input",
                    "title": "Custom Input",
                    "default": "Some value"
                }
            ),
            FieldExampleSpec(
                title="Unsupported Field Type",
                description="Fields with unsupported types fall back to basic schema",
                yaml_config="""type: unsupported_type
name: mystery_field
label: Mystery Field
required: true""",
                rendered_output={
                    "type": "unsupported_type",
                    "name": "mystery_field",
                    "title": "Mystery Field",
                    "required": True
                }
            ),
        ]


class FieldFactory:
    """
    Factory for creating and managing form fields.

    Field-type dispatch is driven by a `FieldTypeRegistry` (one declaration per
    type: schema class, options provider, frontend component - see
    `src/platform/plugins/field_types.py`). By default this is the shared
    `field_type_registry` singleton, already populated with the core types; a
    caller can pass its own `field_registry` (e.g. tests, or once plugins can
    inject extra types) to override it.
    """

    def __init__(self, preset_loader, template_processor=None, field_registry: Optional[FieldTypeRegistry] = None):
        self.preset_loader = preset_loader
        self.template_processor = template_processor
        self.registry = field_registry or field_type_registry

        if not self.registry.all():
            # Deferred import - `builtin.py` imports the concrete field
            # classes under `src.features.fields`, which import this module at
            # package-init time; importing it at module load here would
            # create a circular import.
            from src.features.fields.builtin import register_builtin_fields
            register_builtin_fields(self.registry)

        self._default_field = DefaultField(self.preset_loader)
        self._instances: Dict[type, BaseField] = {}
        self._initialize_fields()

    def _initialize_fields(self):
        """
        Eagerly instantiate a field implementation for every distinct schema
        class currently registered, in registration order, with `DefaultField`
        last. Kept for backward compatibility - `FieldsDocumenter` iterates
        `self.fields` to generate field documentation.
        """
        self.fields: List[BaseField] = []
        for definition in self.registry.all():
            if definition.schema_cls is None:
                continue
            instance = self._get_or_create(definition.schema_cls)
            if instance not in self.fields:
                self.fields.append(instance)
        self.fields.append(self._default_field)

    def _get_or_create(self, schema_cls: type) -> BaseField:
        """Instantiate (and cache) a field implementation for `schema_cls`."""
        instance = self._instances.get(schema_cls)
        if instance is not None:
            return instance

        params = inspect.signature(schema_cls.__init__).parameters
        if 'field_factory' in params:
            instance = schema_cls(self.preset_loader, self)
        elif 'template_processor' in params:
            instance = schema_cls(self.preset_loader, self.template_processor)
        else:
            instance = schema_cls(self.preset_loader)

        self._instances[schema_cls] = instance
        return instance

    def _field_impl_for(self, field_type: str) -> BaseField:
        """Resolve the field implementation for `field_type` via the registry."""
        definition = self.registry.get(field_type)
        if definition.schema_cls is None:
            return self._default_field
        return self._get_or_create(definition.schema_cls)

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map a field using the appropriate field implementation"""
        field_type = self._get_field_type(field)
        return self._field_impl_for(field_type).output(field, preset_id)

    def _get_field_type(self, field) -> str:
        """Get field type regardless of field format"""
        if hasattr(field, 'type'):
            return field.type
        else:
            return field.get('type', 'unknown')
