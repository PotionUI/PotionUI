import base64
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from .media_input import process_media_input, echo_configured_constraints, MAX_MEDIA_LABEL_LENGTH


class Image(BaseField):
    """Image field for handling image uploads and processing"""

    # Supported image formats
    SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}

    # Default max file size (10MB)
    DEFAULT_MAX_SIZE = 10 * 1024 * 1024

    # Multi-item labels are a HANDLE other systems reference (e.g. a future
    # <Picture N> prompt binding), not free-form prose - capped hard here.
    MAX_LABEL_LENGTH = MAX_MEDIA_LABEL_LENGTH

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process and validate image input from frontend - see
        `media_input.process_media_input` for the full shape contract
        (string path / passthrough media-reference dict / legacy base64
        dict / list, with `multi`+`max_items`+per-item `label` support).

        Registered in `src/features/forms/binding.py`'s `_INPUT_VALIDATORS`,
        so this runs on every real generation submission for an `image`/
        `media` field - not just these direct-call tests.
        """
        return process_media_input(
            field_name, value, validation_rules,
            validate_legacy=self._validate_image,
            process_legacy=self._process_image,
        )

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map image field to JSON schema for frontend"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Image-specific schema properties
        schema['accept'] = self._get_accept_string(field_info['configuration'])
        schema['multiple'] = field_info['configuration'].get('multi', False)

        max_items = field_info['configuration'].get('max_items')
        if max_items is not None:
            schema['max_items'] = max_items

        # Initialize configuration dict if needed and pass through allow_inpaint
        if 'allow_inpaint' in field_info['configuration']:
            if 'configuration' not in schema:
                schema['configuration'] = {}
            schema['configuration']['allow_inpaint'] = field_info['configuration']['allow_inpaint']

        # `max_resolution` etc, only when configured - see media_input.echo_configured_constraints.
        echo_configured_constraints(schema, field_info['configuration'])

        # Add validation rules to schema
        validation = field_info.get('validation', {})
        if validation:
            schema['validation'] = {
                'maxSize': validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxWidth': validation.get('max_width'),
                'maxHeight': validation.get('max_height'),
                'formats': validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        # Also check configuration for validation (backwards compatibility)
        config_validation = field_info['configuration'].get('validation', {})
        if config_validation and 'validation' not in schema:
            schema['validation'] = {
                'maxSize': config_validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxWidth': config_validation.get('max_width'),
                'maxHeight': config_validation.get('max_height'),
                'formats': config_validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        return schema
    
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'image'
    
    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map image field to JSON schema for frontend"""
        return self.output(field, preset_id)
    
    def _validate_image(self, image_data: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
        """Validate a single image"""
        errors = []
        
        # Check required fields
        if not isinstance(image_data, dict):
            errors.append("Invalid image data format")
            return errors
        
        if 'data' not in image_data:
            errors.append("Missing image data")
        
        if 'type' not in image_data:
            errors.append("Missing image type")
        
        # Check file size
        max_size = rules.get('max_size', self.DEFAULT_MAX_SIZE)
        if 'size' in image_data and image_data['size'] > max_size:
            errors.append(f"Image size exceeds maximum allowed size of {max_size / (1024 * 1024):.1f}MB")
        
        # Check format
        if 'name' in image_data:
            ext = Path(image_data['name']).suffix.lower()
            allowed_formats = rules.get('formats', list(self.SUPPORTED_FORMATS))
            if ext not in allowed_formats:
                errors.append(f"Unsupported image format. Allowed: {', '.join(allowed_formats)}")
        
        # Check dimensions if specified
        if 'width' in image_data and 'height' in image_data:
            if 'max_width' in rules and image_data['width'] > rules['max_width']:
                errors.append(f"Image width exceeds maximum of {rules['max_width']}px")
            
            if 'max_height' in rules and image_data['height'] > rules['max_height']:
                errors.append(f"Image height exceeds maximum of {rules['max_height']}px")
        
        return errors
    
    def _process_image(self, image_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single image for pipeline use"""
        # Extract base64 data (remove data URL prefix if present)
        base64_data = image_data['data']
        if base64_data.startswith('data:'):
            base64_data = base64_data.split(',', 1)[1]
        
        # Decode base64 to bytes
        try:
            image_bytes = base64.b64decode(base64_data)
        except Exception as e:
            raise ValueError(f"Invalid base64 image data: {str(e)}")
        
        return {
            'data': image_bytes,
            'name': image_data.get('name', 'image'),
            'type': image_data.get('type', 'image/png'),
            'size': len(image_bytes),
            'original_data': image_data  # Keep original data for reference
        }
    
    def _get_accept_string(self, configuration: Dict[str, Any]) -> str:
        """Generate accept string for file input"""
        formats = configuration.get('formats', list(self.SUPPORTED_FORMATS))
        
        # Convert extensions to MIME types
        mime_types = []
        for fmt in formats:
            if fmt == '.jpg' or fmt == '.jpeg':
                mime_types.append('image/jpeg')
            elif fmt == '.png':
                mime_types.append('image/png')
            elif fmt == '.webp':
                mime_types.append('image/webp')
            elif fmt == '.gif':
                mime_types.append('image/gif')
            elif fmt == '.bmp':
                mime_types.append('image/bmp')
        
        return ','.join(mime_types) if mime_types else 'image/*'

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="multi",
                param_type=bool,
                default=False,
                description="Allow multiple image uploads",
                example=True
            ),
            FieldConfigSpec(
                name="max_items",
                param_type=int,
                default=None,
                description="Maximum number of items allowed when `multi` is enabled",
                example=5
            ),
            FieldConfigSpec(
                name="formats",
                param_type=list,
                default=list(cls.SUPPORTED_FORMATS),
                description="Allowed image file formats",
                example=[".jpg", ".png", ".webp"]
            ),
            FieldConfigSpec(
                name="validation",
                param_type=dict,
                default={},
                description="Image validation rules (legacy, prefer field-level validation)",
                example={
                    "max_size": 10485760,
                    "max_width": 1920,
                    "max_height": 1080
                }
            ),
            FieldConfigSpec(
                name="allow_inpaint",
                param_type=bool,
                default=False,
                description="Show the inpaint mask button on this image field",
                example=True
            ),
            FieldConfigSpec(
                name="max_resolution",
                param_type=int,
                default=None,
                description="Maximum width AND height, in pixels, for any uploaded image",
                example=2048
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        image_rules = [
            FieldValidationSpec(
                rule_name="max_size",
                description="Maximum file size in bytes",
                param_type=int,
                example=10485760  # 10MB
            ),
            FieldValidationSpec(
                rule_name="max_width",
                description="Maximum image width in pixels",
                param_type=int,
                example=1920
            ),
            FieldValidationSpec(
                rule_name="max_height",
                description="Maximum image height in pixels",
                param_type=int,
                example=1080
            ),
            FieldValidationSpec(
                rule_name="formats",
                description="List of allowed file extensions",
                param_type=list,
                example=[".jpg", ".png", ".webp"]
            ),
        ]
        return base_rules + image_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Image Upload",
                description="Single image upload field",
                yaml_config="""type: image
name: input_image
label: Upload Image
validation:
  max_size: 5242880  # 5MB
  formats: [".jpg", ".png"]""",
                rendered_output={
                    "type": "image",
                    "name": "input_image",
                    "title": "Upload Image",
                    "accept": "image/jpeg,image/png",
                    "multiple": False,
                    "validation": {
                        "maxSize": 5242880,
                        "formats": [".jpg", ".png"]
                    }
                },
                frontend_preview={
                    "type": "image",
                    "name": "preview_input_image",
                    "title": "Upload Image",
                    "accept": "image/jpeg,image/png",
                    "multiple": False
                }
            ),
            FieldExampleSpec(
                title="Multiple Image Upload",
                description="Allow uploading multiple images",
                yaml_config="""type: image
name: reference_images
label: Reference Images
configuration:
  multi: true
  formats: [".jpg", ".png", ".webp"]
validation:
  max_size: 10485760  # 10MB
  max_width: 2048
  max_height: 2048""",
                rendered_output={
                    "type": "image",
                    "name": "reference_images",
                    "title": "Reference Images",
                    "accept": "image/jpeg,image/png,image/webp",
                    "multiple": True,
                    "validation": {
                        "maxSize": 10485760,
                        "maxWidth": 2048,
                        "maxHeight": 2048,
                        "formats": [".jpg", ".png", ".webp"]
                    }
                },
                frontend_preview={
                    "type": "image",
                    "name": "preview_reference_images",
                    "title": "Reference Images",
                    "accept": "image/jpeg,image/png,image/webp",
                    "multiple": True
                }
            ),
            FieldExampleSpec(
                title="Profile Picture Upload",
                description="Small square image for profiles",
                yaml_config="""type: image
name: avatar
label: Profile Picture
validation:
  max_size: 1048576  # 1MB
  max_width: 512
  max_height: 512
  formats: [".jpg", ".png"]""",
                rendered_output={
                    "type": "image",
                    "name": "avatar",
                    "title": "Profile Picture",
                    "accept": "image/jpeg,image/png",
                    "multiple": False,
                    "validation": {
                        "maxSize": 1048576,
                        "maxWidth": 512,
                        "maxHeight": 512,
                        "formats": [".jpg", ".png"]
                    }
                },
                frontend_preview={
                    "type": "image",
                    "name": "preview_avatar",
                    "title": "Profile Picture",
                    "accept": "image/jpeg,image/png",
                    "multiple": False
                }
            ),
        ]