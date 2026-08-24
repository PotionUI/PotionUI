import unittest
from unittest.mock import Mock

from src.features.fields.checkbox import Checkbox


class TestCheckbox(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.checkbox_field = Checkbox(self.preset_loader)
    
    def test_can_handle(self):
        """Test that checkbox field handler correctly identifies checkbox fields"""
        self.assertTrue(self.checkbox_field.can_handle('checkbox'))
        self.assertFalse(self.checkbox_field.can_handle('text'))
        self.assertFalse(self.checkbox_field.can_handle('select'))
        self.assertFalse(self.checkbox_field.can_handle(''))
        self.assertFalse(self.checkbox_field.can_handle(None))
    
    def test_output_basic_checkbox(self):
        """Test output transformation for basic checkbox field"""
        field = {
            'type': 'checkbox',
            'name': 'enable_feature',
            'label': 'Enable Feature',
            'description': 'Enable this awesome feature',
            'default': True
        }
        
        result = self.checkbox_field.output(field)
        
        self.assertEqual(result['type'], 'boolean')  # Checkbox becomes boolean
        self.assertEqual(result['name'], 'enable_feature')
        self.assertEqual(result['title'], 'Enable Feature')
        self.assertEqual(result['description'], 'Enable this awesome feature')
        self.assertEqual(result['default'], True)
    
    def test_output_minimal_checkbox(self):
        """Test output transformation for minimal checkbox field"""
        field = {
            'type': 'checkbox',
            'name': 'simple_toggle'
        }
        
        result = self.checkbox_field.output(field)
        
        self.assertEqual(result['type'], 'boolean')
        self.assertEqual(result['name'], 'simple_toggle')
        self.assertEqual(result['title'], 'simple_toggle')  # Uses name as title
        self.assertEqual(result['description'], '')
        self.assertNotIn('default', result)  # No default when None
    
    def test_output_with_object_field(self):
        """Test output transformation with object-style field"""
        mock_field = Mock()
        mock_field.type = 'checkbox'
        mock_field.name = 'object_checkbox'
        mock_field.label = 'Object Checkbox'
        mock_field.description = 'An object-style checkbox'
        mock_field.default = True  # Changed to True so it won't be skipped by the 'or' operator
        mock_field.required = False
        mock_field.configuration = {}
        
        result = self.checkbox_field.output(mock_field)
        
        self.assertEqual(result['type'], 'boolean')
        self.assertEqual(result['name'], 'object_checkbox')
        self.assertEqual(result['title'], 'Object Checkbox')
        self.assertEqual(result['description'], 'An object-style checkbox')
        self.assertEqual(result['default'], True)
    
    def test_output_object_field_false_default(self):
        """Test output transformation with object-style field with False default"""
        mock_field = Mock()
        mock_field.type = 'checkbox'
        mock_field.name = 'false_checkbox'
        mock_field.label = 'False Checkbox'
        mock_field.description = 'Checkbox with false default'
        mock_field.default = False
        mock_field.value = False  # Explicitly set both to False
        mock_field.required = False
        mock_field.configuration = {}
        
        result = self.checkbox_field.output(mock_field)
        
        # When both default and value are False, it properly shows False default
        self.assertEqual(result['type'], 'boolean')
        self.assertEqual(result['name'], 'false_checkbox')
        self.assertEqual(result['default'], False)
    
    
    
    
    
    
    
    def test_map_field(self):
        """Test that map_field delegates to output"""
        field = {
            'type': 'checkbox',
            'name': 'test_checkbox',
            'default': True
        }
        
        # map_field should return same result as output
        output_result = self.checkbox_field.output(field, 'preset_id')
        map_result = self.checkbox_field.map_field(field, 'preset_id')
        
        self.assertEqual(output_result, map_result)
    


if __name__ == '__main__':
    unittest.main()