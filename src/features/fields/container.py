import yaml
from pathlib import Path
from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Container(BaseField):
    """Container field for layout elements like tabs, tab, row, group, accordion"""

    def __init__(self, preset_loader, field_factory=None):
        super().__init__(preset_loader)
        self.field_factory = field_factory

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform container field data to frontend format"""
        field_info = self.get_field_info(field)

        # For accordion and group containers, never assign a name to prevent nested form state
        if field_info['type'] in ['accordion', 'group']:
            field_info['name'] = None

        schema = self.create_base_schema(field_info)

        # Process children for container fields
        schema['children'] = []
        children = self._get_children(field, preset_id)

        if self.field_factory:
            for child in children:
                child_schema = self.field_factory.map_field(child, preset_id)
                schema['children'].append(child_schema)

        # Add label for tabs, accordion, and group containers
        if field_info['type'] in ['tab', 'accordion', 'group']:
            schema['label'] = field_info['label']

        # Add configuration, processing templates and excluding 'file' if children were loaded from external file
        configuration = field_info.get('configuration', {})
        if configuration:
            # Process templates in configuration (e.g., {{ get_icon('input') }})
            processed_configuration = self._process_configuration_templates(
                configuration, preset_id
            )

            # Remove 'file' configuration if children were loaded from external file
            if 'file' in processed_configuration and schema['children']:
                processed_configuration = {k: v for k, v in processed_configuration.items() if k != 'file'}

            if processed_configuration:  # Only add if not empty
                schema['configuration'] = processed_configuration

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type in ['tabs', 'tab', 'row', 'group', 'accordion']

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    def _get_children(self, field, preset_id: str = None) -> List:
        """Get children from field regardless of format"""
        field_info = self.get_field_info(field)
        configuration = field_info.get('configuration', {})

        # Check if children should be loaded from external file
        if 'file' in configuration:
            external_children = self._load_children_from_file(configuration['file'], field_info.get('name'))
            if external_children:
                return external_children

        # Fall back to inline children
        if hasattr(field, 'children'):
            return field.children or []
        else:
            return field.get('children', [])

    def _load_children_from_file(self, file_config: Dict[str, Any], field_name: str = None) -> List:
        """Load children from external YAML file"""
        file_path = file_config.get('path')
        if not file_path:
            return []

        # Create context for template processing
        context = {
            'paths': {
                'preset': '',  # This will be empty for std files
            }
        }

        # Process the template to get the actual file path
        if self.field_factory and self.field_factory.template_processor:
            resolved_path = self.field_factory.template_processor.process_template(file_path, context)
        else:
            # Fallback: just use the path as-is if no template processor is available
            resolved_path = file_path

        try:
            file_path_obj = Path(resolved_path)
            if file_path_obj.exists():
                with open(file_path_obj, 'r', encoding='utf-8') as f:
                    file_data = yaml.safe_load(f)

                    # Return the loaded data as children
                    if isinstance(file_data, list):
                        return file_data

        except Exception as e:
            # Silently fail and return empty list
            pass

        return []

    def _process_configuration_templates(self, configuration: Dict[str, Any], preset_id: str) -> Dict[str, Any]:
        """Process Jinja2 templates in configuration values"""
        if not configuration:
            return configuration

        # Find preset to create template context
        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return configuration

        # Create context for template processing
        context = {
            'paths': {
                'preset': found_preset.path,
            }
        }

        # Process templates in configuration values
        processed_config = {}

        for key, value in configuration.items():
            if isinstance(value, str) and ('{{' in value and '}}' in value):
                # Process template string
                try:
                    if self.field_factory and self.field_factory.template_processor:
                        processed_value = self.field_factory.template_processor.process_template(value, context)
                        processed_config[key] = processed_value
                    else:
                        # If no template processor available, keep original value
                        processed_config[key] = value
                except Exception as e:
                    # If template processing fails, keep original value
                    processed_config[key] = value
            else:
                # Keep non-template values as-is
                processed_config[key] = value

        return processed_config

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="file",
                param_type=dict,
                default={},
                description="Load children from external YAML file",
                example={"path": "{{paths.preset}}/files/form/children.yml"}
            ),
            FieldConfigSpec(
                name="icon",
                param_type=str,
                default="",
                description="Icon identifier for the container (processed via templates)",
                example="{{ get_icon('input') }}"
            ),
            FieldConfigSpec(
                name="collapsible",
                param_type=bool,
                default=True,
                description="Whether accordion containers can be collapsed",
                example=True
            ),
            FieldConfigSpec(
                name="collapsed",
                param_type=bool,
                default=False,
                description="Whether accordion starts collapsed (closed) by default",
                example=True
            ),
            FieldConfigSpec(
                name="icon_display",
                param_type=str,
                default="",
                description="How a tab's icon and label are shown together",
                choices=["icon_only", "icon_label", "label"],
                example="icon_only"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - Container is a layout-only field with no validation"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Accordion",
                description="Simple accordion container with inline children",
                yaml_config="""type: accordion
label: Advanced Settings
children:
  - type: slider
    name: quality
    label: Quality
    configuration:
      min: 1
      max: 10
      step: 1""",
                rendered_output={
                    "type": "accordion",
                    "title": "Advanced Settings",
                    "children": []  # Would contain processed child fields
                },
                frontend_preview={
                    "type": "accordion",
                    "title": "Advanced Settings",
                    "label": "Advanced Settings",
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="Collapsed Accordion",
                description="Accordion that starts collapsed (closed) by default",
                yaml_config="""type: accordion
label: Advanced Settings
configuration:
  collapsed: true
  collapsible: true
children:
  - type: slider
    name: quality
    label: Quality""",
                rendered_output={
                    "type": "accordion",
                    "title": "Advanced Settings",
                    "configuration": {
                        "collapsed": True,
                        "collapsible": True
                    },
                    "children": []
                },
                frontend_preview={
                    "type": "accordion",
                    "title": "Advanced Settings",
                    "label": "Advanced Settings",
                    "configuration": {
                        "collapsed": True,
                        "collapsible": True
                    },
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="External File Container",
                description="Container loading children from external YAML file",
                yaml_config="""type: accordion
label: Camera Settings
configuration:
  file:
    path: "{{paths.preset}}/files/form/camera_fields.yml" """,
                rendered_output={
                    "type": "accordion",
                    "title": "Camera Settings",
                    "children": []  # Would contain children loaded from file
                },
                frontend_preview={
                    "type": "accordion",
                    "title": "Camera Settings",
                    "label": "Camera Settings",
                    "description": "Children loaded from external file",
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="Tabs Container",
                description="Tab container organizing fields into sections",
                yaml_config="""type: tabs
children:
  - type: tab
    label: Basic
    children:
      - type: select
        name: style
        label: Style
  - type: tab
    label: Advanced
    children:
      - type: slider
        name: strength
        label: Strength""",
                rendered_output={
                    "type": "tabs",
                    "children": []  # Would contain processed tab children
                },
                frontend_preview={
                    "type": "tabs",
                    "children": []
                }
            ),
        ]
