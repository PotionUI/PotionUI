import unittest
from unittest.mock import Mock

from src.features.fields.checkbox_group import CheckboxGroup


class TestCheckboxGroupField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.checkbox_field = CheckboxGroup(self.preset_loader)
    
    def test_can_handle(self):
        """Test that checkbox group field handles 'checkbox_group' type"""
        self.assertTrue(self.checkbox_field.can_handle('checkbox_group'))
        self.assertFalse(self.checkbox_field.can_handle('select'))
        self.assertFalse(self.checkbox_field.can_handle('slider'))
    
    
    
    
    
    
    
    def test_output_schema_generation(self):
        """Test output schema generation"""
        field = {
            'type': 'checkbox_group',
            'name': 'test_checkbox_group',
            'label': 'Test Checkbox Group',
            'description': 'A test checkbox group field',
            'configuration': {
                'options': [
                    {'label': 'Option 1', 'value': 'opt1'},
                    {'label': 'Option 2', 'value': 'opt2'},
                    {'label': 'Option 3', 'value': 'opt3'}
                ]
            }
        }
        
        schema = self.checkbox_field.output(field)
        
        self.assertEqual(schema['type'], 'checkbox_group')
        self.assertEqual(schema['name'], 'test_checkbox_group')
        self.assertEqual(schema['title'], 'Test Checkbox Group')
        self.assertEqual(schema['description'], 'A test checkbox group field')
        
        # Check items type
        self.assertIn('items', schema)
        self.assertEqual(schema['items']['type'], 'string')
        
        # Check options
        self.assertIn('options', schema)
        self.assertEqual(len(schema['options']), 3)
        self.assertEqual(schema['options'][0]['label'], 'Option 1')
        self.assertEqual(schema['options'][0]['value'], 'opt1')
    
    def test_output_empty_options(self):
        """Test output with empty options"""
        field = {
            'type': 'checkbox_group',
            'name': 'empty_checkbox',
            'label': 'Empty Checkbox',
            'configuration': {}
        }
        
        schema = self.checkbox_field.output(field)
        self.assertEqual(schema['options'], [])
    
    def test_output_with_default_value(self):
        """Test output schema with default value"""
        field = {
            'type': 'checkbox_group',
            'name': 'default_checkbox',
            'label': 'Default Checkbox',
            'default': ['opt1', 'opt3'],
            'configuration': {
                'options': [
                    {'label': 'Option 1', 'value': 'opt1'},
                    {'label': 'Option 2', 'value': 'opt2'},
                    {'label': 'Option 3', 'value': 'opt3'}
                ]
            }
        }
        
        schema = self.checkbox_field.output(field)
        self.assertEqual(schema['default'], ['opt1', 'opt3'])
    
    def test_get_checkbox_options(self):
        """Test checkbox options extraction"""
        # Test with options
        config = {
            'options': [
                {'label': 'First', 'value': 'first'},
                {'label': 'Second', 'value': 'second'}
            ]
        }
        options = self.checkbox_field._get_checkbox_options(config)
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], 'First')
        
        # Test without options
        config = {}
        options = self.checkbox_field._get_checkbox_options(config)
        self.assertEqual(options, [])
    
    


if __name__ == '__main__':
    unittest.main()