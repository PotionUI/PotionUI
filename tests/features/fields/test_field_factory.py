import unittest
from unittest.mock import Mock

from src.features.fields.field_factory import FieldFactory, DefaultField


class TestFieldFactory(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.field_factory = FieldFactory(self.preset_loader)
    
    def test_initialization(self):
        """Test that field factory initializes with all field types"""
        self.assertIsNotNone(self.field_factory.fields)
        self.assertGreater(len(self.field_factory.fields), 0)
        
        # Check that DefaultField is last (fallback)
        self.assertIsInstance(self.field_factory.fields[-1], DefaultField)
    
    def test_map_field_select(self):
        """Test mapping select field"""
        field = {
            'type': 'select',
            'name': 'test_select',
            'label': 'Test Select',
            'configuration': {
                'options': [{'label': 'Option 1', 'value': 'opt1'}]
            }
        }
        
        schema = self.field_factory.map_field(field)
        
        self.assertEqual(schema['type'], 'select')
        self.assertEqual(schema['name'], 'test_select')
        self.assertIn('options', schema)
    
    def test_map_field_slider(self):
        """Test mapping slider field"""
        field = {
            'type': 'slider',
            'name': 'test_slider',
            'configuration': {
                'min': 0,
                'max': 100,
                'step': 1
            }
        }
        
        schema = self.field_factory.map_field(field)
        
        self.assertEqual(schema['type'], 'slider')
        self.assertEqual(schema['minimum'], 0)
        self.assertEqual(schema['maximum'], 100)
        self.assertEqual(schema['step'], 1)

    def test_map_field_seed(self):
        """Test mapping seed field"""
        field = {
            'type': 'seed',
            'name': 'test_seed',
            'configuration': {
                'min': -1,
                'max': 4294967295,
                'step': 1
            }
        }

        schema = self.field_factory.map_field(field)

        self.assertEqual(schema['type'], 'seed')
        self.assertEqual(schema['minimum'], -1)
        self.assertEqual(schema['maximum'], 4294967295)
        self.assertEqual(schema['step'], 1)
        self.assertTrue(schema['is_seed_field'])

    def test_map_field_image(self):
        """Test mapping image field"""
        field = {
            'type': 'image',
            'name': 'test_image',
            'configuration': {
                'multi': False
            },
            'validation': {
                'max_size': 5242880
            }
        }
        
        schema = self.field_factory.map_field(field)
        
        self.assertEqual(schema['type'], 'image')
        self.assertFalse(schema['multiple'])
        self.assertIn('accept', schema)
        self.assertIn('validation', schema)
    
    def test_map_field_checkbox_group(self):
        """Test mapping checkbox group field"""
        field = {
            'type': 'checkbox_group',
            'name': 'test_checkbox',
            'configuration': {
                'options': [
                    {'label': 'Option 1', 'value': 'opt1'},
                    {'label': 'Option 2', 'value': 'opt2'}
                ]
            }
        }
        
        schema = self.field_factory.map_field(field)
        
        self.assertEqual(schema['type'], 'checkbox_group')
        self.assertIn('items', schema)
        self.assertEqual(schema['items']['type'], 'string')
        self.assertIn('options', schema)
    
    def test_map_field_container(self):
        """Test mapping container field"""
        field = {
            'type': 'tabs',
            'name': 'test_tabs',
            'children': [
                {'type': 'select', 'name': 'child_select'}
            ]
        }
        
        schema = self.field_factory.map_field(field)
        
        self.assertEqual(schema['type'], 'tabs')
        self.assertIn('children', schema)

    def test_map_field_group_container(self):
        """Test mapping group container field"""
        field = {
            'type': 'group',
            'label': 'Settings Group',
            'children': [
                {'type': 'seed', 'name': 'seed'},
                {'type': 'slider', 'name': 'strength'}
            ]
        }

        schema = self.field_factory.map_field(field)

        self.assertEqual(schema['type'], 'group')
        self.assertEqual(schema['label'], 'Settings Group')
        self.assertIn('children', schema)
        # Container should not have a name to prevent nested form state
        self.assertIsNone(schema.get('name'))

    def test_map_field_group_with_nested_row(self):
        """Test mapping group container with nested row container"""
        field = {
            'type': 'group',
            'label': 'Quality & Resolution',
            'children': [
                {
                    'type': 'row',
                    'children': [
                        {'type': 'seed', 'name': 'seed'},
                        {'type': 'slider', 'name': 'quality'}
                    ]
                },
                {'type': 'slider', 'name': 'strength'}
            ]
        }

        schema = self.field_factory.map_field(field)

        self.assertEqual(schema['type'], 'group')
        self.assertEqual(schema['label'], 'Quality & Resolution')
        self.assertIn('children', schema)
        self.assertEqual(len(schema['children']), 2)
        # First child should be the row container
        self.assertEqual(schema['children'][0]['type'], 'row')
        # Second child should be the slider
        self.assertEqual(schema['children'][1]['type'], 'slider')

    def test_map_field_unknown_type(self):
        """Test mapping unknown field type (should use DefaultField)"""
        field = {
            'type': 'unknown_type',
            'name': 'test_unknown',
            'label': 'Test Unknown'
        }
        
        schema = self.field_factory.map_field(field)
        
        # Should create basic schema using DefaultField
        self.assertEqual(schema['type'], 'unknown_type')
        self.assertEqual(schema['name'], 'test_unknown')
        self.assertEqual(schema['title'], 'Test Unknown')
    
    def test_get_field_type_object(self):
        """Test getting field type from object with type attribute"""
        mock_field = Mock()
        mock_field.type = 'select'
        
        field_type = self.field_factory._get_field_type(mock_field)
        self.assertEqual(field_type, 'select')
    
    def test_get_field_type_dict(self):
        """Test getting field type from dictionary"""
        field_dict = {'type': 'slider'}
        
        field_type = self.field_factory._get_field_type(field_dict)
        self.assertEqual(field_type, 'slider')
        
        # Test with missing type
        field_dict = {'name': 'test'}
        field_type = self.field_factory._get_field_type(field_dict)
        self.assertEqual(field_type, 'unknown')
    
    def test_unregistered_type_falls_back_to_default(self):
        """Dispatch is registry-driven: an unregistered type never raises, it
        resolves to the default (fallback) field definition."""
        schema = self.field_factory.map_field({'type': 'totally_made_up_type', 'name': 'x'})
        self.assertEqual(schema['type'], 'totally_made_up_type')
        self.assertEqual(schema['name'], 'x')


class TestDefaultField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.default_field = DefaultField(self.preset_loader)
    
    def test_can_handle_any_type(self):
        """Test that DefaultField can handle any field type"""
        self.assertTrue(self.default_field.can_handle('any_type'))
        self.assertTrue(self.default_field.can_handle('unknown'))
        self.assertTrue(self.default_field.can_handle(''))
    
    def test_map_field_basic_schema(self):
        """Test that DefaultField creates basic schema"""
        field = {
            'type': 'custom_type',
            'name': 'custom_field',
            'label': 'Custom Field',
            'description': 'A custom field type'
        }
        
        schema = self.default_field.map_field(field)
        
        self.assertEqual(schema['type'], 'custom_type')
        self.assertEqual(schema['name'], 'custom_field')
        self.assertEqual(schema['title'], 'Custom Field')
        self.assertEqual(schema['description'], 'A custom field type')


if __name__ == '__main__':
    unittest.main()