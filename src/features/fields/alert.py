from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Alert(BaseField):
    """Alert field for displaying informational messages with HeroUI alert styles"""

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'alert'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Alert-specific configuration
        config = field_info['configuration']

        # Set alert variant (color/style)
        schema['variant'] = config.get('variant', 'default')

        # Set alert title
        if 'title' in config:
            schema['alertTitle'] = config['title']

        # Set alert message/content
        schema['content'] = config.get('content', field_info['description'] or '')

        return schema


    @classmethod
    def description(cls) -> str:
        return "Alert field for displaying informational messages with various HeroUI alert styles and configurations"

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="variant",
                param_type=str,
                default="default",
                description="Alert variant/color scheme",
                choices=["default", "primary", "secondary", "success", "warning", "danger"],
                example="success"
            ),
            FieldConfigSpec(
                name="title",
                param_type=str,
                default="",
                description="Alert title text",
                example="Important Notice"
            ),
            FieldConfigSpec(
                name="content",
                param_type=str,
                default="",
                description="Alert message content",
                example="This is an important message for the user."
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - Alert is a display-only field with no validation"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Info Alert",
                description="Simple informational alert with default styling",
                yaml_config="""type: alert
name: info_alert
label: Information
configuration:
  variant: default
  title: Information
  content: This is some helpful information for the user.""",
                rendered_output={
                    "type": "alert",
                    "name": "info_alert",
                    "title": "Information",
                    "variant": "default",
                    "alertTitle": "Information",
                    "content": "This is some helpful information for the user."
                },
                frontend_preview={
                    "type": "alert",
                    "name": "preview_info_alert",
                    "title": "Information",
                    "variant": "default",
                    "alertTitle": "Information",
                    "content": "This is some helpful information for the user."
                }
            ),
            FieldExampleSpec(
                title="Success Alert",
                description="Success alert confirming a completed operation",
                yaml_config="""type: alert
name: success_alert
label: Success Message
configuration:
  variant: success
  title: Success!
  content: Your operation completed successfully.""",
                rendered_output={
                    "type": "alert",
                    "name": "success_alert",
                    "title": "Success Message",
                    "variant": "success",
                    "alertTitle": "Success!",
                    "content": "Your operation completed successfully."
                },
                frontend_preview={
                    "type": "alert",
                    "name": "preview_success_alert",
                    "title": "Success Message",
                    "variant": "success",
                    "alertTitle": "Success!",
                    "content": "Your operation completed successfully."
                }
            ),
            FieldExampleSpec(
                title="Warning Alert",
                description="Warning alert calling out a setting to be careful with",
                yaml_config="""type: alert
name: warning_alert
label: Warning
configuration:
  variant: warning
  title: Warning
  content: Please be careful with this setting.""",
                rendered_output={
                    "type": "alert",
                    "name": "warning_alert",
                    "title": "Warning",
                    "variant": "warning",
                    "alertTitle": "Warning",
                    "content": "Please be careful with this setting."
                },
                frontend_preview={
                    "type": "alert",
                    "name": "preview_warning_alert",
                    "title": "Warning",
                    "variant": "warning",
                    "alertTitle": "Warning",
                    "content": "Please be careful with this setting."
                }
            ),
        ]