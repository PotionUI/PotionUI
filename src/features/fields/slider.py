from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Slider(BaseField):
    """Slider field for numeric input with min/max ranges"""
    
    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform slider field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        config = field_info['configuration']
        schema['minimum'] = config.get('min', 0)
        schema['maximum'] = config.get('max', 100)
        schema['step'] = config.get('step', 1)

        # Pass through tooltip if present in configuration
        if config.get('tooltip'):
            schema['tooltip'] = config['tooltip']

        # Also preserve the configuration object with tooltip for frontend access
        schema['configuration'] = config

        return schema
    
    
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'slider'
    
    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="min",
                param_type=float,
                default=0,
                description="Minimum value for the slider",
                example=0
            ),
            FieldConfigSpec(
                name="max",
                param_type=float,
                default=100,
                description="Maximum value for the slider",
                example=100
            ),
            FieldConfigSpec(
                name="step",
                param_type=float,
                default=1,
                description="Step increment for the slider",
                example=0.1
            ),
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default="",
                description="Tooltip text displayed next to the slider value",
                example="Adjust the strength of the effect"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        slider_rules = [
            FieldValidationSpec(
                rule_name="min",
                description="Minimum allowed value",
                param_type=float,
                example=0
            ),
            FieldValidationSpec(
                rule_name="max",
                description="Maximum allowed value",
                param_type=float,
                example=100
            ),
        ]
        return base_rules + slider_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Slider",
                description="Simple numeric slider with range 0-100",
                yaml_config="""type: slider
name: strength
label: Strength
configuration:
  min: 0
  max: 100
  step: 1
default: 50""",
                rendered_output={
                    "type": "slider",
                    "name": "strength",
                    "title": "Strength",
                    "minimum": 0,
                    "maximum": 100,
                    "step": 1,
                    "default": 50
                },
                frontend_preview={
                    "type": "slider",
                    "name": "preview_strength",
                    "title": "Strength",
                    "minimum": 0,
                    "maximum": 100,
                    "step": 1,
                    "default": 50
                }
            ),
            FieldExampleSpec(
                title="Decimal Slider",
                description="Slider with decimal values for fine control",
                yaml_config="""type: slider
name: cfg_scale
label: CFG Scale
configuration:
  min: 1.0
  max: 20.0
  step: 0.5
default: 7.5""",
                rendered_output={
                    "type": "slider",
                    "name": "cfg_scale",
                    "title": "CFG Scale",
                    "minimum": 1.0,
                    "maximum": 20.0,
                    "step": 0.5,
                    "default": 7.5
                },
                frontend_preview={
                    "type": "slider",
                    "name": "preview_cfg_scale",
                    "title": "CFG Scale",
                    "minimum": 1.0,
                    "maximum": 20.0,
                    "step": 0.5,
                    "default": 7.5
                }
            ),
        ]