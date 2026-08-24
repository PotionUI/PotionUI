from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Header(BaseField):
    """Header field for visual organization - displays a title with no input value"""

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform header field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Header fields are theme/display only
        schema['type'] = 'header'

        # Headers don't store values, so remove name if present
        schema.pop('name', None)

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'header'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return configuration parameters for header field"""
        return [
            FieldConfigSpec(
                name="variant",
                param_type=str,
                default="h3",
                description="HTML heading level (h1, h2, h3, h4, h5, h6)",
                example="h2"
            ),
            FieldConfigSpec(
                name="style",
                param_type=dict,
                default={},
                description="Custom CSS styles for the header",
                example={"color": "#333", "fontWeight": "bold"}
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - Header is a display-only field with no validation"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Header",
                description="Simple section header",
                yaml_config="""type: header
label: Advanced Settings""",
                rendered_output={
                    "type": "header",
                    "title": "Advanced Settings"
                },
                frontend_preview={
                    "type": "header",
                    "title": "Advanced Settings"
                }
            ),
            FieldExampleSpec(
                title="Header with Variant",
                description="Header with specific heading level",
                yaml_config="""type: header
label: Image Generation Settings
configuration:
  variant: h2""",
                rendered_output={
                    "type": "header",
                    "title": "Image Generation Settings",
                    "configuration": {
                        "variant": "h2"
                    }
                },
                frontend_preview={
                    "type": "header",
                    "title": "Image Generation Settings",
                    "configuration": {
                        "variant": "h2"
                    }
                }
            ),
            FieldExampleSpec(
                title="Styled Header",
                description="Header with custom styling",
                yaml_config="""type: header
label: Quality Settings
configuration:
  variant: h4
  style:
    color: "#0066cc"
    fontWeight: "bold"
    marginTop: "20px" """,
                rendered_output={
                    "type": "header",
                    "title": "Quality Settings",
                    "configuration": {
                        "variant": "h4",
                        "style": {
                            "color": "#0066cc",
                            "fontWeight": "bold",
                            "marginTop": "20px"
                        }
                    }
                },
                frontend_preview={
                    "type": "header",
                    "title": "Quality Settings",
                    "configuration": {
                        "variant": "h4"
                    }
                }
            ),
        ]
