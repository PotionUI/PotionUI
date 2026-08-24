import unittest
from unittest.mock import Mock

from src.features.fields.container import Container


class TestContainerField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.field_factory = Mock()
        self.container_field = Container(self.preset_loader, self.field_factory)
    
    def test_can_handle(self):
        """Test that container field handles container types"""
        container_types = ['tabs', 'tab', 'row', 'group', 'accordion']
        
        for container_type in container_types:
            self.assertTrue(self.container_field.can_handle(container_type))
        
        # Test non-container types
        self.assertFalse(self.container_field.can_handle('select'))
        self.assertFalse(self.container_field.can_handle('image'))
        self.assertFalse(self.container_field.can_handle('slider'))
    
    
    def test_output_tabs_container(self):
        """Test output for tabs container"""
        # Mock child fields
        child1 = {'type': 'select', 'name': 'child1'}
        child2 = {'type': 'slider', 'name': 'child2'}
        
        field = {
            'type': 'tabs',
            'name': 'test_tabs',
            'label': 'Test Tabs',
            'children': [child1, child2]
        }
        
        # Mock field factory responses
        self.field_factory.map_field.side_effect = [
            {'type': 'select', 'name': 'child1', 'mapped': True},
            {'type': 'slider', 'name': 'child2', 'mapped': True}
        ]
        
        schema = self.container_field.output(field, 'test_preset')
        
        self.assertEqual(schema['type'], 'tabs')
        self.assertEqual(schema['name'], 'test_tabs')
        self.assertEqual(schema['title'], 'Test Tabs')
        
        # Check children processing
        self.assertIn('children', schema)
        self.assertEqual(len(schema['children']), 2)
        
        # Verify field factory was called for each child
        self.assertEqual(self.field_factory.map_field.call_count, 2)
        self.field_factory.map_field.assert_any_call(child1, 'test_preset')
        self.field_factory.map_field.assert_any_call(child2, 'test_preset')
    
    def test_output_tab_container_with_label(self):
        """Test output for tab container (should include label)"""
        field = {
            'type': 'tab',
            'name': 'test_tab',
            'label': 'Test Tab Label',
            'children': []
        }
        
        schema = self.container_field.output(field)
        
        self.assertEqual(schema['type'], 'tab')
        self.assertEqual(schema['label'], 'Test Tab Label')
    
    def test_output_accordion_container_with_label(self):
        """Test output for accordion container (should include label)"""
        field = {
            'type': 'accordion',
            'name': 'test_accordion',
            'label': 'Test Accordion Label',
            'children': []
        }
        
        schema = self.container_field.output(field)
        
        self.assertEqual(schema['type'], 'accordion')
        self.assertEqual(schema['label'], 'Test Accordion Label')
    
    def test_output_row_container_no_label(self):
        """Test output for row container (should not include label)"""
        field = {
            'type': 'row',
            'name': 'test_row',
            'label': 'This Label Should Not Appear',
            'children': []
        }
        
        schema = self.container_field.output(field)
        
        self.assertEqual(schema['type'], 'row')
        # Label should not be included for row type
        self.assertNotIn('label', schema)
    
    def test_output_group_container_with_label(self):
        """Test output for group container (should include label for visual organization)"""
        field = {
            'type': 'group',
            'name': 'test_group',
            'label': 'Models',
            'children': []
        }

        schema = self.container_field.output(field)

        self.assertEqual(schema['type'], 'group')
        # Label should be included for group type (groups need labels for visual organization)
        self.assertIn('label', schema)
        self.assertEqual(schema['label'], 'Models')
    
    def test_get_children_object_format(self):
        """Test getting children from object with children attribute"""
        mock_field = Mock()
        mock_field.children = ['child1', 'child2']
        mock_field.configuration = {}  # Add empty configuration to avoid Mock issues
        
        children = self.container_field._get_children(mock_field)
        self.assertEqual(children, ['child1', 'child2'])
        
        # Test with None children
        mock_field.children = None
        mock_field.configuration = {}  # Add empty configuration to avoid Mock issues
        children = self.container_field._get_children(mock_field)
        self.assertEqual(children, [])
    
    def test_get_children_dict_format(self):
        """Test getting children from dictionary format"""
        field_dict = {
            'children': ['child1', 'child2', 'child3']
        }
        
        children = self.container_field._get_children(field_dict)
        self.assertEqual(children, ['child1', 'child2', 'child3'])
        
        # Test with missing children key
        field_dict = {}
        children = self.container_field._get_children(field_dict)
        self.assertEqual(children, [])
    
    def test_output_without_field_factory(self):
        """Test output when no field factory is provided"""
        container_no_factory = Container(self.preset_loader, None)
        
        field = {
            'type': 'tabs',
            'name': 'test_tabs',
            'children': [{'type': 'select', 'name': 'child1'}]
        }
        
        schema = container_no_factory.output(field)
        
        # Should still create base schema but no child processing
        self.assertEqual(schema['type'], 'tabs')
        self.assertEqual(schema['children'], [])  # Empty because no factory
    
    def test_nested_containers(self):
        """Test nested container structures"""
        # Create nested structure: tabs -> tab -> row -> select
        select_field = {'type': 'select', 'name': 'nested_select'}
        row_field = {'type': 'row', 'name': 'nested_row', 'children': [select_field]}
        tab_field = {'type': 'tab', 'name': 'nested_tab', 'children': [row_field]}
        tabs_field = {'type': 'tabs', 'name': 'nested_tabs', 'children': [tab_field]}
        
        # Mock field factory to return processed schemas
        self.field_factory.map_field.side_effect = [
            {'type': 'tab', 'name': 'nested_tab', 'children': [
                {'type': 'row', 'name': 'nested_row', 'children': [
                    {'type': 'select', 'name': 'nested_select'}
                ]}
            ]}
        ]
        
        schema = self.container_field.output(tabs_field, 'test_preset')
        
        self.assertEqual(schema['type'], 'tabs')
        self.assertEqual(len(schema['children']), 1)
        
        # Verify field factory was called
        self.field_factory.map_field.assert_called_once_with(tab_field, 'test_preset')
    
    def test_empty_children_list(self):
        """Test container with empty children list"""
        field = {
            'type': 'tabs',
            'name': 'empty_tabs',
            'children': []
        }
        
        schema = self.container_field.output(field)
        
        self.assertEqual(schema['type'], 'tabs')
        self.assertEqual(schema['children'], [])
        
        # Field factory should not be called
        self.field_factory.map_field.assert_not_called()


if __name__ == '__main__':
    unittest.main()