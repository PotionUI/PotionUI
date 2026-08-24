import base64
from typing import Dict, Any, List, Optional
from pathlib import Path

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from .media_input import process_media_input, echo_configured_constraints, MAX_MEDIA_LABEL_LENGTH
from .image import Image
from .video import Video
from .audio import Audio


class Media(BaseField):
    """Generic media field for a single upload slot that can hold more than
    one kind of item - "images or videos", "any of the three" - unlike
    `Image`/`Video`/`Audio`, which are each locked to one category by
    construction. Configured with `accepted_types` (which categories an item
    may be) plus the shared `max_items`/`max_resolution`/duration limits -
    see `media_input._check_media_constraints`."""

    # Union of the three single-type fields' formats - used for the legacy
    # base64 path's format check and the HTML `accept` attribute; a real
    # per-item category restriction is `accepted_types`, not this list.
    SUPPORTED_FORMATS = Image.SUPPORTED_FORMATS | Video.SUPPORTED_FORMATS | Audio.SUPPORTED_FORMATS

    # A media field can hold the heaviest of the three shapes it accepts.
    DEFAULT_MAX_SIZE = Video.DEFAULT_MAX_SIZE

    MAX_LABEL_LENGTH = MAX_MEDIA_LABEL_LENGTH

    _ACCEPT_MIME_TYPES = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
        '.mp4': 'video/mp4', '.webm': 'video/webm', '.avi': 'video/avi',
        '.mov': 'video/quicktime', '.mkv': 'video/x-matroska',
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac',
        '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    }

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process and validate media input from frontend - see
        `media_input.process_media_input` for the full shape contract and
        `media_input._check_media_constraints` for `accepted_types`/
        `max_resolution`/duration enforcement.

        Registered in `src/features/forms/binding.py`'s `_INPUT_VALIDATORS`,
        so this runs on every real generation submission for a `media`
        field - not just these direct-call tests.
        """
        return process_media_input(
            field_name, value, validation_rules,
            validate_legacy=self._validate_media,
            process_legacy=self._process_media,
        )

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map media field to JSON schema for frontend"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info['configuration']

        schema['accept'] = self._get_accept_string(config)
        schema['multiple'] = config.get('multi', False)

        max_items = config.get('max_items')
        if max_items is not None:
            schema['max_items'] = max_items

        if 'allow_inpaint' in config:
            schema.setdefault('configuration', {})['allow_inpaint'] = config['allow_inpaint']

        # `accepted_types`/`max_resolution`/duration limits, only when
        # configured - see media_input.echo_configured_constraints.
        echo_configured_constraints(schema, config)

        validation = field_info.get('validation', {})
        if validation:
            schema['validation'] = {
                'maxSize': validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxWidth': validation.get('max_width'),
                'maxHeight': validation.get('max_height'),
                'formats': validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        return schema

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'media'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map media field to JSON schema for frontend"""
        return self.output(field, preset_id)

    def _validate_media(self, media_data: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
        """Validate a single legacy base64 media upload."""
        errors = []

        if not isinstance(media_data, dict):
            errors.append("Invalid media data format")
            return errors

        if 'data' not in media_data:
            errors.append("Missing media data")

        if 'type' not in media_data:
            errors.append("Missing media type")

        max_size = rules.get('max_size', self.DEFAULT_MAX_SIZE)
        if 'size' in media_data and media_data['size'] > max_size:
            errors.append(f"Media size exceeds maximum allowed size of {max_size / (1024 * 1024):.1f}MB")

        if 'name' in media_data:
            ext = Path(media_data['name']).suffix.lower()
            allowed_formats = rules.get('formats', list(self.SUPPORTED_FORMATS))
            if ext not in allowed_formats:
                errors.append(f"Unsupported media format. Allowed: {', '.join(allowed_formats)}")

        return errors

    def _process_media(self, media_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single legacy base64 media upload for pipeline use"""
        base64_data = media_data['data']
        if base64_data.startswith('data:'):
            base64_data = base64_data.split(',', 1)[1]

        try:
            media_bytes = base64.b64decode(base64_data)
        except Exception as e:
            raise ValueError(f"Invalid base64 media data: {str(e)}")

        return {
            'data': media_bytes,
            'name': media_data.get('name', 'media'),
            'type': media_data.get('type', 'application/octet-stream'),
            'size': len(media_bytes),
            'original_data': media_data
        }

    def _get_accept_string(self, configuration: Dict[str, Any]) -> str:
        """Generate accept string for file input"""
        formats = configuration.get('formats', list(self.SUPPORTED_FORMATS))
        mime_types = [self._ACCEPT_MIME_TYPES[fmt] for fmt in formats if fmt in self._ACCEPT_MIME_TYPES]
        return ','.join(mime_types) if mime_types else '*/*'

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="multi",
                param_type=bool,
                default=False,
                description="Allow multiple media uploads",
                example=True
            ),
            FieldConfigSpec(
                name="max_items",
                param_type=int,
                default=None,
                description="Maximum number of items allowed when `multi` is enabled",
                example=3
            ),
            FieldConfigSpec(
                name="accepted_types",
                param_type=list,
                default=None,
                description="Media categories this field accepts (any of 'image', 'video', 'audio'); "
                             "unset means no restriction",
                example=["image", "video"]
            ),
            FieldConfigSpec(
                name="max_resolution",
                param_type=int,
                default=None,
                description="Maximum width AND height, in pixels, for any image or video item",
                example=2048
            ),
            FieldConfigSpec(
                name="max_video_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum duration, in seconds, of a single video item",
                example=5
            ),
            FieldConfigSpec(
                name="max_total_video_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum combined duration, in seconds, of every video item in a `multi` field",
                example=12
            ),
            FieldConfigSpec(
                name="max_audio_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum duration, in seconds, of a single audio item",
                example=30
            ),
            FieldConfigSpec(
                name="max_total_audio_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum combined duration, in seconds, of every audio item in a `multi` field",
                example=60
            ),
            FieldConfigSpec(
                name="formats",
                param_type=list,
                default=list(cls.SUPPORTED_FORMATS),
                description="Allowed file formats (legacy base64 uploads only)",
                example=[".jpg", ".png", ".mp4"]
            ),
            FieldConfigSpec(
                name="validation",
                param_type=dict,
                default={},
                description="Media validation rules (legacy, prefer field-level validation)",
                example={"max_size": 104857600}
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        base_rules = super().validation_rules()
        media_rules = [
            FieldValidationSpec(
                rule_name="max_size",
                description="Maximum file size in bytes",
                param_type=int,
                example=104857600
            ),
            FieldValidationSpec(
                rule_name="formats",
                description="List of allowed file extensions",
                param_type=list,
                example=[".jpg", ".png", ".mp4"]
            ),
        ]
        return base_rules + media_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Up to 3 references, images or video",
                description="A multi-item field accepting either images or videos, capped in count, "
                             "resolution and total video duration",
                yaml_config="""type: media
name: reference_media
label: Reference Media
configuration:
  multi: true
  max_items: 3
  accepted_types: [image, video]
  max_resolution: 2048
  max_video_duration_seconds: 5
  max_total_video_duration_seconds: 12""",
                rendered_output={
                    "type": "media",
                    "name": "reference_media",
                    "title": "Reference Media",
                    "multiple": True,
                    "max_items": 3,
                    "accepted_types": ["image", "video"],
                    "max_resolution": 2048,
                    "max_video_duration_seconds": 5,
                    "max_total_video_duration_seconds": 12
                },
                frontend_preview={
                    "type": "media",
                    "name": "preview_reference_media",
                    "title": "Reference Media",
                    "multiple": True
                }
            ),
        ]
