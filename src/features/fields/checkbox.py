from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Checkbox(BaseField):
    """Checkbox field for boolean values"""
    
    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform checkbox field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        
        # Checkbox fields are boolean type
        schema['type'] = 'boolean'
        
        return schema
    
    
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'checkbox'
    
    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts.

        Checkbox is a simple boolean field with no additional configuration options.
        """
        return [
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default="",
                description="Tooltip text displayed on hover",
                example="Enable this option to activate the feature"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether the checkbox must be checked (useful for terms acceptance)",
                param_type=bool,
                example=True
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Checkbox",
                description="Simple boolean checkbox field",
                yaml_config="""type: checkbox
name: enable_feature
label: Enable Feature
default: false""",
                rendered_output={
                    "type": "boolean",
                    "name": "enable_feature",
                    "title": "Enable Feature",
                    "default": False
                },
                frontend_preview={
                    "type": "checkbox",
                    "name": "preview_enable_feature",
                    "title": "Enable Feature",
                    "default": False
                }
            ),
            FieldExampleSpec(
                title="Required Checkbox",
                description="Checkbox that must be checked (e.g., terms acceptance)",
                yaml_config="""type: checkbox
name: accept_terms
label: I accept the terms and conditions
required: true""",
                rendered_output={
                    "type": "boolean",
                    "name": "accept_terms",
                    "title": "I accept the terms and conditions",
                    "default": None
                },
                frontend_preview={
                    "type": "checkbox",
                    "name": "preview_accept_terms",
                    "title": "I accept the terms and conditions",
                    "default": False
                }
            ),
        ]