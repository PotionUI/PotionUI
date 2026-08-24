import unittest
from unittest.mock import Mock

from src.features.fields.file import File


class TestFile(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.file_field = File(self.preset_loader)
    
    def test_can_handle(self):
        """Test that file field handler correctly identifies file fields"""
        self.assertTrue(self.file_field.can_handle('file'))
        self.assertFalse(self.file_field.can_handle('text'))
        self.assertFalse(self.file_field.can_handle('image'))
        self.assertFalse(self.file_field.can_handle('checkbox'))
        self.assertFalse(self.file_field.can_handle(''))
        self.assertFalse(self.file_field.can_handle(None))
    
    def test_output_basic_file(self):
        """Test output transformation for basic file field"""
        field = {
            'type': 'file',
            'name': 'upload_file',
            'label': 'Upload File',
            'description': 'Upload a file',
            'default': None
        }
        
        result = self.file_field.output(field)
        
        self.assertEqual(result['type'], 'file')
        self.assertEqual(result['name'], 'upload_file')
        self.assertEqual(result['title'], 'Upload File')
        self.assertEqual(result['description'], 'Upload a file')
        self.assertNotIn('default', result)  # No default when None
    
    def test_output_with_configuration(self):
        """Test output transformation with file configuration"""
        field = {
            'type': 'file',
            'name': 'document',
            'label': 'Upload Document',
            'configuration': {
                'accept': '.pdf,.doc,.docx',
                'multiple': True,
                'max_size': 10485760  # 10MB
            }
        }
        
        result = self.file_field.output(field)
        
        self.assertEqual(result['type'], 'file')
        self.assertEqual(result['name'], 'document')
        self.assertEqual(result['accept'], '.pdf,.doc,.docx')
        self.assertTrue(result['multiple'])
        self.assertEqual(result['max_size'], 10485760)
    
    def test_output_partial_configuration(self):
        """Test output with partial configuration"""
        field = {
            'type': 'file',
            'name': 'image_upload',
            'configuration': {
                'accept': 'image/*'
                # No multiple or max_size
            }
        }
        
        result = self.file_field.output(field)
        
        self.assertEqual(result['type'], 'file')
        self.assertEqual(result['accept'], 'image/*')
        self.assertNotIn('multiple', result)
        self.assertNotIn('max_size', result)
    
    def test_output_empty_configuration(self):
        """Test output with empty configuration"""
        field = {
            'type': 'file',
            'name': 'basic_file',
            'configuration': {}
        }
        
        result = self.file_field.output(field)
        
        self.assertEqual(result['type'], 'file')
        self.assertNotIn('accept', result)
        self.assertNotIn('multiple', result)
        self.assertNotIn('max_size', result)
    
    def test_output_with_object_field(self):
        """Test output transformation with object-style field"""
        mock_field = Mock()
        mock_field.type = 'file'
        mock_field.name = 'object_file'
        mock_field.label = 'Object File Upload'
        mock_field.description = 'An object-style file field'
        mock_field.default = '/default/path.txt'
        mock_field.required = False
        mock_field.configuration = {
            'accept': '.txt',
            'multiple': False
        }
        
        result = self.file_field.output(mock_field)
        
        self.assertEqual(result['type'], 'file')
        self.assertEqual(result['name'], 'object_file')
        self.assertEqual(result['title'], 'Object File Upload')
        self.assertEqual(result['description'], 'An object-style file field')
        self.assertEqual(result['default'], '/default/path.txt')
        self.assertEqual(result['accept'], '.txt')
        self.assertFalse(result['multiple'])
    
    
    
    
    
    
    def test_map_field(self):
        """Test that map_field delegates to output"""
        field = {
            'type': 'file',
            'name': 'test_file',
            'configuration': {
                'accept': '*/*',
                'multiple': True
            }
        }
        
        # map_field should return same result as output
        output_result = self.file_field.output(field, 'preset_id')
        map_result = self.file_field.map_field(field, 'preset_id')
        
        self.assertEqual(output_result, map_result)
    
    def test_complex_file_configurations(self):
        """Test complex file field configurations"""
        field = {
            'type': 'file',
            'name': 'complex_upload',
            'label': 'Complex File Upload',
            'configuration': {
                'accept': '.jpg,.jpeg,.png,.gif,.webp',
                'multiple': True,
                'max_size': 5242880,  # 5MB
                'custom_option': 'should_be_ignored'  # Should not be in output
            }
        }
        
        result = self.file_field.output(field)
        
        self.assertEqual(result['type'], 'file')
        self.assertEqual(result['accept'], '.jpg,.jpeg,.png,.gif,.webp')
        self.assertTrue(result['multiple'])
        self.assertEqual(result['max_size'], 5242880)
        self.assertNotIn('custom_option', result)  # Only known options are included
    


if __name__ == '__main__':
    unittest.main()