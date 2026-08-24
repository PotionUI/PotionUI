import yaml
import glob
import os
from pathlib import Path
from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class CarouselField(BaseField):
    """Carousel field for displaying selectable image grids/carousels"""

    def __init__(self, preset_loader, template_processor=None):
        super().__init__(preset_loader)
        self.template_processor = template_processor

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform carousel field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Get carousel items/options
        schema['options'] = self._get_carousel_items(field_info['configuration'], preset_id)

        # Include preset_id for frontend URL building
        schema['preset_id'] = preset_id

        # Configuration for carousel behavior and appearance
        schema['configuration'] = {
            'multi_select': field_info['configuration'].get('multi_select', False),
            'rows': field_info['configuration'].get('rows', 2),
            'columns': field_info['configuration'].get('columns', 3),
            'item_width': field_info['configuration'].get('item_width', 150),
            'item_height': field_info['configuration'].get('item_height', 150),
            'mode': field_info['configuration'].get('mode', 'grid'),
            'show_labels': field_info['configuration'].get('show_labels', True),
        }

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'carousel'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="items",
                param_type=list,
                default=[],
                description="Static list of carousel items with images",
                example=[
                    {
                        "label": "Fine Grain",
                        "value": "fine",
                        "image": "files/carousel/grain_fine.png",
                        "description": "Subtle film grain effect"
                    },
                    {
                        "label": "Heavy Grain",
                        "value": "heavy",
                        "image": "files/carousel/grain_heavy.png",
                        "description": "Strong film grain effect"
                    }
                ]
            ),
            FieldConfigSpec(
                name="file",
                param_type=dict,
                default={},
                description="Load items from a YAML file",
                example={"path": "{{paths.preset}}/files/form/carousel_items.yml"}
            ),
            FieldConfigSpec(
                name="images",
                param_type=dict,
                default={},
                description="Scan directory for images to use as carousel items",
                example={
                    "in": "{{paths.preset}}/files/carousel",
                    "pattern": "*.png",
                    "recursive": False
                }
            ),
            FieldConfigSpec(
                name="multi_select",
                param_type=bool,
                default=False,
                description="Allow multiple items to be selected",
                example=True
            ),
            FieldConfigSpec(
                name="rows",
                param_type=int,
                default=2,
                description="Number of rows in grid layout",
                example=2
            ),
            FieldConfigSpec(
                name="columns",
                param_type=int,
                default=3,
                description="Number of columns in grid layout",
                example=3
            ),
            FieldConfigSpec(
                name="item_width",
                param_type=int,
                default=150,
                description="Width of each carousel item in pixels",
                example=200
            ),
            FieldConfigSpec(
                name="item_height",
                param_type=int,
                default=150,
                description="Height of each carousel item in pixels",
                example=150
            ),
            FieldConfigSpec(
                name="mode",
                param_type=str,
                default="grid",
                description="Display mode: 'grid' for static grid or 'carousel' for sliding carousel",
                example="grid"
            ),
            FieldConfigSpec(
                name="show_labels",
                param_type=bool,
                default=True,
                description="Show labels below images",
                example=True
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether at least one selection is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="min_selections",
                description="Minimum number of selections required (for multi_select mode)",
                param_type=int,
                example=1
            ),
            FieldValidationSpec(
                rule_name="max_selections",
                description="Maximum number of selections allowed (for multi_select mode)",
                param_type=int,
                example=5
            ),
            FieldValidationSpec(
                rule_name="allowed_values",
                description="List of allowed item values (validated against options at runtime)",
                param_type=list,
                example=["fine", "medium", "heavy"]
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Static Items",
                description="Carousel with predefined items and images",
                yaml_config="""type: carousel
name: film_grain
label: Film Grain Effect
configuration:
  items:
    - label: Fine Grain
      value: fine
      image: files/carousel/grain_fine.png
      description: Subtle film grain
    - label: Medium Grain
      value: medium
      image: files/carousel/grain_medium.png
      description: Moderate film grain
    - label: Heavy Grain
      value: heavy
      image: files/carousel/grain_heavy.png
      description: Strong film grain
  rows: 1
  columns: 3
  item_width: 200
  item_height: 150""",
                rendered_output={
                    "type": "carousel",
                    "name": "film_grain",
                    "title": "Film Grain Effect",
                    "options": [
                        {
                            "label": "Fine Grain",
                            "value": "fine",
                            "image": "files/carousel/grain_fine.png",
                            "description": "Subtle film grain"
                        }
                    ],
                    "configuration": {
                        "multi_select": False,
                        "rows": 1,
                        "columns": 3,
                        "item_width": 200,
                        "item_height": 150,
                        "mode": "grid",
                        "show_labels": True
                    }
                },
                frontend_preview={
                    "type": "carousel",
                    "name": "preview_film_grain",
                    "title": "Film Grain Effect",
                    "options": [],
                    "configuration": {
                        "multi_select": False,
                        "rows": 1,
                        "columns": 3,
                        "mode": "grid",
                        "show_labels": True
                    }
                }
            ),
            FieldExampleSpec(
                title="File-based Items",
                description="Load carousel items from external YAML file",
                yaml_config="""type: carousel
name: art_style
label: Art Style
configuration:
  file:
    path: "{{paths.preset}}/files/form/art_styles.yml"
  rows: 2
  columns: 4""",
                rendered_output={
                    "type": "carousel",
                    "name": "art_style",
                    "title": "Art Style",
                    "options": [],  # Populated from file at runtime
                    "configuration": {
                        "multi_select": False,
                        "rows": 2,
                        "columns": 4,
                        "mode": "grid"
                    }
                },
                frontend_preview={
                    "type": "carousel",
                    "name": "preview_art_style",
                    "title": "Art Style",
                    "description": "Items loaded from external file",
                    "options": [],
                    "configuration": {
                        "multi_select": False,
                        "rows": 2,
                        "columns": 4,
                        "mode": "grid"
                    }
                }
            ),
            FieldExampleSpec(
                title="Directory Scan",
                description="Auto-generate carousel from image directory",
                yaml_config="""type: carousel
name: preset_image
label: Select Preset Image
configuration:
  images:
    in: "{{paths.preset}}/files/carousel"
    pattern: "*.png"
    recursive: false
  rows: 2
  columns: 3
  multi_select: true""",
                rendered_output={
                    "type": "carousel",
                    "name": "preset_image",
                    "title": "Select Preset Image",
                    "options": [],  # Populated from directory scan at runtime
                    "configuration": {
                        "multi_select": True,
                        "rows": 2,
                        "columns": 3,
                        "mode": "grid"
                    }
                },
                frontend_preview={
                    "type": "carousel",
                    "name": "preview_preset_image",
                    "title": "Select Preset Image",
                    "description": "Items scanned from directory",
                    "options": [],
                    "configuration": {
                        "multi_select": True,
                        "rows": 2,
                        "columns": 3,
                        "mode": "grid"
                    }
                }
            ),
        ]

    def _get_carousel_items(self, configuration: Dict[str, Any], preset_id: str) -> List[Dict[str, Any]]:
        """Get carousel items from various sources"""
        items = []

        # Static items
        if 'items' in configuration:
            items.extend(self._get_static_items(configuration))

        # File-based items
        if 'file' in configuration:
            items.extend(self._get_file_items(configuration, preset_id))

        # Directory scan items
        if 'images' in configuration:
            items.extend(self._get_directory_items(configuration, preset_id))

        return items

    def _get_static_items(self, configuration: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get items from static configuration"""
        items = []

        for item in configuration['items']:
            if isinstance(item, dict):
                item_dict = {
                    'label': item.get('label', item.get('value')),
                    'value': item.get('value'),
                    'image': item.get('image'),
                }
                # Include optional fields if present
                if 'description' in item:
                    item_dict['description'] = item['description']
                items.append(item_dict)

        return items

    def _get_file_items(self, configuration: Dict[str, Any], preset_id: str) -> List[Dict[str, Any]]:
        """Get items from a YAML file"""
        items = []
        file_path = configuration['file'].get('path')

        if not file_path:
            return items

        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return items

        # Create context for template processing
        context = {
            'paths': {
                'preset': found_preset.path,
            }
        }

        # Process the template to get the actual file path
        if self.template_processor:
            resolved_path = self.template_processor.process_template(file_path, context)
        else:
            # Fallback: simple string replacement
            resolved_path = file_path.replace('{{paths.preset}}', context['paths']['preset'])

        try:
            file_path_obj = Path(resolved_path)
            if file_path_obj.exists():
                with open(file_path_obj, 'r') as f:
                    file_data = yaml.safe_load(f)
                    items.extend(self._parse_yaml_items(file_data))
        except Exception as e:
            print(f"Error loading carousel items from file {resolved_path}: {e}")

        return items

    def _get_directory_items(self, configuration: Dict[str, Any], preset_id: str) -> List[Dict[str, Any]]:
        """Get items by scanning directory for images"""
        items = []
        images_config = configuration['images']
        directory = images_config.get('in')

        if not directory:
            return items

        found_preset = self._find_preset_by_id(preset_id)
        if not found_preset:
            return items

        context = {
            'paths': {
                'preset': found_preset.path,
            }
        }

        # Process the template to get the actual directory path
        if self.template_processor:
            resolved_directory = self.template_processor.process_template(directory, context)
        else:
            # Fallback: simple string replacement
            resolved_directory = directory.replace('{{paths.preset}}', context['paths']['preset'])

        try:
            # Get pattern and recursive flag
            pattern = images_config.get('pattern', '*.png')
            recursive = images_config.get('recursive', False)

            if recursive:
                # Scan recursively
                search_pattern = os.path.join(resolved_directory, '**', pattern)
                files = glob.glob(search_pattern, recursive=True)
            else:
                # Scan immediate directory only
                search_pattern = os.path.join(resolved_directory, pattern)
                files = glob.glob(search_pattern)

            for file_path in sorted(files):
                filename = os.path.basename(file_path)
                name_without_ext = os.path.splitext(filename)[0]

                # Get relative path from preset directory for the image URL
                preset_path = context['paths']['preset']
                rel_path = os.path.relpath(file_path, preset_path)

                items.append({
                    'label': name_without_ext.replace('_', ' ').title(),
                    'value': name_without_ext,
                    'image': rel_path
                })
        except Exception as e:
            print(f"Error scanning directory {resolved_directory}: {e}")

        return items

    def _parse_yaml_items(self, file_data) -> List[Dict[str, Any]]:
        """Parse carousel items from YAML file data"""
        items = []

        if isinstance(file_data, list):
            # If the file contains a list of items
            for item in file_data:
                if isinstance(item, dict):
                    item_dict = {
                        'label': item.get('label', item.get('value')),
                        'value': item.get('value'),
                        'image': item.get('image'),
                    }
                    if 'description' in item:
                        item_dict['description'] = item['description']
                    items.append(item_dict)
        elif isinstance(file_data, dict):
            # If the file contains a dictionary with items
            for item in file_data.get('items', []):
                if isinstance(item, dict):
                    item_dict = {
                        'label': item.get('label', item.get('value')),
                        'value': item.get('value'),
                        'image': item.get('image'),
                    }
                    if 'description' in item:
                        item_dict['description'] = item['description']
                    items.append(item_dict)

        return items
