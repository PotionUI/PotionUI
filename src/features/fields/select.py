import yaml
import glob
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Select(BaseField):
    """Select field for dropdown selections with various data sources"""

    def __init__(self, preset_loader, template_processor=None):
        super().__init__(preset_loader)
        self.template_processor = template_processor

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform select field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        schema['options'] = self._get_select_options(field_info['configuration'], preset_id)
        schema['configuration'] = {
            'allow_empty': field_info['configuration'].get('allow_empty', False)
        }
        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'select'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="options",
                param_type=list,
                default=[],
                description="Static list of options with optional examples and sub_labels",
                example=[
                    {"label": "Low Angle", "value": "low_angle", "example": "camera low angle <subject>"},
                    {"label": "High Angle", "value": "high_angle", "example": "camera high angle looking down at <subject>"},
                    {"label": "Eye Level", "value": "eye_level", "example": "camera at eye level with <subject>"}
                ]
            ),
            FieldConfigSpec(
                name="file",
                param_type=dict,
                default={},
                description="Load options from a YAML file",
                example={"path": "{{paths.preset}}/files/form/options.yml"}
            ),
            FieldConfigSpec(
                name="files",
                param_type=dict,
                default={},
                description="Scan filesystem for files to use as options",
                example={
                    "in": "{{paths.preset}}/files/loras",
                    "recursive": True
                }
            ),
            FieldConfigSpec(
                name="allow_empty",
                param_type=bool,
                default=False,
                description="Allow empty/null selection option",
                example=True
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether a selection is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="allowed_values",
                description="List of allowed values (validated against options at runtime)",
                param_type=list,
                example=["low", "medium", "high"]
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Static Options",
                description="Simple dropdown with predefined options",
                yaml_config="""type: select
name: quality
label: Quality
configuration:
  options:
    - label: Low
      value: low
    - label: Medium
      value: medium
    - label: High
      value: high""",
                rendered_output={
                    "type": "select",
                    "name": "quality",
                    "title": "Quality",
                    "options": [
                        {"label": "Low", "value": "low"},
                        {"label": "Medium", "value": "medium"},
                        {"label": "High", "value": "high"}
                    ]
                },
                frontend_preview={
                    "type": "select",
                    "name": "preview_quality",
                    "title": "Quality",
                    "options": [
                        {"label": "Low", "value": "low"},
                        {"label": "Medium", "value": "medium"},
                        {"label": "High", "value": "high"}
                    ],
                    "default": "medium"
                }
            ),
            FieldExampleSpec(
                title="File-based Options",
                description="Load options from an external YAML file",
                yaml_config="""type: select
name: art_style
label: Art Style
configuration:
  file:
    path: "{{paths.preset}}/files/form/art_styles.yml" """,
                rendered_output={
                    "type": "select",
                    "name": "art_style",
                    "title": "Art Style",
                    "options": []  # Populated from file at runtime
                },
                frontend_preview={
                    "type": "select",
                    "name": "preview_art_style",
                    "title": "Art Style",
                    "description": "Options loaded from external file",
                    "options": [
                        {"label": "(Loaded from file)", "value": ""}
                    ]
                }
            ),
            FieldExampleSpec(
                title="Filesystem Scan",
                description="Scan directory for .safetensors files",
                yaml_config="""type: select
name: lora
label: LoRA Model
configuration:
  files:
    in: "{{paths.preset}}/files/loras"
    recursive: false""",
                rendered_output={
                    "type": "select",
                    "name": "lora",
                    "title": "LoRA Model",
                    "options": []  # Populated from filesystem scan at runtime
                },
                frontend_preview={
                    "type": "select",
                    "name": "preview_lora",
                    "title": "LoRA Model",
                    "description": "Options scanned from filesystem",
                    "options": [
                        {"label": "(Scanned from directory)", "value": ""}
                    ]
                }
            ),
            FieldExampleSpec(
                title="Select with Examples",
                description="Select field where each option has its own example text",
                yaml_config="""type: select
name: camera_arc
label: Camera Arc
configuration:
  options:
    - label: Low Angle
      value: low_angle
      example: "camera low angle <subject>"
    - label: High Angle
      value: high_angle
      example: "camera high angle looking down at <subject>"
    - label: Eye Level
      value: eye_level
      example: "camera at eye level with <subject>" """,
                rendered_output={
                    "type": "select",
                    "name": "camera_arc",
                    "title": "Camera Arc",
                    "options": [
                        {"label": "Low Angle", "value": "low_angle", "example": "camera low angle <subject>"},
                        {"label": "High Angle", "value": "high_angle", "example": "camera high angle looking down at <subject>"},
                        {"label": "Eye Level", "value": "eye_level", "example": "camera at eye level with <subject>"}
                    ]
                },
                frontend_preview={
                    "type": "select",
                    "name": "preview_camera_arc",
                    "title": "Camera Arc",
                    "options": [
                        {"label": "Low Angle", "value": "low_angle"},
                        {"label": "High Angle", "value": "high_angle"},
                        {"label": "Eye Level", "value": "eye_level"}
                    ],
                    "default": "eye_level"
                }
            ),
            FieldExampleSpec(
                title="Select with Empty Option",
                description="Select field that allows empty/null selection",
                yaml_config="""type: select
name: optional_style
label: Optional Style
configuration:
  allow_empty: true
  options:
    - label: Realistic
      value: realistic
    - label: Anime
      value: anime
    - label: Fantasy
      value: fantasy""",
                rendered_output={
                    "type": "select",
                    "name": "optional_style",
                    "title": "Optional Style",
                    "configuration": {
                        "allow_empty": True
                    },
                    "options": [
                        {"label": "Realistic", "value": "realistic"},
                        {"label": "Anime", "value": "anime"},
                        {"label": "Fantasy", "value": "fantasy"}
                    ]
                },
                frontend_preview={
                    "type": "select",
                    "name": "preview_optional_style",
                    "title": "Optional Style",
                    "configuration": {
                        "allow_empty": True
                    },
                    "options": [
                        {"label": "Realistic", "value": "realistic"},
                        {"label": "Anime", "value": "anime"},
                        {"label": "Fantasy", "value": "fantasy"}
                    ]
                }
            ),
        ]

    def _get_select_options(self, configuration: Dict[str, Any], preset_id: str = None) -> List[Dict[str, str]]:
        """Get select field options from various sources"""
        options = []

        # Static options
        options.extend(self._get_static_options(configuration))

        # File-based options
        if 'file' in configuration and preset_id:
            options.extend(self._get_file_options(configuration, preset_id))

        # File system scanning for files.in configuration
        if 'files' in configuration and preset_id:
            options.extend(self._get_filesystem_options(configuration, preset_id))

        return options

    def _get_static_options(self, configuration: Dict[str, Any]) -> List[Dict[str, str]]:
        """Get static options from configuration"""
        options = []

        for option in configuration.get('options', []):
            if isinstance(option, dict):
                opt_dict = {
                    'label': option.get('label', option.get('value')),
                    'value': option.get('value')
                }
                # Include example if present
                if 'example' in option:
                    opt_dict['example'] = option['example']
                if option.get('sub_label'):
                    opt_dict['sub_label'] = option['sub_label']
                options.append(opt_dict)
            else:
                options.append({
                    'label': str(option),
                    'value': str(option)
                })

        return options

    def _get_file_options(self, configuration: Dict[str, Any], preset_id: str) -> List[Dict[str, str]]:
        """Get options from a YAML file"""
        options = []
        file_path = configuration['file'].get('path')

        if not file_path:
            return options

        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return options

        # Create context for template processing
        context = {
            'paths': {
                'preset': found_preset.path,
                '_shared': str(self.preset_loader.shared_path),
            }
        }

        # Process the template to get the actual file path
        if self.template_processor:
            resolved_path = self.template_processor.process_template(file_path, context)
        else:
            # Fallback for when template_processor is not available
            resolved_path = file_path

        try:
            file_path_obj = Path(resolved_path)
            if file_path_obj.exists():
                with open(file_path_obj, 'r') as f:
                    file_data = yaml.safe_load(f)
                    options.extend(self._parse_yaml_options(file_data))
        except Exception as e:
            print(f"Error loading options from file {resolved_path}: {e}")

        return options

    def _get_filesystem_options(self, configuration: Dict[str, Any], preset_id: str) -> List[Dict[str, str]]:
        """Get options by scanning filesystem"""
        options = []
        files_config = configuration['files']
        directory = files_config.get('in')

        if not directory:
            return options

        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return options

        context = {
            'paths': {
                'preset': found_preset.path,
                '_shared': str(self.preset_loader.shared_path),
            }
        }

        # Process the template to get the actual directory path
        if self.template_processor:
            resolved_directory = self.template_processor.process_template(directory, context)
        else:
            # Fallback for when template_processor is not available
            resolved_directory = directory

        try:
            # Check if recursive scanning is enabled
            recursive = files_config.get('recursive', False)

            if recursive:
                # Scan recursively for .safetensors files
                pattern = resolved_directory + "/**/*.safetensors"
                files = glob.glob(pattern, recursive=True)
            else:
                # Scan for .safetensors files in immediate directory only
                pattern = os.path.join(resolved_directory, "*.safetensors")
                files = glob.glob(pattern)

            for file_path in files:
                filename = os.path.basename(file_path)
                name_without_ext = filename.replace('.safetensors', '')

                # If recursive, include relative path from base directory for context
                if recursive:
                    rel_path = os.path.relpath(file_path, resolved_directory)
                    label = os.path.splitext(rel_path)[0]  # Remove extension but keep path
                    options.append({
                        'label': label,
                        'value': rel_path
                    })
                else:
                    options.append({
                        'label': name_without_ext,
                        'value': filename
                    })
        except Exception as e:
            print(f"Error scanning directory {resolved_directory}: {e}")

        return options

    def _parse_yaml_options(self, file_data) -> List[Dict[str, str]]:
        """Parse options from YAML file data"""
        options = []

        if isinstance(file_data, list):
            # If the file contains a list of options
            for option in file_data:
                if isinstance(option, dict):
                    opt_dict = {
                        'label': option.get('label', option.get('value')),
                        'value': option.get('value')
                    }
                    # Include example if present
                    if 'example' in option:
                        opt_dict['example'] = option['example']
                    if option.get('sub_label'):
                        opt_dict['sub_label'] = option['sub_label']
                    options.append(opt_dict)
                else:
                    options.append({
                        'label': str(option),
                        'value': str(option)
                    })
        elif isinstance(file_data, dict):
            # If the file contains a dictionary with options
            for option in file_data.get('options', []):
                if isinstance(option, dict):
                    opt_dict = {
                        'label': option.get('label', option.get('value')),
                        'value': option.get('value')
                    }
                    # Include example if present
                    if 'example' in option:
                        opt_dict['example'] = option['example']
                    if option.get('sub_label'):
                        opt_dict['sub_label'] = option['sub_label']
                    options.append(opt_dict)
                else:
                    options.append({
                        'label': str(option),
                        'value': str(option)
                    })

        return options
