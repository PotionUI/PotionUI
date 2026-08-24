import unittest
import base64
from unittest.mock import Mock

from src.features.fields.media import Media


class TestMediaField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.media_field = Media(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.media_field.can_handle('media'))
        self.assertFalse(self.media_field.can_handle('image'))

    def test_input_single_passthrough_image_item(self):
        item = {
            'path': 'uploads/a.png', 'relative_path': 'uploads/a.png',
            'url': '/api/media/uploads/a.png', 'name': 'a.png', 'type': 'image',
        }
        self.assertEqual(self.media_field.input('source', item), item)

    def test_input_single_passthrough_video_item(self):
        """Unlike `Image`, `Media` accepts a video item with no configuration
        at all - it has no built-in category restriction of its own."""
        item = {'path': 'uploads/a.mp4', 'type': 'video'}
        self.assertEqual(self.media_field.input('source', item), item)

    def test_input_multi_mixed_types_pass_through(self):
        items = [
            {'path': 'a.png', 'type': 'image'},
            {'path': 'a.mp4', 'type': 'video'},
            {'path': 'a.mp3', 'type': 'audio'},
        ]
        result = self.media_field.input('refs', items, {'multi': True})
        self.assertEqual(result, items)

    def test_input_accepted_types_restricts_the_field(self):
        items = [{'path': 'a.mp3', 'type': 'audio'}]
        with self.assertRaises(ValueError) as cm:
            self.media_field.input('refs', items, {'multi': True, 'accepted_types': ['image', 'video']})
        self.assertIn('not accepted', str(cm.exception))

    def test_input_empty_value(self):
        self.assertIsNone(self.media_field.input('source', None))
        self.assertIsNone(self.media_field.input('source', ''))

    def test_input_missing_data_legacy_shape(self):
        with self.assertRaises(ValueError) as cm:
            self.media_field.input('source', {'name': 'x'})
        self.assertIn('Missing media data', str(cm.exception))

    def test_input_legacy_base64_decodes(self):
        b64 = base64.b64encode(b'\x00' * 8).decode()
        result = self.media_field.input('source', {'data': b64, 'name': 'x.png', 'type': 'image/png', 'size': 8})
        self.assertIsInstance(result['data'], bytes)

    def test_output_max_items_and_accepted_types_are_echoed(self):
        field = {
            'type': 'media', 'name': 'refs', 'label': 'Refs',
            'configuration': {'multi': True, 'max_items': 3, 'accepted_types': ['image', 'video']},
        }
        schema = self.media_field.output(field)
        self.assertTrue(schema['multiple'])
        self.assertEqual(schema['max_items'], 3)
        self.assertEqual(schema['accepted_types'], ['image', 'video'])

    def test_output_advanced_limits_are_echoed_only_when_configured(self):
        field_with_limits = {
            'type': 'media', 'name': 'refs', 'label': 'Refs',
            'configuration': {
                'multi': True,
                'max_resolution': 2048,
                'max_video_duration_seconds': 5,
                'max_total_video_duration_seconds': 12,
            },
        }
        field_without_limits = {
            'type': 'media', 'name': 'single', 'label': 'Single', 'configuration': {},
        }
        schema = self.media_field.output(field_with_limits)
        self.assertEqual(schema['max_resolution'], 2048)
        self.assertEqual(schema['max_video_duration_seconds'], 5)
        self.assertEqual(schema['max_total_video_duration_seconds'], 12)

        bare_schema = self.media_field.output(field_without_limits)
        self.assertNotIn('max_resolution', bare_schema)
        self.assertNotIn('max_video_duration_seconds', bare_schema)

    def test_get_accept_string_spans_all_three_categories(self):
        accept_string = self.media_field._get_accept_string({})
        self.assertIn('image/png', accept_string)
        self.assertIn('video/mp4', accept_string)
        self.assertIn('audio/mpeg', accept_string)

    def test_configuration_spec_declares_the_new_limit_keys(self):
        names = {spec.name for spec in Media.configuration()}
        self.assertEqual(
            names,
            {
                'multi', 'max_items', 'accepted_types', 'max_resolution',
                'max_video_duration_seconds', 'max_total_video_duration_seconds',
                'max_audio_duration_seconds', 'max_total_audio_duration_seconds',
                'formats', 'validation',
            },
        )


if __name__ == '__main__':
    unittest.main()
