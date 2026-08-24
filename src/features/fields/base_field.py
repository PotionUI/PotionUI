from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class BaseField(ABC):
    """Base class for form fields that handle specific field types"""
    
    def __init__(self, preset_loader):
        self.preset_loader = preset_loader
    
    # Main interface methods at the top for readability
    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform field data from backend to frontend format
        Override this method to customize how data is presented to the frontend
        """
        return self.map_field(field, preset_id)
    
    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process and validate input data from frontend
        Override this method to handle incoming data transformation and validation
        
        Args:
            field_name: The name of the field
            value: The raw value from frontend
            validation_rules: Optional validation rules from field configuration
        
        Returns:
            The processed/validated value
            
        Raises:
            ValueError: If validation fails
        """
        # Default implementation just passes through the value
        return value
    
    # Dispatch is now driven by `FieldTypeRegistry` (src/platform/plugins/field_types.py),
    # not by scanning `can_handle` across an ordered list of field implementations.
    # `can_handle` is kept as a non-abstract, informational predicate - several
    # field classes still implement it and existing tests assert on it - but
    # `FieldFactory` no longer calls it to decide dispatch.
    def can_handle(self, field_type: str) -> bool:
        """Check if this field can handle the given field type (informational only)"""
        return False

    # Abstract methods
    @abstractmethod
    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map a field to its JSON schema representation"""
        pass

    # Documentation methods - override in subclasses for specific documentation
    @classmethod
    def description(cls) -> str:
        """Return field type description"""
        return cls.__doc__ or "No description available"

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return []

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        # Common validation rules available to all fields
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether the field is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="min_length",
                description="Minimum length for string values",
                param_type=int,
                example=5
            ),
            FieldValidationSpec(
                rule_name="max_length",
                description="Maximum length for string values",
                param_type=int,
                example=100
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return []

    # Helper methods
    def validate(self, value: Any, rules: Dict[str, Any]) -> List[str]:
        """Validate a value against given rules
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Common validation rules that can be extended by subclasses
        if rules.get('required') and not value:
            errors.append(f"This field is required")
        
        if 'min_length' in rules and len(str(value)) < rules['min_length']:
            errors.append(f"Minimum length is {rules['min_length']}")
        
        if 'max_length' in rules and len(str(value)) > rules['max_length']:
            errors.append(f"Maximum length is {rules['max_length']}")
        
        return errors
    
    def get_field_info(self, field):
        """Extract common field information regardless of field format"""
        if hasattr(field, 'type'):
            return {
                'type': field.type,
                'name': field.name,
                'label': field.label or field.name,
                'description': getattr(field, 'description', ''),
                'ai_hint': getattr(field, 'ai_hint', None),
                'required': field.required,
                'configuration': field.configuration or {},
                'validation': getattr(field, 'validation', {}),
                # Exact pass-through - no truthiness collapse. `value:` no
                # longer exists (see src/features/presets/templates.py FieldTemplate); `default`
                # is the one initializer key, and false/0/""/[] must survive.
                'default': getattr(field, 'default', None),
                'reactions': getattr(field, 'reactions', []),
                'audience': getattr(field, 'audience', None) or 'simple',
                # Admin per-field override lock (see FieldTemplate.readonly).
                'readonly': getattr(field, 'readonly', False),
                # Fractional row-weight (see FieldTemplate.width). Emitted
                # as-authored - no server-side normalization.
                'width': getattr(field, 'width', None),
                'full_width': getattr(field, 'full_width', False),
                'hidden_when_video_director': getattr(field, 'hidden_when_video_director', False),
            }
        else:
            return {
                'type': field.get('type'),
                'name': field.get('name'),
                'label': field.get('label', field.get('name')),
                'description': field.get('description', ''),
                'ai_hint': field.get('ai_hint'),
                'required': field.get('required', False),
                'configuration': field.get('configuration', {}),
                'validation': field.get('validation', {}),
                'default': field.get('default'),
                'reactions': field.get('reactions', []),
                'audience': field.get('audience') or 'simple',
                'readonly': field.get('readonly', False),
                'width': field.get('width'),
                'full_width': field.get('full_width', False),
                'hidden_when_video_director': field.get('hidden_when_video_director', False),
            }

    def create_base_schema(self, field_info: Dict[str, Any]) -> Dict[str, Any]:
        """Create base schema structure for a field"""
        schema = {
            'type': field_info['type'],
            'title': field_info['label'],
            'description': field_info['description'],
        }

        # Only include name if it's not None (important for container fields like accordion)
        if field_info['name'] is not None:
            schema['name'] = field_info['name']

        if field_info['default'] is not None:
            schema['default'] = field_info['default']

        if field_info.get('ai_hint'):
            schema['ai_hint'] = field_info['ai_hint']

        # Include reactions if present
        if field_info.get('reactions'):
            schema['reactions'] = field_info['reactions']

        # "simple" (default) vs "advanced" - always present so the frontend
        # never has to special-case a missing key.
        schema['audience'] = field_info.get('audience') or 'simple'

        # An admin per-field override locked this field (`editable: false`).
        if field_info.get('readonly'):
            schema['readonly'] = True

        # Emitted as-authored (string fraction stays a string, number stays a
        # number) - the frontend does its own parsing.
        if field_info.get('width') is not None:
            schema['width'] = field_info['width']

        if field_info.get('full_width'):
            schema['full_width'] = True

        # Only emitted when true - mirrors `full_width`'s pattern above, and
        # matches `audience`'s frontend contract (rendering-only, value stays
        # in formData either way).
        if field_info.get('hidden_when_video_director'):
            schema['hidden_when_video_director'] = True

        return schema
    
    def _find_preset_by_id(self, preset_id: str):
        """Find a preset template by its ID"""
        if not preset_id:
            return None

        for preset_template in self.preset_loader.presets:
            if preset_template.id == preset_id:
                return preset_template
        return None