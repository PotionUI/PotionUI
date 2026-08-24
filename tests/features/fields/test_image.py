import unittest
import base64
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock

from src.features.fields.image import Image


class TestImageField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.image_field = Image(self.preset_loader)
        
        # Sample image data (minimal PNG)
        self.sample_image_data = {
            'data': 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAI9jU77mgAAAABJRU5ErkJggg==',
            'name': 'test.png',
            'type': 'image/png',
            'size': 95
        }
        
    def test_can_handle(self):
        """Test that image field handles 'image' type"""
        self.assertTrue(self.image_field.can_handle('image'))
        self.assertFalse(self.image_field.can_handle('select'))
        self.assertFalse(self.image_field.can_handle('slider'))
    
    def test_input_single_image(self):
        """Test processing single image input"""
        result = self.image_field.input('test_image', self.sample_image_data)
        
        self.assertIsInstance(result, dict)
        self.assertIn('data', result)
        self.assertIn('name', result)
        self.assertIn('type', result)
        self.assertIn('size', result)
        self.assertIsInstance(result['data'], bytes)
        self.assertEqual(result['name'], 'test.png')
        self.assertEqual(result['type'], 'image/png')
    
    def test_input_multiple_images(self):
        """Test processing multiple images input"""
        images = [self.sample_image_data, self.sample_image_data.copy()]
        result = self.image_field.input('test_images', images)
        
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for image in result:
            self.assertIsInstance(image, dict)
            self.assertIn('data', image)
            self.assertIsInstance(image['data'], bytes)
    
    def test_input_path_string(self):
        """Test processing path string input"""
        # Create a temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.png', delete=False) as f:
            temp_path = f.name
            f.write('test')
        
        try:
            result = self.image_field.input('test_image', temp_path)
            self.assertEqual(result, temp_path)
        finally:
            os.unlink(temp_path)
    
    def test_input_path_string_not_exists(self):
        """A non-existent absolute path passes through - existence is the
        media pipe's job at execution time (bind-time checks broke the
        golden harness's /GOLDEN placeholders)."""
        non_existent_path = '/tmp/non_existent_image.png'
        self.assertEqual(self.image_field.input('test_image', non_existent_path), non_existent_path)
    
    def test_input_empty_value(self):
        """Test handling empty input"""
        self.assertIsNone(self.image_field.input('test_image', None))
        self.assertIsNone(self.image_field.input('test_image', ''))
    
    def test_input_data_url_format(self):
        """Test handling data URL format"""
        data_url_format = {
            'data': 'data:image/png;base64,' + self.sample_image_data['data'],
            'name': 'test.png',
            'type': 'image/png',
            'size': 95
        }
        
        result = self.image_field.input('test_image', data_url_format)
        self.assertIsInstance(result['data'], bytes)
    
    def test_input_invalid_base64(self):
        """Test handling invalid base64 data"""
        invalid_data = self.sample_image_data.copy()
        invalid_data['data'] = 'invalid_base64_data'
        
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('test_image', invalid_data)
        
        self.assertIn('Invalid base64 image data', str(cm.exception))
    
    def test_input_missing_data(self):
        """Test handling missing image data"""
        invalid_data = {'name': 'test.png', 'type': 'image/png'}
        
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('test_image', invalid_data)
        
        self.assertIn('Missing image data', str(cm.exception))
    
    def test_input_validation_file_size(self):
        """Test file size validation"""
        validation_rules = {'max_size': 50}  # Very small limit
        
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('test_image', self.sample_image_data, validation_rules)
        
        self.assertIn('exceeds maximum allowed size', str(cm.exception))
    
    def test_input_validation_format(self):
        """Test format validation"""
        validation_rules = {'formats': ['.jpg', '.jpeg']}  # Only allow JPEG
        
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('test_image', self.sample_image_data, validation_rules)
        
        self.assertIn('Unsupported image format', str(cm.exception))
    
    def test_output_schema_generation(self):
        """Test output schema generation"""
        field = {
            'type': 'image',
            'name': 'test_image',
            'label': 'Test Image',
            'description': 'Upload a test image',
            'required': False,
            'configuration': {
                'multi': False,
                'formats': ['.png', '.jpg']
            },
            'validation': {
                'max_size': 5242880,
                'max_width': 1024,
                'max_height': 1024
            }
        }
        
        schema = self.image_field.output(field)
        
        self.assertEqual(schema['type'], 'image')
        self.assertEqual(schema['name'], 'test_image')
        self.assertEqual(schema['title'], 'Test Image')
        self.assertFalse(schema['multiple'])
        self.assertIn('accept', schema)
        self.assertIn('validation', schema)
        self.assertEqual(schema['validation']['maxSize'], 5242880)
    
    def test_output_multiple_images(self):
        """Test output schema for multiple images"""
        field = {
            'type': 'image',
            'name': 'test_images',
            'label': 'Test Images',
            'configuration': {'multi': True}
        }
        
        schema = self.image_field.output(field)
        self.assertTrue(schema['multiple'])
    
    def test_get_accept_string(self):
        """Test MIME type accept string generation"""
        # Test default formats
        accept_string = self.image_field._get_accept_string({})
        self.assertIn('image/png', accept_string)
        self.assertIn('image/jpeg', accept_string)
        
        # Test specific formats
        config = {'formats': ['.png', '.jpg']}
        accept_string = self.image_field._get_accept_string(config)
        self.assertIn('image/png', accept_string)
        self.assertIn('image/jpeg', accept_string)
        self.assertNotIn('image/gif', accept_string)
    
    # --- Multi-item mode ---

    def test_input_multi_requires_a_list(self):
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('refs', 'uploads/a.png', {'multi': True})
        self.assertIn('must be a list', str(cm.exception))

    def test_input_multi_passthrough_media_refs_with_labels(self):
        items = [
            {'path': 'uploads/a.png', 'relative_path': 'uploads/a.png', 'type': 'image', 'label': '  Hero  '},
            {'path': 'uploads/b.png', 'relative_path': 'uploads/b.png', 'type': 'image'},
        ]
        result = self.image_field.input('refs', items, {'multi': True})

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['label'], 'Hero')
        self.assertNotIn('label', result[1])
        self.assertEqual(result[0]['path'], 'uploads/a.png')

    def test_input_multi_label_is_capped_and_stripped(self):
        items = [{'path': 'uploads/a.png', 'type': 'image', 'label': '  ' + ('x' * 100) + '  '}]
        result = self.image_field.input('refs', items, {'multi': True})
        self.assertEqual(len(result[0]['label']), self.image_field.MAX_LABEL_LENGTH)
        self.assertEqual(result[0]['label'], 'x' * self.image_field.MAX_LABEL_LENGTH)

    def test_input_multi_non_string_label_is_dropped(self):
        items = [{'path': 'uploads/a.png', 'type': 'image', 'label': 123}]
        result = self.image_field.input('refs', items, {'multi': True})
        self.assertNotIn('label', result[0])

    def test_input_multi_item_missing_path_raises(self):
        """An item with no path/relative_path/url and no base64 `data`
        falls through to the legacy validator, which reports the specific
        problem (same message a single-mode dict without `data` gets - see
        test_input_missing_data)."""
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('refs', [{'type': 'image'}], {'multi': True})
        self.assertIn('Missing image data', str(cm.exception))

    def test_input_multi_item_with_url_only_passes_through(self):
        """A `url`-only dict (no path/relative_path) still counts as a
        locatable media reference and passes through untouched - mirrors
        bind_form's TestMediaContainment::test_media_ref_dict_without_path_keys_is_untouched."""
        items = [{'url': '/api/media/x.png', 'type': 'image'}]
        result = self.image_field.input('refs', items, {'multi': True})
        self.assertEqual(result, items)

    def test_input_multi_enforces_max_items(self):
        items = [{'path': f'uploads/{i}.png'} for i in range(3)]
        with self.assertRaises(ValueError) as cm:
            self.image_field.input('refs', items, {'multi': True, 'max_items': 2})
        self.assertIn('Too many items', str(cm.exception))

    def test_input_multi_string_path_items_pass_through(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.png', delete=False) as f:
            temp_path = f.name
        try:
            result = self.image_field.input('refs', [temp_path], {'multi': True})
            self.assertEqual(result, [temp_path])
        finally:
            os.unlink(temp_path)

    def test_input_multi_legacy_base64_item(self):
        result = self.image_field.input('refs', [self.sample_image_data], {'multi': True})
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0]['data'], bytes)

    def test_input_single_mode_unaffected_by_multi_support(self):
        """Bite-check: single-mode (no `multi` in validation_rules) behavior
        is byte-identical to before multi-item support was added."""
        result = self.image_field.input('test_image', self.sample_image_data)
        self.assertIsInstance(result, dict)
        self.assertIn('data', result)
        self.assertIsInstance(result['data'], bytes)

    def test_input_media_ref_dict_passes_through(self):
        """The real value shape MediaLoaderField sends (single mode) - not
        base64. This is the shape every real generation submission carries,
        now that Image.input() is registered in bind_form's
        _INPUT_VALIDATORS and runs live."""
        item = {
            'path': 'generations/2026-01-01/gen1/0.png',
            'relative_path': 'generations/2026-01-01/gen1/0.png',
            'url': '/api/media/generations/gen1/0.png',
            'name': '0.png',
            'type': 'image',
        }
        result = self.image_field.input('source_image', item)
        self.assertEqual(result, item)

    def test_input_relative_path_string_is_not_existence_checked(self):
        """A storage-relative path has no meaningful cwd-relative existence
        check - verifying it is bind_form's `_check_media_containment` job,
        which has the storage root. Only an ABSOLUTE path that doesn't
        exist is rejected here (see test_input_path_string_not_exists)."""
        result = self.image_field.input('test_image', 'generations/2026-01-01/gen1/0.png')
        self.assertEqual(result, 'generations/2026-01-01/gen1/0.png')

    def test_output_emits_max_items_only_when_configured(self):
        field_with_cap = {
            'type': 'image', 'name': 'refs', 'label': 'Refs',
            'configuration': {'multi': True, 'max_items': 4},
        }
        field_without_cap = {
            'type': 'image', 'name': 'single', 'label': 'Single',
            'configuration': {},
        }
        self.assertEqual(self.image_field.output(field_with_cap)['max_items'], 4)
        self.assertNotIn('max_items', self.image_field.output(field_without_cap))

    def test_output_echoes_max_resolution_only_when_configured(self):
        """`max_resolution` reaches the rendered schema (top-level, like
        `max_items`) so the frontend can enforce it before upload, not just
        server-side in bind_form."""
        field_with_cap = {
            'type': 'image', 'name': 'refs', 'label': 'Refs',
            'configuration': {'max_resolution': 2048},
        }
        field_without_cap = {
            'type': 'image', 'name': 'single', 'label': 'Single', 'configuration': {},
        }
        self.assertEqual(self.image_field.output(field_with_cap)['max_resolution'], 2048)
        self.assertNotIn('max_resolution', self.image_field.output(field_without_cap))

    def test_validate_image_dimensions(self):
        """Test image dimension validation"""
        image_data = self.sample_image_data.copy()
        image_data.update({'width': 2000, 'height': 1500})
        
        rules = {'max_width': 1024, 'max_height': 1024}
        errors = self.image_field._validate_image(image_data, rules)
        
        self.assertEqual(len(errors), 2)  # Both width and height exceed limits
        self.assertTrue(any('width exceeds maximum' in error for error in errors))
        self.assertTrue(any('height exceeds maximum' in error for error in errors))


if __name__ == '__main__':
    unittest.main()