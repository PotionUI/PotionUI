import unittest
import base64
import tempfile
import os
from unittest.mock import Mock

from src.features.fields.video import Video


class TestVideoField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.video_field = Video(self.preset_loader)

        self.sample_base64 = base64.b64encode(b'\x00' * 16).decode()
        self.sample_video_data = {
            'data': self.sample_base64,
            'name': 'test.mp4',
            'type': 'video/mp4',
            'size': 16
        }

    def test_can_handle(self):
        self.assertTrue(self.video_field.can_handle('video'))
        self.assertFalse(self.video_field.can_handle('audio'))
        self.assertFalse(self.video_field.can_handle('image'))

    def test_input_single_video(self):
        result = self.video_field.input('test_video', self.sample_video_data)

        self.assertIsInstance(result, dict)
        self.assertIsInstance(result['data'], bytes)
        self.assertEqual(result['name'], 'test.mp4')
        self.assertEqual(result['type'], 'video/mp4')

    def test_input_multiple_videos(self):
        videos = [self.sample_video_data, self.sample_video_data.copy()]
        result = self.video_field.input('test_videos', videos)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for video in result:
            self.assertIsInstance(video['data'], bytes)

    def test_input_path_string(self):
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.mp4', delete=False) as f:
            temp_path = f.name
            f.write(b'\x00' * 16)

        try:
            result = self.video_field.input('test_video', temp_path)
            self.assertEqual(result, temp_path)
        finally:
            os.unlink(temp_path)

    def test_input_path_string_not_exists_passes_through(self):
        self.assertEqual(
            self.video_field.input('test_video', '/tmp/non_existent_video.mp4'),
            '/tmp/non_existent_video.mp4',
        )

    def test_input_relative_path_string_is_not_existence_checked(self):
        """A storage-relative path (the real MediaLoaderField shape once a
        server round-trip has happened) has no meaningful cwd-relative
        existence check - resolving/verifying it is bind_form's
        `_check_media_containment` job, which has the storage root."""
        result = self.video_field.input('test_video', 'generations/2026-01-01/gen1/0.mp4')
        self.assertEqual(result, 'generations/2026-01-01/gen1/0.mp4')

    def test_input_empty_value(self):
        self.assertIsNone(self.video_field.input('test_video', None))
        self.assertIsNone(self.video_field.input('test_video', ''))

    def test_input_missing_data(self):
        invalid_data = {'name': 'test.mp4', 'type': 'video/mp4'}
        with self.assertRaises(ValueError) as cm:
            self.video_field.input('test_video', invalid_data)
        self.assertIn('Missing video data', str(cm.exception))

    def test_input_validation_file_size(self):
        validation_rules = {'max_size': 8}
        with self.assertRaises(ValueError) as cm:
            self.video_field.input('test_video', self.sample_video_data, validation_rules)
        self.assertIn('exceeds maximum allowed size', str(cm.exception))

    def test_input_media_ref_dict_passes_through(self):
        """The real value shape MediaLoaderField sends - not base64."""
        item = {
            'path': 'generations/2026-01-01/gen1/0.mp4',
            'relative_path': 'generations/2026-01-01/gen1/0.mp4',
            'url': '/api/media/generations/gen1/0.mp4',
            'name': '0.mp4',
            'type': 'video',
        }
        result = self.video_field.input('source_video', item)
        self.assertEqual(result, item)

    # --- Multi-item mode ---

    def test_input_multi_passthrough_media_refs_with_labels(self):
        items = [
            {'path': 'uploads/a.mp4', 'type': 'video', 'label': '  Establishing  '},
            {'path': 'uploads/b.mp4', 'type': 'video'},
        ]
        result = self.video_field.input('refs', items, {'multi': True})
        self.assertEqual(result[0]['label'], 'Establishing')
        self.assertNotIn('label', result[1])

    def test_input_multi_enforces_max_items(self):
        items = [{'path': f'uploads/{i}.mp4'} for i in range(3)]
        with self.assertRaises(ValueError) as cm:
            self.video_field.input('refs', items, {'multi': True, 'max_items': 2})
        self.assertIn('Too many items', str(cm.exception))

    def test_input_multi_empty_list_stays_a_list(self):
        result = self.video_field.input('refs', [], {'multi': True})
        self.assertEqual(result, [])

    def test_output_schema_generation(self):
        field = {
            'type': 'video',
            'name': 'test_video',
            'label': 'Test Video',
            'required': False,
            'configuration': {'multi': False, 'formats': ['.mp4', '.webm']},
            'validation': {'max_size': 104857600},
        }
        schema = self.video_field.output(field)

        self.assertEqual(schema['type'], 'video')
        self.assertFalse(schema['multiple'])
        self.assertNotIn('max_items', schema)

    def test_output_multiple_videos_emits_max_items_when_configured(self):
        field = {
            'type': 'video',
            'name': 'test_videos',
            'label': 'Test Videos',
            'configuration': {'multi': True, 'max_items': 4},
        }
        schema = self.video_field.output(field)
        self.assertTrue(schema['multiple'])
        self.assertEqual(schema['max_items'], 4)

    def test_configuration_specs_include_max_items(self):
        names = [s.name for s in Video.configuration()]
        self.assertIn('multi', names)
        self.assertIn('max_items', names)

    def test_output_echoes_advanced_limits_only_when_configured(self):
        field_with_limits = {
            'type': 'video', 'name': 'refs', 'label': 'Refs',
            'configuration': {
                'multi': True,
                'max_resolution': 2048,
                'max_video_duration_seconds': 5,
                'max_total_video_duration_seconds': 12,
            },
        }
        field_without_limits = {
            'type': 'video', 'name': 'single', 'label': 'Single', 'configuration': {},
        }
        schema = self.video_field.output(field_with_limits)
        self.assertEqual(schema['max_resolution'], 2048)
        self.assertEqual(schema['max_video_duration_seconds'], 5)
        self.assertEqual(schema['max_total_video_duration_seconds'], 12)

        bare_schema = self.video_field.output(field_without_limits)
        self.assertNotIn('max_resolution', bare_schema)
        self.assertNotIn('max_video_duration_seconds', bare_schema)
        self.assertNotIn('max_total_video_duration_seconds', bare_schema)


if __name__ == '__main__':
    unittest.main()
