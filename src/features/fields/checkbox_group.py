from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class CheckboxGroup(BaseField):
    """Checkbox group field for multiple selections"""

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform checkbox group field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        schema['items'] = {'type': 'string'}
        schema['options'] = self._get_checkbox_options(field_info['configuration'])
        schema['layout'] = field_info['configuration'].get('layout', 'vertical')

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'checkbox_group'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    def _get_checkbox_options(self, configuration: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get checkbox group options"""
        return configuration.get('options', [])

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="options",
                param_type=list,
                default=[],
                description="List of available checkbox options",
                example=[
                    {"label": "Option 1", "value": "opt1"},
                    {"label": "Option 2", "value": "opt2"},
                    {"label": "Option 3", "value": "opt3"}
                ]
            ),
            FieldConfigSpec(
                name="layout",
                param_type=str,
                default="vertical",
                description="Layout direction for checkboxes",
                choices=["vertical", "horizontal"],
                example="horizontal"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        checkbox_group_rules = [
            FieldValidationSpec(
                rule_name="min_selections",
                description="Minimum number of items that must be selected",
                param_type=int,
                example=2
            ),
            FieldValidationSpec(
                rule_name="max_selections",
                description="Maximum number of items that can be selected",
                param_type=int,
                example=5
            ),
        ]
        return base_rules + checkbox_group_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Checkbox Group",
                description="Multiple selection checkbox group",
                yaml_config="""type: checkbox_group
name: features
label: Select Features
configuration:
  options:
    - label: High Quality
      value: hq
    - label: Fast Processing
      value: fast
    - label: Low Memory Usage
      value: low_mem""",
                rendered_output={
                    "type": "checkbox_group",
                    "name": "features",
                    "title": "Select Features",
                    "items": {"type": "string"},
                    "options": [
                        {"label": "High Quality", "value": "hq"},
                        {"label": "Fast Processing", "value": "fast"},
                        {"label": "Low Memory Usage", "value": "low_mem"}
                    ]
                },
                frontend_preview={
                    "type": "checkbox_group",
                    "name": "preview_features",
                    "title": "Select Features",
                    "options": [
                        {"label": "High Quality", "value": "hq"},
                        {"label": "Fast Processing", "value": "fast"},
                        {"label": "Low Memory Usage", "value": "low_mem"}
                    ],
                    "default": ["hq"]
                }
            ),
            FieldExampleSpec(
                title="Horizontal Layout",
                description="Checkbox group with horizontal layout",
                yaml_config="""type: checkbox_group
name: formats
label: Output Formats
configuration:
  layout: horizontal
  options:
    - label: PNG
      value: png
    - label: JPG
      value: jpg
    - label: WEBP
      value: webp""",
                rendered_output={
                    "type": "checkbox_group",
                    "name": "formats",
                    "title": "Output Formats",
                    "items": {"type": "string"},
                    "options": [
                        {"label": "PNG", "value": "png"},
                        {"label": "JPG", "value": "jpg"},
                        {"label": "WEBP", "value": "webp"}
                    ]
                },
                frontend_preview={
                    "type": "checkbox_group",
                    "name": "preview_formats",
                    "title": "Output Formats",
                    "layout": "horizontal",
                    "options": [
                        {"label": "PNG", "value": "png"},
                        {"label": "JPG", "value": "jpg"},
                        {"label": "WEBP", "value": "webp"}
                    ],
                    "default": ["png"]
                }
            ),
        ]
