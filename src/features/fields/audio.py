import base64
from typing import Dict, Any, List, Union, Optional
from pathlib import Path

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from .media_input import process_media_input, echo_configured_constraints, MAX_MEDIA_LABEL_LENGTH


class Audio(BaseField):
    """Audio field for handling audio uploads and processing"""

    # Supported audio formats
    SUPPORTED_FORMATS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac'}

    # Default max file size (50MB)
    DEFAULT_MAX_SIZE = 50 * 1024 * 1024

    MAX_LABEL_LENGTH = MAX_MEDIA_LABEL_LENGTH

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process and validate audio input from frontend - see
        `media_input.process_media_input` for the full shape contract.
        Registered in binding.py's `_INPUT_VALIDATORS` - runs on every real
        generation submission for an `audio` field.
        """
        return process_media_input(
            field_name, value, validation_rules,
            validate_legacy=self._validate_audio,
            process_legacy=self._process_audio,
        )

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map audio field to JSON schema for frontend"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Audio-specific schema properties
        schema['accept'] = self._get_accept_string(field_info['configuration'])
        schema['multiple'] = field_info['configuration'].get('multi', False)

        max_items = field_info['configuration'].get('max_items')
        if max_items is not None:
            schema['max_items'] = max_items

        # `max_audio_duration_seconds`/etc, only when configured - see
        # media_input.echo_configured_constraints.
        echo_configured_constraints(schema, field_info['configuration'])

        # Add validation rules to schema
        validation = field_info.get('validation', {})
        if validation:
            schema['validation'] = {
                'maxSize': validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxDuration': validation.get('max_duration'),
                'formats': validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        # Also check configuration for validation (backwards compatibility)
        config_validation = field_info['configuration'].get('validation', {})
        if config_validation and 'validation' not in schema:
            schema['validation'] = {
                'maxSize': config_validation.get('max_size', self.DEFAULT_MAX_SIZE),
                'maxDuration': config_validation.get('max_duration'),
                'formats': config_validation.get('formats', list(self.SUPPORTED_FORMATS))
            }

        return schema

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'audio'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map audio field to JSON schema for frontend"""
        return self.output(field, preset_id)

    def _validate_audio(self, audio_data: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
        """Validate a single audio"""
        errors = []

        # Check required fields
        if not isinstance(audio_data, dict):
            errors.append("Invalid audio data format")
            return errors

        if 'data' not in audio_data:
            errors.append("Missing audio data")

        if 'type' not in audio_data:
            errors.append("Missing audio type")

        # Check file size
        max_size = rules.get('max_size', self.DEFAULT_MAX_SIZE)
        if 'size' in audio_data and audio_data['size'] > max_size:
            errors.append(f"Audio size exceeds maximum allowed size of {max_size / (1024 * 1024):.1f}MB")

        # Check format
        if 'name' in audio_data:
            ext = Path(audio_data['name']).suffix.lower()
            allowed_formats = rules.get('formats', list(self.SUPPORTED_FORMATS))
            if ext not in allowed_formats:
                errors.append(f"Unsupported audio format. Allowed: {', '.join(allowed_formats)}")

        # Check duration if specified
        if 'duration' in audio_data and 'max_duration' in rules:
            if audio_data['duration'] > rules['max_duration']:
                errors.append(f"Audio duration exceeds maximum of {rules['max_duration']} seconds")

        return errors

    def _process_audio(self, audio_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single audio for pipeline use"""
        # Extract base64 data (remove data URL prefix if present)
        base64_data = audio_data['data']
        if base64_data.startswith('data:'):
            base64_data = base64_data.split(',', 1)[1]

        # Decode base64 to bytes
        try:
            audio_bytes = base64.b64decode(base64_data)
        except Exception as e:
            raise ValueError(f"Invalid base64 audio data: {str(e)}")

        return {
            'data': audio_bytes,
            'name': audio_data.get('name', 'audio'),
            'type': audio_data.get('type', 'audio/mpeg'),
            'size': len(audio_bytes),
            'original_data': audio_data  # Keep original data for reference
        }

    def _get_accept_string(self, configuration: Dict[str, Any]) -> str:
        """Generate accept string for file input"""
        formats = configuration.get('formats', list(self.SUPPORTED_FORMATS))

        # Convert extensions to MIME types
        mime_types = []
        for fmt in formats:
            if fmt == '.mp3':
                mime_types.append('audio/mpeg')
            elif fmt == '.wav':
                mime_types.append('audio/wav')
            elif fmt == '.flac':
                mime_types.append('audio/flac')
            elif fmt == '.ogg':
                mime_types.append('audio/ogg')
            elif fmt == '.m4a':
                mime_types.append('audio/mp4')
            elif fmt == '.aac':
                mime_types.append('audio/aac')

        return ','.join(mime_types) if mime_types else 'audio/*'

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="multi",
                param_type=bool,
                default=False,
                description="Allow multiple audio uploads",
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
                description="Allowed audio file formats",
                example=[".mp3", ".wav", ".flac"]
            ),
            FieldConfigSpec(
                name="validation",
                param_type=dict,
                default={},
                description="Audio validation rules (legacy, prefer field-level validation)",
                example={
                    "max_size": 52428800,
                    "max_duration": 300
                }
            ),
            FieldConfigSpec(
                name="max_audio_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum duration, in seconds, of a single uploaded audio file",
                example=30
            ),
            FieldConfigSpec(
                name="max_total_audio_duration_seconds",
                param_type=float,
                default=None,
                description="Maximum combined duration, in seconds, of every audio file in a `multi` field",
                example=60
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        audio_rules = [
            FieldValidationSpec(
                rule_name="max_size",
                description="Maximum file size in bytes",
                param_type=int,
                example=52428800  # 50MB
            ),
            FieldValidationSpec(
                rule_name="max_duration",
                description="Maximum audio duration in seconds",
                param_type=int,
                example=300  # 5 minutes
            ),
            FieldValidationSpec(
                rule_name="formats",
                description="List of allowed file extensions",
                param_type=list,
                example=[".mp3", ".wav", ".flac"]
            ),
        ]
        return base_rules + audio_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic Audio Upload",
                description="Single audio upload field",
                yaml_config="""type: audio
name: input_audio
label: Upload Audio
validation:
  max_size: 26214400  # 25MB
  formats: [".mp3", ".wav"]""",
                rendered_output={
                    "type": "audio",
                    "name": "input_audio",
                    "title": "Upload Audio",
                    "accept": "audio/mpeg,audio/wav",
                    "multiple": False,
                    "validation": {
                        "maxSize": 26214400,
                        "formats": [".mp3", ".wav"]
                    }
                },
                frontend_preview={
                    "type": "audio",
                    "name": "preview_input_audio",
                    "title": "Upload Audio",
                    "accept": "audio/mpeg,audio/wav",
                    "multiple": False
                }
            ),
            FieldExampleSpec(
                title="Multiple Audio Upload",
                description="Allow uploading multiple audio files",
                yaml_config="""type: audio
name: reference_audios
label: Reference Audios
configuration:
  multi: true
  formats: [".mp3", ".wav", ".flac"]
validation:
  max_size: 52428800  # 50MB
  max_duration: 600  # 10 minutes""",
                rendered_output={
                    "type": "audio",
                    "name": "reference_audios",
                    "title": "Reference Audios",
                    "accept": "audio/mpeg,audio/wav,audio/flac",
                    "multiple": True,
                    "validation": {
                        "maxSize": 52428800,
                        "maxDuration": 600,
                        "formats": [".mp3", ".wav", ".flac"]
                    }
                },
                frontend_preview={
                    "type": "audio",
                    "name": "preview_reference_audios",
                    "title": "Reference Audios",
                    "accept": "audio/mpeg,audio/wav,audio/flac",
                    "multiple": True
                }
            ),
            FieldExampleSpec(
                title="Short Audio Clip",
                description="Upload short audio clips for processing",
                yaml_config="""type: audio
name: audio_clip
label: Audio Clip
validation:
  max_size: 10485760  # 10MB
  max_duration: 30  # 30 seconds
  formats: [".mp3"]""",
                rendered_output={
                    "type": "audio",
                    "name": "audio_clip",
                    "title": "Audio Clip",
                    "accept": "audio/mpeg",
                    "multiple": False,
                    "validation": {
                        "maxSize": 10485760,
                        "maxDuration": 30,
                        "formats": [".mp3"]
                    }
                },
                frontend_preview={
                    "type": "audio",
                    "name": "preview_audio_clip",
                    "title": "Audio Clip",
                    "accept": "audio/mpeg",
                    "multiple": False
                }
            ),
        ]
