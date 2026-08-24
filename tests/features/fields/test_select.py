import unittest
import tempfile
import os
import yaml
from unittest.mock import Mock, patch
from pathlib import Path

from src.features.fields.select import Select


class TestSelectField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.select_field = Select(self.preset_loader)
        
        # Mock preset template
        self.mock_preset = Mock()
        self.mock_preset.path = '/test/presets/test_author/test_model/v1/main'
        
        self.preset_loader.preset_files_path = '/test/presets'
        self.preset_loader.presets = [self.mock_preset]
        self.mock_preset.id = 'test_preset_id'
    
    def test_can_handle(self):
        """Test that select field handles 'select' type"""
        self.assertTrue(self.select_field.can_handle('select'))
        self.assertFalse(self.select_field.can_handle('image'))
        self.assertFalse(self.select_field.can_handle('slider'))

    def test_configuration(self):
        """Test that configuration includes all expected parameters"""
        config_specs = Select.configuration()

        # Check that we have the expected configuration parameters
        config_names = [spec.name for spec in config_specs]
        expected_params = ['options', 'file', 'files']

        for param in expected_params:
            self.assertIn(param, config_names, f"Missing configuration parameter: {param}")

        # Check options parameter supports examples
        options_spec = next((spec for spec in config_specs if spec.name == 'options'), None)
        self.assertIsNotNone(options_spec)
        self.assertEqual(options_spec.param_type, list)

        # Check that example shows options with examples
        example_options = options_spec.example
        self.assertTrue(isinstance(example_options, list))
        self.assertTrue(any('example' in opt for opt in example_options if isinstance(opt, dict)))
    
    
    def test_output_static_options(self):
        """Test output with static options"""
        field = {
            'type': 'select',
            'name': 'test_select',
            'label': 'Test Select',
            'configuration': {
                'options': [
                    {'label': 'Option 1', 'value': 'opt1'},
                    {'label': 'Option 2', 'value': 'opt2'},
                    'simple_option'  # String format
                ]
            }
        }

        schema = self.select_field.output(field)

        self.assertEqual(schema['type'], 'select')
        self.assertEqual(schema['name'], 'test_select')
        self.assertEqual(schema['title'], 'Test Select')
        self.assertIn('options', schema)

        options = schema['options']
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['label'], 'Option 1')
        self.assertEqual(options[0]['value'], 'opt1')
        self.assertEqual(options[2]['label'], 'simple_option')
        self.assertEqual(options[2]['value'], 'simple_option')

    def test_output_static_options_with_examples(self):
        """Test output with static options that include examples"""
        field = {
            'type': 'select',
            'name': 'camera_arc',
            'label': 'Camera Arc',
            'configuration': {
                'options': [
                    {'label': 'Low Angle', 'value': 'low_angle', 'example': 'camera low angle <subject>'},
                    {'label': 'High Angle', 'value': 'high_angle', 'example': 'camera high angle looking down at <subject>'},
                    {'label': 'Eye Level', 'value': 'eye_level'}  # No example on this one
                ]
            }
        }

        schema = self.select_field.output(field)

        self.assertEqual(schema['type'], 'select')
        self.assertEqual(schema['name'], 'camera_arc')
        self.assertEqual(schema['title'], 'Camera Arc')
        self.assertIn('options', schema)

        options = schema['options']
        self.assertEqual(len(options), 3)

        # First option with example
        self.assertEqual(options[0]['label'], 'Low Angle')
        self.assertEqual(options[0]['value'], 'low_angle')
        self.assertEqual(options[0]['example'], 'camera low angle <subject>')

        # Second option with example
        self.assertEqual(options[1]['label'], 'High Angle')
        self.assertEqual(options[1]['value'], 'high_angle')
        self.assertEqual(options[1]['example'], 'camera high angle looking down at <subject>')

        # Third option without example
        self.assertEqual(options[2]['label'], 'Eye Level')
        self.assertEqual(options[2]['value'], 'eye_level')
        self.assertNotIn('example', options[2])
    
    def test_output_static_options_with_sub_label(self):
        """Test output with static options that include sub_label"""
        field = {
            'type': 'select',
            'name': 'speed_profile',
            'label': 'Speed Profile',
            'configuration': {
                'options': [
                    {'label': 'Turbo', 'value': 'turbo', 'sub_label': '8 steps, fastest'},
                    {'label': 'Quality', 'value': 'quality', 'sub_label': '25 steps, DPM++ 2M'},
                    {'label': 'Balanced', 'value': 'balanced'}  # No sub_label on this one
                ]
            }
        }

        schema = self.select_field.output(field)
        options = schema['options']
        self.assertEqual(len(options), 3)

        self.assertEqual(options[0]['sub_label'], '8 steps, fastest')
        self.assertEqual(options[1]['sub_label'], '25 steps, DPM++ 2M')
        self.assertNotIn('sub_label', options[2])

    def test_get_static_options_sub_label_absent_when_not_authored(self):
        """Test that sub_label key is absent entirely when not authored"""
        config = {
            'options': [
                {'label': 'Option 1', 'value': 'opt1'},
                {'label': 'Option 2', 'value': 'opt2', 'sub_label': ''},  # Empty string treated as absent
            ]
        }

        options = self.select_field._get_static_options(config)

        self.assertNotIn('sub_label', options[0])
        self.assertNotIn('sub_label', options[1])

    def test_get_static_options(self):
        """Test static options processing"""
        config = {
            'options': [
                {'label': 'Label 1', 'value': 'value1'},
                {'value': 'value2'},  # No label
                'string_option',
                123  # Non-string option
            ]
        }
        
        options = self.select_field._get_static_options(config)
        
        self.assertEqual(len(options), 4)
        self.assertEqual(options[0]['label'], 'Label 1')
        self.assertEqual(options[1]['label'], 'value2')  # Uses value as label
        self.assertEqual(options[2]['label'], 'string_option')
        self.assertEqual(options[3]['label'], '123')
    
    @patch('builtins.open')
    @patch('pathlib.Path.exists')
    @patch('src.platform.templating.TemplateProcessor.process_template')
    def test_get_file_options(self, mock_template, mock_exists, mock_open):
        """Test file-based options loading"""
        # Setup mocks
        mock_exists.return_value = True
        mock_template.return_value = '/resolved/path/options.yml'
        
        # Mock YAML file content
        yaml_content = {
            'options': [
                {'label': 'File Option 1', 'value': 'file_opt1'},
                {'label': 'File Option 2', 'value': 'file_opt2'}
            ]
        }
        mock_open.return_value.__enter__.return_value.read.return_value = yaml.dump(yaml_content)
        
        # Mock yaml.safe_load
        with patch('yaml.safe_load', return_value=yaml_content):
            config = {'file': {'path': '/test/path/options.yml'}}
            options = self.select_field._get_file_options(config, 'test_preset_id')
        
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], 'File Option 1')
        self.assertEqual(options[0]['value'], 'file_opt1')
    
    @patch('glob.glob')
    def test_get_filesystem_options(self, mock_glob):
        """Test filesystem scanning options"""
        self.select_field.template_processor = Mock()
        self.select_field.template_processor.process_template.return_value = '/resolved/models/path'
        mock_glob.return_value = [
            '/resolved/models/path/model1.safetensors',
            '/resolved/models/path/model2.safetensors'
        ]

        config = {'files': {'in': '/models/path'}}
        options = self.select_field._get_filesystem_options(config, 'test_preset_id')

        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], 'model1')
        self.assertEqual(options[0]['value'], 'model1.safetensors')
        self.assertEqual(options[1]['label'], 'model2')
        self.assertEqual(options[1]['value'], 'model2.safetensors')

    @patch('glob.glob')
    def test_get_filesystem_options_recursive(self, mock_glob):
        """Test filesystem scanning options with recursive enabled"""
        self.select_field.template_processor = Mock()
        self.select_field.template_processor.process_template.return_value = '/resolved/models/path'
        mock_glob.return_value = [
            '/resolved/models/path/model1.safetensors',
            '/resolved/models/path/subfolder/model2.safetensors',
            '/resolved/models/path/deep/nested/model3.safetensors'
        ]
        
        config = {'files': {'in': '/models/path', 'recursive': True}}
        options = self.select_field._get_filesystem_options(config, 'test_preset_id')
        
        # Check that glob was called with recursive pattern
        print("Actual calls:", mock_glob.call_args_list)
        mock_glob.assert_called_with('/resolved/models/path/**/*.safetensors', recursive=True)
        
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['label'], 'model1')
        self.assertEqual(options[0]['value'], 'model1.safetensors')
        self.assertEqual(options[1]['label'], 'subfolder/model2')
        self.assertEqual(options[1]['value'], 'subfolder/model2.safetensors')
        self.assertEqual(options[2]['label'], 'deep/nested/model3')
        self.assertEqual(options[2]['value'], 'deep/nested/model3.safetensors')
    
    def test_parse_yaml_options_list(self):
        """Test parsing YAML options from list format"""
        yaml_data = [
            {'label': 'Option A', 'value': 'opt_a'},
            {'value': 'opt_b'},
            'simple_option'
        ]

        options = self.select_field._parse_yaml_options(yaml_data)

        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['label'], 'Option A')
        self.assertEqual(options[1]['label'], 'opt_b')
        self.assertEqual(options[2]['value'], 'simple_option')

    def test_parse_yaml_options_with_examples(self):
        """Test parsing YAML options that include examples"""
        yaml_data = [
            {'label': 'Wide Shot', 'value': 'wide_shot', 'example': 'wide shot of <subject> showing environment'},
            {'label': 'Close Up', 'value': 'close_up', 'example': 'close up of <subject> face'},
            {'label': 'Medium Shot', 'value': 'medium_shot'}  # No example
        ]

        options = self.select_field._parse_yaml_options(yaml_data)

        self.assertEqual(len(options), 3)

        # First option with example
        self.assertEqual(options[0]['label'], 'Wide Shot')
        self.assertEqual(options[0]['value'], 'wide_shot')
        self.assertEqual(options[0]['example'], 'wide shot of <subject> showing environment')

        # Second option with example
        self.assertEqual(options[1]['label'], 'Close Up')
        self.assertEqual(options[1]['value'], 'close_up')
        self.assertEqual(options[1]['example'], 'close up of <subject> face')

        # Third option without example
        self.assertEqual(options[2]['label'], 'Medium Shot')
        self.assertEqual(options[2]['value'], 'medium_shot')
        self.assertNotIn('example', options[2])
    
    def test_parse_yaml_options_with_sub_label(self):
        """Test parsing YAML options (list format) that include sub_label"""
        yaml_data = [
            {'label': 'Turbo', 'value': 'turbo', 'sub_label': '8 steps, fastest'},
            {'label': 'Quality', 'value': 'quality', 'sub_label': '25 steps, DPM++ 2M'},
            {'label': 'Balanced', 'value': 'balanced'}  # No sub_label
        ]

        options = self.select_field._parse_yaml_options(yaml_data)

        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['sub_label'], '8 steps, fastest')
        self.assertEqual(options[1]['sub_label'], '25 steps, DPM++ 2M')
        self.assertNotIn('sub_label', options[2])

    def test_parse_yaml_options_dict_with_sub_label(self):
        """Test parsing YAML options (dict format) that include sub_label"""
        yaml_data = {
            'options': [
                {'label': 'Turbo', 'value': 'turbo', 'sub_label': '8 steps, fastest'},
                {'label': 'Balanced', 'value': 'balanced'}
            ]
        }

        options = self.select_field._parse_yaml_options(yaml_data)

        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['sub_label'], '8 steps, fastest')
        self.assertNotIn('sub_label', options[1])

    def test_parse_yaml_options_dict(self):
        """Test parsing YAML options from dict format"""
        yaml_data = {
            'options': [
                {'label': 'Dict Option 1', 'value': 'dict_opt1'},
                'dict_simple'
            ]
        }
        
        options = self.select_field._parse_yaml_options(yaml_data)
        
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], 'Dict Option 1')
        self.assertEqual(options[1]['value'], 'dict_simple')
    
    def test_file_options_no_preset(self):
        """Test file options when preset not found"""
        config = {'file': {'path': '/test/path'}}
        options = self.select_field._get_file_options(config, 'nonexistent_preset')
        self.assertEqual(len(options), 0)
    
    def test_file_options_no_path(self):
        """Test file options with missing path"""
        config = {'file': {}}
        options = self.select_field._get_file_options(config, 'test_preset_id')
        self.assertEqual(len(options), 0)
    
    @patch('pathlib.Path.exists')
    def test_file_options_file_not_exists(self, mock_exists):
        """Test file options when file doesn't exist"""
        mock_exists.return_value = False
        
        with patch('src.platform.templating.TemplateProcessor.process_template', return_value='/nonexistent/file.yml'):
            config = {'file': {'path': '/test/path'}}
            options = self.select_field._get_file_options(config, 'test_preset_id')
            self.assertEqual(len(options), 0)
    
    def test_combined_options(self):
        """Test combining static, file, and filesystem options"""
        field = {
            'type': 'select',
            'name': 'combined_select',
            'label': 'Combined Select',
            'configuration': {
                'options': [{'label': 'Static Option', 'value': 'static'}],
                'file': {'path': '/test/file.yml'},
                'files': {'in': '/test/directory'}
            }
        }
        
        # Mock the internal methods
        with patch.object(self.select_field, '_get_file_options', return_value=[{'label': 'File Option', 'value': 'file'}]):
            with patch.object(self.select_field, '_get_filesystem_options', return_value=[{'label': 'FS Option', 'value': 'fs'}]):
                schema = self.select_field.output(field, 'test_preset_id')
        
        options = schema['options']
        self.assertEqual(len(options), 3)
        
        # Check that all option types are present
        values = [opt['value'] for opt in options]
        self.assertIn('static', values)
        self.assertIn('file', values)
        self.assertIn('fs', values)


if __name__ == '__main__':
    unittest.main()