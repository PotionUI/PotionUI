from typing import Any, Dict, List, Optional

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldExampleSpec, FieldValidationSpec
from .tool_values import coerce_field_value


class NumberBox(BaseField):
    @classmethod
    def description(cls) -> str:
        return "Compact number box that accepts typed digits and may be left blank"

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info['configuration']

        schema['integer'] = config.get('integer') is True
        for key, target in (('min', 'minimum'), ('max', 'maximum')):
            bound = config.get(key)
            if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                schema[target] = bound
        if isinstance(config.get('unit'), str) and config['unit']:
            schema['unit'] = config['unit']
        if isinstance(config.get('placeholder'), str):
            schema['placeholder'] = config['placeholder']
        return schema

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        rules = validation_rules or {}
        param = {"type": "integer" if rules.get('integer') is True else "float"}
        for key in ('min', 'max', 'unit'):
            if rules.get(key) is not None:
                param[key] = rules[key]
        return coerce_field_value(param, value)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="integer",
                param_type=bool,
                default=False,
                description="Round what is typed to a whole number and refuse fractions on submission",
                example=True
            ),
            FieldConfigSpec(
                name="min",
                param_type=float,
                default=None,
                description="Smallest accepted value; a lower one is flagged under the box",
                example=0
            ),
            FieldConfigSpec(
                name="max",
                param_type=float,
                default=None,
                description="Largest accepted value; a higher one is flagged under the box",
                example=4096
            ),
            FieldConfigSpec(
                name="unit",
                param_type=str,
                default=None,
                description="Suffix drawn inside the box, such as `px`",
                example="px"
            ),
            FieldConfigSpec(
                name="placeholder",
                param_type=str,
                default="Auto",
                description="Text shown while the box is blank",
                example="Auto"
            ),
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default=None,
                description="Help text shown in an info tooltip next to the label",
                example="Width of the crop box"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether the box may be left blank",
                param_type=bool,
                example=True
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Crop Width",
                description="A whole-number box in pixels that may be left blank",
                yaml_config="""type: number_box
name: width
label: Width
configuration:
  integer: true
  min: 1
  max: 8192
  unit: px""",
                rendered_output={
                    "type": "number_box",
                    "name": "width",
                    "title": "Width",
                    "integer": True,
                    "minimum": 1,
                    "maximum": 8192,
                    "unit": "px"
                },
                frontend_preview={
                    "type": "number_box",
                    "name": "preview_width",
                    "title": "Width",
                    "integer": True,
                    "minimum": 1,
                    "maximum": 8192,
                    "unit": "px"
                }
            ),
        ]
