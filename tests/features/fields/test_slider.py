import unittest
from unittest.mock import Mock

from src.features.fields.slider import Slider


class TestSliderField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.slider_field = Slider(self.preset_loader)
    
    def test_can_handle(self):
        """Test that slider field handles 'slider' type"""
        self.assertTrue(self.slider_field.can_handle('slider'))
        self.assertFalse(self.slider_field.can_handle('select'))
        self.assertFalse(self.slider_field.can_handle('image'))
    
    
    
    
    
    
    def test_output_schema_generation(self):
        """Test output schema generation"""
        field = {
            'type': 'slider',
            'name': 'test_slider',
            'label': 'Test Slider',
            'description': 'A test slider field',
            'default': 50,
            'configuration': {
                'min': 0,
                'max': 100,
                'step': 0.1
            }
        }
        
        schema = self.slider_field.output(field)
        
        self.assertEqual(schema['type'], 'slider')
        self.assertEqual(schema['name'], 'test_slider')
        self.assertEqual(schema['title'], 'Test Slider')
        self.assertEqual(schema['description'], 'A test slider field')
        self.assertEqual(schema['default'], 50)
        self.assertEqual(schema['minimum'], 0)
        self.assertEqual(schema['maximum'], 100)
        self.assertEqual(schema['step'], 0.1)
    
    def test_output_default_values(self):
        """Test output with default configuration values"""
        field = {
            'type': 'slider',
            'name': 'default_slider',
            'label': 'Default Slider',
            'configuration': {}  # Empty configuration
        }
        
        schema = self.slider_field.output(field)
        
        # Should use default values
        self.assertEqual(schema['minimum'], 0)   # Default min
        self.assertEqual(schema['maximum'], 100) # Default max
        self.assertEqual(schema['step'], 1)      # Default step
    
    def test_output_partial_configuration(self):
        """Test output with partial configuration"""
        field = {
            'type': 'slider',
            'name': 'partial_slider',
            'label': 'Partial Slider',
            'configuration': {
                'min': 10,
                'max': 50
                # No step specified
            }
        }
        
        schema = self.slider_field.output(field)
        
        self.assertEqual(schema['minimum'], 10)
        self.assertEqual(schema['maximum'], 50)
        self.assertEqual(schema['step'], 1)  # Default step
    
    def test_negative_ranges(self):
        """Test sliders with negative ranges"""
        field = {
            'type': 'slider',
            'name': 'negative_slider',
            'configuration': {
                'min': -100,
                'max': -10,
                'step': 5
            }
        }
        
        schema = self.slider_field.output(field)
        self.assertEqual(schema['minimum'], -100)
        self.assertEqual(schema['maximum'], -10)

    def test_decimal_steps(self):
        """Test sliders with decimal step values"""
        field = {
            'type': 'slider',
            'name': 'decimal_slider',
            'configuration': {
                'min': 0,
                'max': 1,
                'step': 0.01
            }
        }

        schema = self.slider_field.output(field)
        self.assertEqual(schema['step'], 0.01)


if __name__ == '__main__':
    unittest.main()