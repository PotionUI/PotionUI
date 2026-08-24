import base64
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from .media_input import process_media_input, echo_configured_constraints, MAX_MEDIA_LABEL_LENGTH


class Video(BaseField):
    """Video field for handling video uploads and processing"""

    # Supported video formats
    SUPPORTED_FORMATS = {'.mp4', '.webm', '.avi', '.mov', '.mkv'}

    # Default max file size (100MB)
    DEFAULT_MAX_SIZE = 100 * 1024 * 1024

    MAX_LABEL_LENGTH = MAX_MEDIA_LABEL_LENGTH

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process and validate video input from frontend - see
        `media_input.process_media_input` for the full shape contract.
        Registered in binding.py's `_INPUT_VALIDATORS` - runs on every real
        generation submission for a `video` field.
        """
        return process_media_input(
            field_name, value, validation_rules,
            validate_legacy=self._validate_video,
            process_legacy=self._process_video,
        )

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map video field to JSON schema for frontend"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Video-specific schema properties
        schema['accept'] = self._get_accept_string(field_info['configuration'])
        schema['multiple'] = field_info['configuration'].get('multi', False)

        max_items = field_info['configuration'].get('max_items')
        if max_items is not None:
            schema['max_items'] = max_items

        # `max_resolution`/`max_video_duration_seconds`/etc, only when
        # configured - see media_input.echo_configured_constraints.
        echo_configured_constraints(schema, field_info['configuration'])

        # Add validation rules to schema
        validation = field_info.get('validation', {})
        if validation:
            schema['validation'] = {
                'maxSize': validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxDuration': validation.get('max_duration'),
                'maxWidth': validation.get('max_width'),
                'maxHeight': validation.get('max_height'),
                'formats': validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        # Also check configuration for validation (backwards compatibility)
        config_validation = field_info['configuration'].get('validation', {})
        if config_validation and 'validation' not in schema:
            schema['validation'] = {
                'maxSize': config_validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxDuration': config_validation.get('max_duration'),
                'maxWidth': config_validation.get('max_width'),
                'maxHeight': config_validation.get('max_height'),
                'formats': config_validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        return schema

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'video'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map video field to JSON schema for frontend"""
        return self.output(field, preset_id)

    def _validate_video(self, video_data: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
        """Validate a single video"""
        errors = []

        # Check required fields
        if not isinstance(video_data, dict):
            errors.append("Invalid video data format")
            return errors

        if 'data' not in video_data:
            errors.append("Missing video data")

        if 'type' not in video_data:
            errors.append("Missing video type")

        # Check file size
        max_size = rules.get('max_size', self.DEFAULT_MAX_SIZE)
        if 'size' in video_data and video_data['size'] > max_size:
            errors.append(f"Video size exceeds maximum allowed size of {max_size / (1024 * 1024):.1f}MB")

        # Check format
        if 'name' in video_data:
            ext = Path(video_data['name']).suffix.lower()
            allowed_formats = rules.get('formats', list(self.SUPPORTED_FORMATS))
            if ext not in allowed_formats:
                errors.append(f"Unsupported video format. Allowed: {', '.join(allowed_formats)}")

        # Check dimensions if specified
        if 'width' in video_data and 'height' in video_data:
            if 'max_width' in rules and video_data['width'] > rules['max_width']:
                errors.append(f"Video width exceeds maximum of {rules['max_width']}px")

            if 'max_height' in rules and video_data['height'] > rules['max_height']:
                errors.append(f"Video height exceeds maximum of {rules['max_height']}px")

        # Check duration if specified
        if 'duration' in video_data and 'max_duration' in rules:
            if video_data['duration'] > rules['max_duration']:
                errors.append(f"Video duration exceeds maximum of {rules['max_duration']} seconds")

        return errors

    def _process_video(self, video_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single video for pipeline use"""
        # Extract base64 data (remove data URL prefix if present)
        base64_data = video_data['data']
        if base64_data.startswith('data:'):
            base64_data = base64_data.split(',', 1)[1]

        # Decode base64 to bytes
        try:
            video_bytes = base64.b64decode(base64_data)
        except Exception as e:
            raise ValueError(f"Invalid base64 video data: {str(e)}")

        return {
            'data': video_bytes,
            'name': video_data.get('name', 'video'),
            'type': video_data.get('type', 'video/mp4'),
            'size': len(video_bytes),
            'original_data': video_data  # Keep original data for reference
        }

    def _get_accept_string(self, configuration: Dict[str, Any]) -> str:
        """Generate accept string for file input"""
        formats = configuration.get('formats', list(self.SUPPORTED_FORMATS))

        # Convert extensions to MIME types
        mime_types = []
        for fmt in formats:
            if fmt == '.mp4':
                mime_types.append('video/mp4')
            elif fmt == '.webm':
                mime_types.append('video/webm')
            elif fmt == '.avi':
                mime_types.append('video/avi')
            elif fmt == '.mov':
                mime_types.append('video/quicktime')
            elif fmt == '.mkv':
                mime_types.append('video/x-matroska')

        return ','.join(mime_types) if mime_types else 'video/*'

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="multi",
                param_type=bool,
                default=False,
                description="Allow multiple video uploads",
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
                description="Allowed video file formats",
                example=[".mp4", ".webm", ".mov"]
            ),
            FieldConfigSpec(
                name="validation",
                param_type=dict,
                default={},
                description="Video validation rules (legacy, prefer field-level validation)",
                example={
                    "max_size": 104857600,
                    "max_duration": 300,
                    "max_width": 1920,
                    "max_height": 1080
                }
            ),
            FieldConfigSpec(
                name="max_resolution",
                param_type=int,
                default=None,
                description="Maximum width AND height, in pixels, for any uploaded video",
                example=2048
            ),
            FieldConfigSpec(
                name="max_video_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum duration, in seconds, of a single uploaded video",
                example=5
            ),
            FieldConfigSpec(
                name="max_total_video_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum combined duration, in seconds, of every video in a `multi` field",
                example=12
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        video_rules = [
            FieldValidationSpec(
                rule_name="max_size",
                description="Maximum file size in bytes",
                param_type=int,
                example=104857600  # 100MB
            ),
            FieldValidationSpec(
                rule_name="max_duration",
                description="Maximum video duration in seconds",
                param_type=int,
                example=300  # 5 minutes
            ),
            FieldValidationSpec(
                rule_name="max_width",
                description="Maximum video width in pixels",
                param_type=int,
                example=1920
            ),
            FieldValidationSpec(
                rule_name="max_height",
                description="Maximum video height in pixels",
                param_type=int,
                example=1080
            ),
            FieldValidationSpec(
                rule_name="formats",
                description="List of allowed file extensions",
                param_type=list,
                example=[".mp4", ".webm", ".mov"]
            ),
        ]
        return base_rules + video_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Video Upload",
                description="Single video upload field",
                yaml_config="""type: video
name: input_video
label: Upload Video
validation:
  max_size: 52428800  # 50MB
  formats: [".mp4", ".webm"]""",
                rendered_output={
                    "type": "video",
                    "name": "input_video",
                    "title": "Upload Video",
                    "accept": "video/mp4,video/webm",
                    "multiple": False,
                    "validation": {
                        "maxSize": 52428800,
                        "formats": [".mp4", ".webm"]
                    }
                },
                frontend_preview={
                    "type": "video",
                    "name": "preview_input_video",
                    "title": "Upload Video",
                    "accept": "video/mp4,video/webm",
                    "multiple": False
                }
            ),
            FieldExampleSpec(
                title="Multiple Video Upload",
                description="Allow uploading multiple videos",
                yaml_config="""type: video
name: reference_videos
label: Reference Videos
configuration:
  multi: true
  formats: [".mp4", ".webm", ".mov"]
validation:
  max_size: 104857600  # 100MB
  max_duration: 600  # 10 minutes
  max_width: 1920
  max_height: 1080""",
                rendered_output={
                    "type": "video",
                    "name": "reference_videos",
                    "title": "Reference Videos",
                    "accept": "video/mp4,video/webm,video/quicktime",
                    "multiple": True,
                    "validation": {
                        "maxSize": 104857600,
                        "maxDuration": 600,
                        "maxWidth": 1920,
                        "maxHeight": 1080,
                        "formats": [".mp4", ".webm", ".mov"]
                    }
                },
                frontend_preview={
                    "type": "video",
                    "name": "preview_reference_videos",
                    "title": "Reference Videos",
                    "accept": "video/mp4,video/webm,video/quicktime",
                    "multiple": True
                }
            ),
            FieldExampleSpec(
                title="Short Video Clip",
                description="Upload short video clips for processing",
                yaml_config="""type: video
name: video_clip
label: Video Clip
validation:
  max_size: 20971520  # 20MB
  max_duration: 30  # 30 seconds
  max_width: 1280
  max_height: 720
  formats: [".mp4"]""",
                rendered_output={
                    "type": "video",
                    "name": "video_clip",
                    "title": "Video Clip",
                    "accept": "video/mp4",
                    "multiple": False,
                    "validation": {
                        "maxSize": 20971520,
                        "maxDuration": 30,
                        "maxWidth": 1280,
                        "maxHeight": 720,
                        "formats": [".mp4"]
                    }
                },
                frontend_preview={
                    "type": "video",
                    "name": "preview_video_clip",
                    "title": "Video Clip",
                    "accept": "video/mp4",
                    "multiple": False
                }
            ),
        ]