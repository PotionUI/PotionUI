import yaml
from pathlib import Path
from math import gcd
from typing import Dict, Any, List, Optional

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Resolution(BaseField):
    """Resolution field for width x height selections with structured output"""

    def __init__(self, preset_loader, template_processor=None):
        super().__init__(preset_loader)
        self.template_processor = template_processor

    def _compute_ratio(self, value_str: str) -> Optional[List[int]]:
        """Parse 'WIDTHxHEIGHT' and return simplified [w, h] ratio"""
        try:
            parts = value_str.split('x')
            if len(parts) != 2:
                return None
            width = int(parts[0].strip())
            height = int(parts[1].strip())
            divisor = gcd(width, height)
            return [width // divisor, height // divisor]
        except (ValueError, ZeroDivisionError):
            return None

    def _normalize_option(self, opt, group=None) -> Dict[str, Any]:
        """Normalize a resolution option to a dict with value, ratio, tier, and optional group"""
        tier = None
        if isinstance(opt, str):
            result = {"value": opt, "ratio": self._compute_ratio(opt)}
        elif isinstance(opt, dict):
            result = {"value": opt.get("value", "")}
            if "description" in opt:
                result["description"] = opt["description"]
            if "ratio" in opt:
                result["ratio"] = opt["ratio"]
            else:
                result["ratio"] = self._compute_ratio(result["value"])
            tier = opt.get("tier")
        else:
            result = {"value": str(opt), "ratio": self._compute_ratio(str(opt))}

        if group is not None:
            result["group"] = group

        # An option's own `tier:` wins; a file/group entry with no per-item
        # tier (2K/1K/4K single-tier files, video/social presets) falls back
        # to its file-level group, then to "Standard" for ungrouped inline
        # options - every option ends up with SOME tier the UI can show.
        result["tier"] = tier or group or "Standard"

        return result

    def _resolve_template_path(self, template_path: str, preset_id: str) -> Optional[str]:
        """Resolve a template path using the preset context"""
        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return None

        context = {
            'paths': {
                'preset': found_preset.path,
                '_shared': str(self.preset_loader.shared_path),
            }
        }

        if self.template_processor:
            return self.template_processor.process_template(template_path, context)
        else:
            return template_path

    def _load_resolution_file(self, file_path: str) -> List:
        """Load resolution options from a YAML file"""
        try:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                print(f"Warning: Resolution file not found: {file_path}")
                return []
            with open(file_path_obj, 'r') as f:
                file_data = yaml.safe_load(f)
                if isinstance(file_data, list):
                    return file_data
                elif isinstance(file_data, dict) and 'options' in file_data:
                    return file_data['options']
                return []
        except Exception as e:
            print(f"Warning: Error loading resolution file {file_path}: {e}")
            return []

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform resolution field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        configuration = field_info['configuration']
        all_options = []

        # 1. Inline options
        for opt in configuration.get('options', []):
            all_options.append(self._normalize_option(opt))

        # 2. Single file
        if 'file' in configuration and preset_id:
            file_path = configuration['file'].get('path')
            if file_path:
                resolved = self._resolve_template_path(file_path, preset_id)
                if resolved:
                    for opt in self._load_resolution_file(resolved):
                        all_options.append(self._normalize_option(opt))

        # 3. Multiple files with optional groups
        if 'files' in configuration and preset_id:
            for file_entry in configuration['files']:
                file_path = file_entry.get('path')
                group = file_entry.get('group')
                if file_path:
                    resolved = self._resolve_template_path(file_path, preset_id)
                    if resolved:
                        for opt in self._load_resolution_file(resolved):
                            all_options.append(self._normalize_option(opt, group=group))

        schema['options'] = all_options
        return schema

    # Bounds enforced on every WIDTHxHEIGHT submission, including custom
    # entries the picker's configured `options` never listed. No per-preset
    # granularity reaches this validator (it runs stateless, without the
    # preset/template context `output()` resolves shared YAML files with -
    # see `_INPUT_VALIDATORS` in src/features/forms/binding.py), and shipped
    # options aren't reliably snapped to any single step (flux.yml's own
    # "Portrait" Full-tier entry, 1140x1472, isn't a multiple of 8) - so this
    # validator only guards the min/max range against degenerate or absurd
    # requests reaching the pipeline. Snap-granularity guidance for NEW
    # custom entries lives client-side only (resolutionPicker.ts).
    MIN_DIMENSION = 64
    MAX_DIMENSION = 8192

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process resolution field input - validate format/bounds and pass through as string"""
        if not value:
            return None

        # Keep as string for pipeline compatibility
        if isinstance(value, str):
            # Validate format
            parts = value.split('x')
            if len(parts) != 2:
                raise ValueError(f"Invalid resolution format: {value}. Expected 'WIDTHxHEIGHT'")

            try:
                width = int(parts[0].strip())
                height = int(parts[1].strip())
            except ValueError:
                raise ValueError(f"Invalid resolution format: {value}. Width and height must be numeric")

            for label, dim in (("Width", width), ("Height", height)):
                if dim < self.MIN_DIMENSION or dim > self.MAX_DIMENSION:
                    raise ValueError(
                        f"Invalid resolution: {value}. {label} must be between "
                        f"{self.MIN_DIMENSION} and {self.MAX_DIMENSION}px"
                    )

            return value

        raise ValueError(f"Invalid resolution value type: {type(value)}, expected string")

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'resolution'

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
                description="List of resolution options in WIDTHxHEIGHT format as simple string array",
                example=["1280x720", "1920x1080", "3840x2160"]
            ),
            FieldConfigSpec(
                name="file",
                param_type=dict,
                default={},
                description="Load resolution options from a YAML file",
                example={"path": "{{paths._shared}}/resolutions/sdxl.yml"}
            ),
            FieldConfigSpec(
                name="files",
                param_type=list,
                default=[],
                description="Load from multiple YAML files with optional group labels",
                example=[{"path": "{{paths._shared}}/resolutions/sdxl.yml", "group": "SDXL Standard"}]
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether a resolution selection is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="format",
                description="Resolution must be in WIDTHxHEIGHT format (e.g., '1920x1080')",
                param_type=str,
                example="WIDTHxHEIGHT"
            ),
            FieldValidationSpec(
                rule_name="allowed_resolutions",
                description="List of allowed resolution values (validated against options)",
                param_type=list,
                example=["1024x1024", "1920x1080"]
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Resolution Selector",
                description="Simple resolution dropdown with array of resolution strings",
                yaml_config="""type: resolution
name: resolution
label: Resolution
configuration:
  options: ["896x1152", "1152x896", "1024x1024"]
value: "1024x1024" """,
                rendered_output={
                    "type": "resolution",
                    "name": "resolution",
                    "title": "Resolution",
                    "default": "1024x1024",
                    "options": ["896x1152", "1152x896", "1024x1024"]
                },
                frontend_preview={
                    "type": "resolution",
                    "name": "preview_resolution",
                    "title": "Resolution",
                    "options": ["896x1152", "1152x896", "1024x1024"],
                    "default": "1024x1024"
                }
            ),
            FieldExampleSpec(
                title="HD Resolution Options",
                description="Common HD resolution options",
                yaml_config="""type: resolution
name: output_resolution
label: Output Resolution
configuration:
  options: ["1280x720", "1920x1080", "2560x1440", "3840x2160"]""",
                rendered_output={
                    "type": "resolution",
                    "name": "output_resolution",
                    "title": "Output Resolution",
                    "options": ["1280x720", "1920x1080", "2560x1440", "3840x2160"]
                },
                frontend_preview={
                    "type": "resolution",
                    "name": "preview_output_resolution",
                    "title": "Output Resolution",
                    "options": ["1280x720", "1920x1080", "2560x1440", "3840x2160"],
                    "default": "1920x1080"
                }
            ),
            FieldExampleSpec(
                title="File-based Resolutions",
                description="Load resolutions from shared YAML file",
                yaml_config="""type: resolution
name: resolution
label: Resolution
configuration:
  file:
    path: "{{paths._shared}}/resolutions/sdxl.yml"
value: "1024x1024" """,
                rendered_output={
                    "type": "resolution",
                    "name": "resolution",
                    "title": "Resolution",
                    "options": [
                        {"value": "1024x1024", "ratio": [1, 1], "description": "Square"},
                        {"value": "1152x896", "ratio": [9, 7], "description": "Landscape"}
                    ]
                },
                frontend_preview={
                    "type": "resolution",
                    "name": "preview_resolution",
                    "title": "Resolution",
                    "options": [
                        {"value": "1024x1024", "ratio": [1, 1], "description": "Square"},
                        {"value": "1152x896", "ratio": [9, 7], "description": "Landscape"}
                    ],
                    "default": "1024x1024"
                }
            ),
            FieldExampleSpec(
                title="Grouped Resolution Collections",
                description="Multiple resolution files with group labels",
                yaml_config="""type: resolution
name: resolution
label: Resolution
configuration:
  files:
    - path: "{{paths._shared}}/resolutions/sdxl.yml"
      group: "SDXL Standard"
    - path: "{{paths._shared}}/resolutions/social_media.yml"
      group: "Social Media"
value: "1024x1024" """,
                rendered_output={
                    "type": "resolution",
                    "name": "resolution",
                    "title": "Resolution",
                    "options": [
                        {"value": "1024x1024", "ratio": [1, 1], "description": "Square", "group": "SDXL Standard"},
                        {"value": "1920x1080", "ratio": [16, 9], "description": "YouTube / Twitter", "group": "Social Media"}
                    ]
                },
                frontend_preview={
                    "type": "resolution",
                    "name": "preview_resolution",
                    "title": "Resolution",
                    "options": [
                        {"value": "1024x1024", "ratio": [1, 1], "description": "Square", "group": "SDXL Standard"}
                    ],
                    "default": "1024x1024"
                }
            ),
        ]
