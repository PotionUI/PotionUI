from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Stepper(BaseField):
    """Compact -/+ stepper for small-range numeric fields (e.g. batch count).

    Numeric semantics and config keys (`min`/`max`/`step`) match `Slider` -
    only the frontend widget differs: a bordered [-][value][+] control
    instead of a range track.
    """

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        config = field_info['configuration']
        schema['minimum'] = config.get('min', 0)
        schema['maximum'] = config.get('max', 100)
        schema['step'] = config.get('step', 1)

        if config.get('tooltip'):
            schema['tooltip'] = config['tooltip']

        schema['configuration'] = config

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'stepper'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="min",
                param_type=float,
                default=0,
                description="Minimum value for the stepper",
                example=1
            ),
            FieldConfigSpec(
                name="max",
                param_type=float,
                default=100,
                description="Maximum value for the stepper",
                example=10
            ),
            FieldConfigSpec(
                name="step",
                param_type=float,
                default=1,
                description="Step increment for the stepper",
                example=1
            ),
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default="",
                description="Tooltip text displayed next to the stepper value",
                example="Number of images to generate"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        base_rules = super().validation_rules()
        stepper_rules = [
            FieldValidationSpec(
                rule_name="min",
                description="Minimum allowed value",
                param_type=float,
                example=1
            ),
            FieldValidationSpec(
                rule_name="max",
                description="Maximum allowed value",
                param_type=float,
                example=10
            ),
        ]
        return base_rules + stepper_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Compact Stepper",
                description="Small integer range rendered as a [-][value][+] control",
                yaml_config="""type: stepper
name: quantity
label: Images
configuration:
  min: 1
  max: 10
  step: 1
default: 1""",
                rendered_output={
                    "type": "stepper",
                    "name": "quantity",
                    "title": "Images",
                    "minimum": 1,
                    "maximum": 10,
                    "step": 1,
                    "default": 1
                },
                frontend_preview={
                    "type": "stepper",
                    "name": "preview_quantity",
                    "title": "Images",
                    "minimum": 1,
                    "maximum": 10,
                    "step": 1,
                    "default": 1
                }
            ),
        ]
