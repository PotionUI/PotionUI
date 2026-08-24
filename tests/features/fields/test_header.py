import unittest
from unittest.mock import Mock

from src.features.fields.header import Header


class TestHeader(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.header_field = Header(self.preset_loader)

    def test_can_handle(self):
        """Test that header field handler correctly identifies header fields"""
        self.assertTrue(self.header_field.can_handle('header'))
        self.assertFalse(self.header_field.can_handle('text'))
        self.assertFalse(self.header_field.can_handle('checkbox'))
        self.assertFalse(self.header_field.can_handle(''))
        self.assertFalse(self.header_field.can_handle(None))

    def test_output_basic_header(self):
        """Test output transformation for basic header field"""
        field = {
            'type': 'header',
            'name': 'section_header',
            'label': 'Advanced Settings',
            'description': 'Configure advanced options'
        }

        result = self.header_field.output(field)

        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)  # Headers don't have names
        self.assertEqual(result['title'], 'Advanced Settings')
        self.assertEqual(result['description'], 'Configure advanced options')

    def test_output_minimal_header(self):
        """Test output transformation for minimal header field"""
        field = {
            'type': 'header',
            'label': 'Simple Header'
        }

        result = self.header_field.output(field)

        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)  # Headers don't have names
        self.assertEqual(result['title'], 'Simple Header')
        self.assertEqual(result['description'], '')
        self.assertNotIn('default', result)

    def test_output_with_variant_configuration(self):
        """Test output transformation with variant configuration"""
        field = {
            'type': 'header',
            'label': 'Large Header',
            'configuration': {
                'variant': 'h2'
            }
        }

        result = self.header_field.output(field)

        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)
        self.assertEqual(result['title'], 'Large Header')
        # Configuration should be preserved in output
        # (checked through base_schema behavior)

    def test_output_with_style_configuration(self):
        """Test output transformation with custom style"""
        field = {
            'type': 'header',
            'label': 'Styled Header',
            'configuration': {
                'variant': 'h4',
                'style': {
                    'color': '#0066cc',
                    'fontWeight': 'bold',
                    'marginTop': '20px'
                }
            }
        }

        result = self.header_field.output(field)

        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)
        self.assertEqual(result['title'], 'Styled Header')

    def test_output_with_object_field(self):
        """Test output transformation with object-style field"""
        mock_field = Mock()
        mock_field.type = 'header'
        mock_field.name = 'object_header'
        mock_field.label = 'Object Header'
        mock_field.description = 'An object-style header'
        mock_field.default = None
        mock_field.required = False
        mock_field.configuration = {'variant': 'h3'}

        result = self.header_field.output(mock_field)

        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)  # Name should be removed for headers
        self.assertEqual(result['title'], 'Object Header')
        self.assertEqual(result['description'], 'An object-style header')


    def test_map_field(self):
        """Test that map_field delegates to output"""
        field = {
            'type': 'header',
            'label': 'Test Header',
            'configuration': {'variant': 'h2'}
        }

        # map_field should return same result as output
        output_result = self.header_field.output(field, 'preset_id')
        map_result = self.header_field.map_field(field, 'preset_id')

        self.assertEqual(output_result, map_result)

    def test_configuration_specs(self):
        """Test configuration specifications"""
        config_specs = Header.configuration()

        self.assertGreater(len(config_specs), 0)

        # Check for variant configuration
        variant_spec = next((spec for spec in config_specs if spec.name == 'variant'), None)
        self.assertIsNotNone(variant_spec)
        self.assertEqual(variant_spec.param_type, str)
        self.assertEqual(variant_spec.default, 'h3')

        # Check for style configuration
        style_spec = next((spec for spec in config_specs if spec.name == 'style'), None)
        self.assertIsNotNone(style_spec)
        self.assertEqual(style_spec.param_type, dict)

    def test_examples(self):
        """Test example configurations"""
        examples = Header.examples()

        self.assertGreater(len(examples), 0)

        # Verify all examples have required properties
        for example in examples:
            self.assertIsNotNone(example.title)
            self.assertIsNotNone(example.description)
            self.assertIsNotNone(example.yaml_config)
            self.assertIsNotNone(example.rendered_output)
            self.assertIn('type', example.rendered_output)
            self.assertEqual(example.rendered_output['type'], 'header')

    def test_description(self):
        """Test field description"""
        description = Header.description()
        self.assertIsNotNone(description)
        self.assertIsInstance(description, str)
        self.assertGreater(len(description), 0)

    def test_name_removal_from_output(self):
        """Test that name is always removed from output schema"""
        # Test with name explicitly set
        field_with_name = {
            'type': 'header',
            'name': 'should_be_removed',
            'label': 'Header With Name'
        }

        result = self.header_field.output(field_with_name)
        self.assertNotIn('name', result)

        # Test without name
        field_without_name = {
            'type': 'header',
            'label': 'Header Without Name'
        }

        result = self.header_field.output(field_without_name)
        self.assertNotIn('name', result)

    def test_edge_cases(self):
        """Test edge cases for header field"""
        # Empty label
        field = {
            'type': 'header',
            'label': ''
        }
        result = self.header_field.output(field)
        self.assertEqual(result['type'], 'header')
        self.assertEqual(result['title'], '')

        # No label at all (using name)
        field = {
            'type': 'header',
            'name': 'header_name'
        }
        result = self.header_field.output(field)
        self.assertEqual(result['type'], 'header')
        self.assertNotIn('name', result)  # Still removed even when used as title


if __name__ == '__main__':
    unittest.main()
