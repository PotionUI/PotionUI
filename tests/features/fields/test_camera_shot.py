import unittest
from unittest.mock import Mock

from src.features.fields.camera_shot import CameraShot
from src.features.fields.camera_shot_taxonomy import CATEGORY_KEYS, DEFAULT_CATEGORY_KEYS


class TestCameraShot(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.field = CameraShot(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.field.can_handle('camera_shot'))
        self.assertFalse(self.field.can_handle('header'))

    def test_output_defaults_to_image_categories(self):
        field = {'type': 'camera_shot', 'name': 'camera', 'label': 'Camera & Shot'}
        result = self.field.output(field)

        self.assertEqual(result['type'], 'camera_shot')
        # Motion is opt-in: the default is image categories only.
        self.assertEqual(result['configuration']['categories'], list(DEFAULT_CATEGORY_KEYS))
        self.assertNotIn('motion', result['configuration']['categories'])
        self.assertEqual([c['key'] for c in result['catalog']], list(DEFAULT_CATEGORY_KEYS))

    def test_output_video_categories_include_motion(self):
        field = {
            'type': 'camera_shot',
            'name': 'camera',
            'configuration': {'categories': list(CATEGORY_KEYS)},
        }
        result = self.field.output(field)
        self.assertEqual([c['key'] for c in result['catalog']], list(CATEGORY_KEYS))

    def test_output_filters_and_orders_categories(self):
        field = {
            'type': 'camera_shot',
            'name': 'camera',
            'configuration': {'categories': ['orientation', 'angle', 'bogus']},
        }
        result = self.field.output(field)
        self.assertEqual(result['configuration']['categories'], ['orientation', 'angle'])
        self.assertEqual([c['key'] for c in result['catalog']], ['orientation', 'angle'])

    def test_vocabulary_override_beats_default_in_catalog(self):
        field = {
            'type': 'camera_shot',
            'name': 'camera',
            'configuration': {
                'categories': ['angle'],
                'vocabulary': {'overhead': 'from the ceiling'},
            },
        }
        result = self.field.output(field)
        overhead = next(s for s in result['catalog'][0]['shots'] if s['key'] == 'overhead')
        eye_level = next(s for s in result['catalog'][0]['shots'] if s['key'] == 'eye_level')
        self.assertEqual(overhead['phrase'], 'from the ceiling')
        self.assertTrue(overhead['overridden'])
        self.assertEqual(eye_level['phrase'], eye_level['default_phrase'])
        self.assertFalse(eye_level['overridden'])

    def test_unknown_vocabulary_key_is_ignored_in_output(self):
        field = {
            'type': 'camera_shot',
            'name': 'camera',
            'configuration': {'categories': ['angle'], 'vocabulary': {'bogus': 'x'}},
        }
        # Output must not raise; unknown keys simply don't appear.
        result = self.field.output(field)
        keys = [s['key'] for s in result['catalog'][0]['shots']]
        self.assertNotIn('bogus', keys)


    def test_map_field_delegates_to_output(self):
        field = {'type': 'camera_shot', 'name': 'camera', 'label': 'Camera'}
        self.assertEqual(self.field.output(field, 'p'), self.field.map_field(field, 'p'))

    def test_examples_render_camera_shot(self):
        for example in CameraShot.examples():
            self.assertEqual(example.rendered_output['type'], 'camera_shot')


if __name__ == '__main__':
    unittest.main()
