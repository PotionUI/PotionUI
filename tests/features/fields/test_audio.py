import unittest
import base64
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock

from src.features.fields.audio import Audio


class TestAudioField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.audio_field = Audio(self.preset_loader)

        # Minimal valid base64 data (a few null bytes encoded)
        self.sample_base64 = base64.b64encode(b'\x00' * 16).decode()
        self.sample_audio_data = {
            'data': self.sample_base64,
            'name': 'test.mp3',
            'type': 'audio/mpeg',
            'size': 16
        }

    def test_can_handle(self):
        """Test that audio field handles 'audio' type only"""
        self.assertTrue(self.audio_field.can_handle('audio'))
        self.assertFalse(self.audio_field.can_handle('video'))
        self.assertFalse(self.audio_field.can_handle('image'))
        self.assertFalse(self.audio_field.can_handle('select'))

    def test_input_single_audio(self):
        """Test processing single audio input"""
        result = self.audio_field.input('test_audio', self.sample_audio_data)

        self.assertIsInstance(result, dict)
        self.assertIn('data', result)
        self.assertIn('name', result)
        self.assertIn('type', result)
        self.assertIn('size', result)
        self.assertIsInstance(result['data'], bytes)
        self.assertEqual(result['name'], 'test.mp3')
        self.assertEqual(result['type'], 'audio/mpeg')

    def test_input_multiple_audios(self):
        """Test processing multiple audio inputs"""
        audios = [self.sample_audio_data, self.sample_audio_data.copy()]
        result = self.audio_field.input('test_audios', audios)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for audio in result:
            self.assertIsInstance(audio, dict)
            self.assertIn('data', audio)
            self.assertIsInstance(audio['data'], bytes)

    def test_input_path_string(self):
        """Test processing path string input (passes through if file exists)"""
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.mp3', delete=False) as f:
            temp_path = f.name
            f.write(b'\x00' * 16)

        try:
            result = self.audio_field.input('test_audio', temp_path)
            self.assertEqual(result, temp_path)
        finally:
            os.unlink(temp_path)

    def test_input_path_string_not_exists(self):
        """A non-existent absolute path passes through - existence is the
        media pipe's job at execution time."""
        non_existent_path = '/tmp/non_existent_audio.mp3'
        self.assertEqual(self.audio_field.input('test_audio', non_existent_path), non_existent_path)

    def test_input_empty_value(self):
        """Test handling empty input returns None"""
        self.assertIsNone(self.audio_field.input('test_audio', None))
        self.assertIsNone(self.audio_field.input('test_audio', ''))

    def test_input_data_url_format(self):
        """Test handling data URL format (strips prefix before decoding)"""
        data_url_format = {
            'data': 'data:audio/mpeg;base64,' + self.sample_base64,
            'name': 'test.mp3',
            'type': 'audio/mpeg',
            'size': 16
        }

        result = self.audio_field.input('test_audio', data_url_format)
        self.assertIsInstance(result['data'], bytes)

    def test_input_invalid_base64(self):
        """Test handling invalid base64 data"""
        invalid_data = self.sample_audio_data.copy()
        invalid_data['data'] = '!!!invalid_base64!!!'

        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('test_audio', invalid_data)

        self.assertIn('Invalid base64 audio data', str(cm.exception))

    def test_input_missing_data(self):
        """Test handling missing audio data field"""
        invalid_data = {'name': 'test.mp3', 'type': 'audio/mpeg'}

        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('test_audio', invalid_data)

        self.assertIn('Missing audio data', str(cm.exception))

    def test_input_validation_file_size(self):
        """Test file size validation"""
        validation_rules = {'max_size': 8}  # Smaller than our 16-byte sample

        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('test_audio', self.sample_audio_data, validation_rules)

        self.assertIn('exceeds maximum allowed size', str(cm.exception))

    def test_input_validation_format(self):
        """Test format validation rejects disallowed extensions"""
        validation_rules = {'formats': ['.wav', '.flac']}  # Only allow wav/flac

        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('test_audio', self.sample_audio_data, validation_rules)

        self.assertIn('Unsupported audio format', str(cm.exception))

    def test_input_validation_duration(self):
        """Test duration validation"""
        audio_with_duration = self.sample_audio_data.copy()
        audio_with_duration['duration'] = 400  # seconds

        validation_rules = {'max_duration': 300}

        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('test_audio', audio_with_duration, validation_rules)

        self.assertIn('duration exceeds maximum', str(cm.exception))

    def test_input_media_ref_dict_passes_through(self):
        """The real value shape MediaLoaderField sends - not base64."""
        item = {
            'path': 'generations/2026-01-01/gen1/0.mp3',
            'relative_path': 'generations/2026-01-01/gen1/0.mp3',
            'url': '/api/media/generations/gen1/0.mp3',
            'name': '0.mp3',
            'type': 'audio',
        }
        result = self.audio_field.input('source_audio', item)
        self.assertEqual(result, item)

    # --- Multi-item mode ---

    def test_input_multi_passthrough_media_refs_with_labels(self):
        items = [
            {'path': 'uploads/a.mp3', 'type': 'audio', 'label': '  Narration  '},
            {'path': 'uploads/b.mp3', 'type': 'audio'},
        ]
        result = self.audio_field.input('refs', items, {'multi': True})
        self.assertEqual(result[0]['label'], 'Narration')
        self.assertNotIn('label', result[1])

    def test_input_multi_enforces_max_items(self):
        items = [{'path': f'uploads/{i}.mp3'} for i in range(3)]
        with self.assertRaises(ValueError) as cm:
            self.audio_field.input('refs', items, {'multi': True, 'max_items': 2})
        self.assertIn('Too many items', str(cm.exception))

    def test_output_schema_generation(self):
        """Test output schema generation with validation rules"""
        field = {
            'type': 'audio',
            'name': 'test_audio',
            'label': 'Test Audio',
            'description': 'Upload a test audio file',
            'required': False,
            'configuration': {
                'multi': False,
                'formats': ['.mp3', '.wav']
            },
            'validation': {
                'max_size': 26214400,
                'max_duration': 300
            }
        }

        schema = self.audio_field.output(field)

        self.assertEqual(schema['type'], 'audio')
        self.assertEqual(schema['name'], 'test_audio')
        self.assertEqual(schema['title'], 'Test Audio')
        self.assertFalse(schema['multiple'])
        self.assertIn('accept', schema)
        self.assertIn('validation', schema)
        self.assertEqual(schema['validation']['maxSize'], 26214400)
        self.assertEqual(schema['validation']['maxDuration'], 300)

    def test_output_multiple_audios(self):
        """Test output schema for multiple audio uploads"""
        field = {
            'type': 'audio',
            'name': 'test_audios',
            'label': 'Test Audios',
            'configuration': {'multi': True}
        }

        schema = self.audio_field.output(field)
        self.assertTrue(schema['multiple'])

    def test_output_multiple_audios_emits_max_items_when_configured(self):
        field = {
            'type': 'audio',
            'name': 'test_audios',
            'label': 'Test Audios',
            'configuration': {'multi': True, 'max_items': 3}
        }
        schema = self.audio_field.output(field)
        self.assertEqual(schema['max_items'], 3)

    def test_output_config_validation_backwards_compat(self):
        """Test that validation in configuration is respected (backwards compat)"""
        field = {
            'type': 'audio',
            'name': 'test_audio',
            'label': 'Test Audio',
            'configuration': {
                'validation': {
                    'max_size': 10485760,
                    'max_duration': 120
                }
            }
        }

        schema = self.audio_field.output(field)
        self.assertIn('validation', schema)
        self.assertEqual(schema['validation']['maxSize'], 10485760)
        self.assertEqual(schema['validation']['maxDuration'], 120)

    def test_output_echoes_advanced_limits_only_when_configured(self):
        field_with_limits = {
            'type': 'audio', 'name': 'refs', 'label': 'Refs',
            'configuration': {
                'multi': True,
                'max_audio_duration_seconds': 30,
                'max_total_audio_duration_seconds': 60,
            },
        }
        field_without_limits = {
            'type': 'audio', 'name': 'single', 'label': 'Single', 'configuration': {},
        }
        schema = self.audio_field.output(field_with_limits)
        self.assertEqual(schema['max_audio_duration_seconds'], 30)
        self.assertEqual(schema['max_total_audio_duration_seconds'], 60)

        bare_schema = self.audio_field.output(field_without_limits)
        self.assertNotIn('max_audio_duration_seconds', bare_schema)
        self.assertNotIn('max_total_audio_duration_seconds', bare_schema)

    def test_get_accept_string_all_formats(self):
        """Test MIME type accept string generation for all supported formats"""
        mime_map = {
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.flac': 'audio/flac',
            '.ogg': 'audio/ogg',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
        }

        for ext, expected_mime in mime_map.items():
            config = {'formats': [ext]}
            accept_string = self.audio_field._get_accept_string(config)
            self.assertEqual(accept_string, expected_mime, f"Expected {expected_mime} for {ext}")

    def test_get_accept_string_multiple_formats(self):
        """Test MIME accept string with multiple formats"""
        config = {'formats': ['.mp3', '.wav']}
        accept_string = self.audio_field._get_accept_string(config)
        self.assertIn('audio/mpeg', accept_string)
        self.assertIn('audio/wav', accept_string)

    def test_get_accept_string_empty_formats(self):
        """Test MIME accept string fallback when no formats map to MIME types"""
        config = {'formats': ['.unknown_ext']}
        accept_string = self.audio_field._get_accept_string(config)
        self.assertEqual(accept_string, 'audio/*')

    def test_validate_audio_invalid_format(self):
        """Test _validate_audio directly with non-dict input"""
        errors = self.audio_field._validate_audio("not a dict", {})
        self.assertEqual(len(errors), 1)
        self.assertIn('Invalid audio data format', errors[0])

    def test_validate_audio_duration(self):
        """Test _validate_audio duration check"""
        audio_data = self.sample_audio_data.copy()
        audio_data['duration'] = 600

        errors = self.audio_field._validate_audio(audio_data, {'max_duration': 300})
        self.assertEqual(len(errors), 1)
        self.assertIn('duration exceeds maximum', errors[0])

    def test_map_field_delegates_to_output(self):
        """Test that map_field delegates to output"""
        field = {
            'type': 'audio',
            'name': 'test_audio',
            'label': 'Test Audio',
            'configuration': {}
        }

        schema_output = self.audio_field.output(field)
        schema_map = self.audio_field.map_field(field)
        self.assertEqual(schema_output, schema_map)

    def test_configuration_specs(self):
        """Test that configuration() returns valid specs"""
        specs = Audio.configuration()
        self.assertIsInstance(specs, list)
        self.assertTrue(len(specs) > 0)
        names = [s.name for s in specs]
        self.assertIn('multi', names)
        self.assertIn('formats', names)
        self.assertIn('validation', names)

    def test_validation_rules_specs(self):
        """Test that validation_rules() returns valid specs without width/height"""
        rules = Audio.validation_rules()
        self.assertIsInstance(rules, list)
        rule_names = [r.rule_name for r in rules]
        self.assertIn('max_size', rule_names)
        self.assertIn('max_duration', rule_names)
        self.assertIn('formats', rule_names)
        # Audio does not have width/height rules
        self.assertNotIn('max_width', rule_names)
        self.assertNotIn('max_height', rule_names)

    def test_examples(self):
        """Test that examples() returns three valid example specs"""
        examples = Audio.examples()
        self.assertEqual(len(examples), 3)
        for example in examples:
            self.assertIsNotNone(example.title)
            self.assertIsNotNone(example.yaml_config)
            self.assertIsNotNone(example.rendered_output)


if __name__ == '__main__':
    unittest.main()
