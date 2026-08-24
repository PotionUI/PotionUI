import unittest
from unittest.mock import Mock, patch
from src.features.fields.llm import LLMField


class TestLLMField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.field = LLMField(self.preset_loader)

    def test_can_handle(self):
        """Test field type matching"""
        self.assertTrue(self.field.can_handle('llm'))
        self.assertFalse(self.field.can_handle('text'))
        self.assertFalse(self.field.can_handle('select'))
        self.assertFalse(self.field.can_handle('llm_field'))

    def test_output_schema_full_field(self):
        """Test output schema generation with all components enabled"""
        field_config = {
            'type': 'llm',
            'name': 'llm_field',
            'label': 'LLM Configuration',
            'configuration': {
                'show_llm_select': True,
                'show_prompt': True
            }
        }

        schema = self.field.output(field_config)

        self.assertEqual(schema['type'], 'llm')
        self.assertEqual(schema['name'], 'llm_field')
        self.assertEqual(schema['title'], 'LLM Configuration')
        self.assertIn('llm_options', schema)
        # LLM options are populated client-side from the API.
        self.assertEqual(schema['llm_options'], [])
        self.assertEqual(schema['default_llm_id'], '')
        self.assertEqual(schema['configuration']['show_llm_select'], True)
        self.assertEqual(schema['configuration']['show_prompt'], True)

    def test_output_schema_llm_only(self):
        """Test output schema with only LLM select enabled"""
        field_config = {
            'type': 'llm',
            'name': 'llm_select',
            'label': 'Select LLM',
            'configuration': {
                'show_llm_select': True,
                'show_prompt': False
            }
        }

        schema = self.field.output(field_config)

        self.assertIn('llm_options', schema)
        self.assertEqual(schema['configuration']['show_llm_select'], True)
        self.assertEqual(schema['configuration']['show_prompt'], False)

    def test_output_default_llm_id_none_when_not_allow_empty(self):
        """default_llm_id is None when empty selection is disallowed"""
        field_config = {
            'type': 'llm',
            'name': 'llm_field',
            'configuration': {'show_llm_select': True, 'allow_empty': False}
        }

        schema = self.field.output(field_config)

        self.assertIsNone(schema['default_llm_id'])


    def test_configuration_specs(self):
        """Test configuration specifications"""
        config_specs = LLMField.configuration()

        self.assertGreater(len(config_specs), 0)

        # Check that all expected parameters are present
        param_names = {spec.name for spec in config_specs}
        self.assertIn('show_llm_select', param_names)
        self.assertIn('show_prompt', param_names)
        self.assertIn('tooltip', param_names)

        # Check parameter details for boolean configs
        bool_specs = [s for s in config_specs if s.name != 'tooltip']
        for spec in bool_specs:
            self.assertEqual(spec.param_type, bool)
            self.assertIsNotNone(spec.description)

        # Check tooltip spec
        tooltip_spec = next(s for s in config_specs if s.name == 'tooltip')
        self.assertEqual(tooltip_spec.param_type, str)
        self.assertEqual(tooltip_spec.default, None)
        self.assertFalse(tooltip_spec.required)

    def test_examples(self):
        """Test example configurations"""
        examples = LLMField.examples()

        self.assertGreater(len(examples), 0)

        for example in examples:
            self.assertIsNotNone(example.title)
            self.assertIsNotNone(example.description)
            self.assertIsNotNone(example.yaml_config)
            self.assertIsNotNone(example.rendered_output)

        # Check specific examples
        titles = {ex.title for ex in examples}
        self.assertIn('Full LLM Field', titles)
        self.assertIn('Simple LLM Selector', titles)

    def test_map_field_delegates_to_output(self):
        """Test that map_field delegates to output method"""
        with patch.object(self.field, 'output') as mock_output:
            mock_output.return_value = {'test': 'data'}

            field_config = {'type': 'llm', 'name': 'test'}
            result = self.field.map_field(field_config, 'preset1')

            mock_output.assert_called_once_with(field_config, 'preset1')
            self.assertEqual(result, {'test': 'data'})

    def test_output_with_tooltip(self):
        """Test that tooltip is included in schema when provided"""
        field_config = {
            'type': 'llm',
            'name': 'llm_field',
            'label': 'LLM Configuration',
            'configuration': {
                'show_llm_select': True,
                'tooltip': 'This is helpful information about the LLM field'
            }
        }

        schema = self.field.output(field_config)

        self.assertEqual(schema['tooltip'], 'This is helpful information about the LLM field')

    def test_output_without_tooltip(self):
        """Test that tooltip is not included in schema when not provided"""
        field_config = {
            'type': 'llm',
            'name': 'llm_field',
            'label': 'LLM Configuration',
            'configuration': {
                'show_llm_select': True
            }
        }

        schema = self.field.output(field_config)

        self.assertNotIn('tooltip', schema)


if __name__ == '__main__':
    unittest.main()
