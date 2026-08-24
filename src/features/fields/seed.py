from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Seed(BaseField):
    """Seed field for numeric input with seed generation functionality"""

    # Maximum seed value (2^32 - 1, same as in generate_seed function)
    MAX_SEED_VALUE = 2**32 - 1

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform seed field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        config = field_info['configuration']
        schema['minimum'] = config.get('min', -1)
        schema['maximum'] = config.get('max', self.MAX_SEED_VALUE)
        schema['step'] = config.get('step', 1)

        # Add seed-specific properties
        schema['is_seed_field'] = True

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'seed'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="min",
                param_type=int,
                default=-1,
                description="Minimum value for the seed (-1 for backend random)",
                example=-1
            ),
            FieldConfigSpec(
                name="max",
                param_type=int,
                default=cls.MAX_SEED_VALUE,
                description="Maximum value for the seed",
                example=cls.MAX_SEED_VALUE
            ),
            FieldConfigSpec(
                name="step",
                param_type=int,
                default=1,
                description="Step increment for the seed input",
                example=1
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        seed_rules = [
            FieldValidationSpec(
                rule_name="min",
                description="Minimum allowed seed value",
                param_type=int,
                example=-1
            ),
            FieldValidationSpec(
                rule_name="max",
                description="Maximum allowed seed value",
                param_type=int,
                example=cls.MAX_SEED_VALUE
            ),
        ]
        return base_rules + seed_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Seed Field",
                description="Standard seed input with randomize functionality",
                yaml_config="""type: seed
name: seed
label: Seed
configuration:
  min: -1
  max: 4294967295
  step: 1
default: -1""",
                rendered_output={
                    "type": "seed",
                    "name": "seed",
                    "title": "Seed",
                    "minimum": -1,
                    "maximum": cls.MAX_SEED_VALUE,
                    "step": 1,
                    "default": -1,
                    "is_seed_field": True
                },
                frontend_preview={
                    "type": "seed",
                    "name": "preview_seed",
                    "title": "Seed",
                    "minimum": -1,
                    "maximum": 4294967295,
                    "step": 1,
                    "default": -1,
                    "is_seed_field": True
                }
            ),
            FieldExampleSpec(
                title="Custom Range Seed",
                description="Seed field with custom min/max range",
                yaml_config=f"""type: seed
name: custom_seed
label: Custom Seed
configuration:
  min: 0
  max: 1000000
  step: 1
default: 12345""",
                rendered_output={
                    "type": "seed",
                    "name": "custom_seed",
                    "title": "Custom Seed",
                    "minimum": 0,
                    "maximum": 1000000,
                    "step": 1,
                    "default": 12345,
                    "is_seed_field": True
                },
                frontend_preview={
                    "type": "seed",
                    "name": "preview_custom_seed",
                    "title": "Custom Seed",
                    "minimum": 0,
                    "maximum": 1000000,
                    "step": 1,
                    "default": 12345,
                    "is_seed_field": True
                }
            ),
        ]