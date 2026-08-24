import os
import tempfile
import unittest
from unittest.mock import Mock, MagicMock

import yaml

from src.features.fields.resolution import Resolution


class TestResolutionField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.resolution_field = Resolution(self.preset_loader)

    def test_can_handle(self):
        """Test that resolution field handles 'resolution' type"""
        self.assertTrue(self.resolution_field.can_handle('resolution'))
        self.assertFalse(self.resolution_field.can_handle('select'))
        self.assertFalse(self.resolution_field.can_handle('slider'))

    def test_configuration(self):
        """Test that configuration includes options, file, and files parameters"""
        config_specs = Resolution.configuration()

        config_names = [spec.name for spec in config_specs]
        self.assertIn('options', config_names)
        self.assertIn('file', config_names)
        self.assertIn('files', config_names)

        # Check options parameter
        options_spec = next((spec for spec in config_specs if spec.name == 'options'), None)
        self.assertIsNotNone(options_spec)
        self.assertEqual(options_spec.param_type, list)
        self.assertTrue(isinstance(options_spec.example, list))

        # Check file parameter
        file_spec = next((spec for spec in config_specs if spec.name == 'file'), None)
        self.assertIsNotNone(file_spec)
        self.assertEqual(file_spec.param_type, dict)

        # Check files parameter
        files_spec = next((spec for spec in config_specs if spec.name == 'files'), None)
        self.assertIsNotNone(files_spec)
        self.assertEqual(files_spec.param_type, list)

    def test_input_processing_string_format(self):
        """Test input processing with WIDTHxHEIGHT string format"""
        result = self.resolution_field.input('resolution', '1920x1080')

        self.assertIsInstance(result, str)
        self.assertEqual(result, '1920x1080')

    def test_input_processing_with_spaces(self):
        """Test input processing with spaces in string"""
        result = self.resolution_field.input('resolution', '1280 x 720')

        self.assertIsInstance(result, str)
        self.assertEqual(result, '1280 x 720')

    def test_input_validation_non_string(self):
        """Test validation fails for non-string input"""
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', {'width': 1024, 'height': 1024})

        self.assertIn('Invalid resolution value type', str(context.exception))

    def test_input_processing_none_value(self):
        """Test input processing with None value"""
        result = self.resolution_field.input('resolution', None)
        self.assertIsNone(result)

    def test_input_processing_empty_string(self):
        """Test input processing with empty string"""
        result = self.resolution_field.input('resolution', '')
        self.assertIsNone(result)

    def test_input_validation_invalid_format(self):
        """Test validation fails for invalid format"""
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', 'invalid_format')

        self.assertIn('Invalid resolution format', str(context.exception))

    def test_input_validation_missing_height(self):
        """Test validation fails when height is missing"""
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', '1920x')

        self.assertIn('Invalid resolution format', str(context.exception))

    def test_input_validation_non_numeric(self):
        """Test validation fails for non-numeric values"""
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', 'widthxheight')

        self.assertIn('Invalid resolution format', str(context.exception))

    def test_input_validation_invalid_type(self):
        """Test validation fails for invalid value type"""
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', 12345)

        self.assertIn('Invalid resolution value type', str(context.exception))

    def test_output_with_options(self):
        """Test output schema with resolution options - now returns normalized dicts"""
        field = {
            'type': 'resolution',
            'name': 'resolution',
            'label': 'Resolution',
            'configuration': {
                'options': ['896x1152', '1152x896', '1024x1024']
            }
        }

        schema = self.resolution_field.output(field)

        self.assertEqual(schema['type'], 'resolution')
        self.assertEqual(schema['name'], 'resolution')
        self.assertEqual(schema['title'], 'Resolution')
        self.assertIn('options', schema)

        options = schema['options']
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['value'], '896x1152')
        self.assertEqual(options[0]['ratio'], [7, 9])
        self.assertEqual(options[1]['value'], '1152x896')
        self.assertEqual(options[1]['ratio'], [9, 7])
        self.assertEqual(options[2]['value'], '1024x1024')
        self.assertEqual(options[2]['ratio'], [1, 1])

    def test_output_with_hd_resolutions(self):
        """Test output schema with HD resolutions - normalized format"""
        field = {
            'type': 'resolution',
            'name': 'resolution',
            'label': 'Resolution',
            'configuration': {
                'options': ['1280x720', '1920x1080', '3840x2160']
            }
        }

        schema = self.resolution_field.output(field)

        options = schema['options']
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['value'], '1280x720')
        self.assertEqual(options[0]['ratio'], [16, 9])
        self.assertEqual(options[1]['value'], '1920x1080')
        self.assertEqual(options[1]['ratio'], [16, 9])
        self.assertEqual(options[2]['value'], '3840x2160')
        self.assertEqual(options[2]['ratio'], [16, 9])

    def test_output_with_simple_string_options(self):
        """Test output schema with simple string options - normalized format"""
        field = {
            'type': 'resolution',
            'name': 'resolution',
            'label': 'Resolution',
            'configuration': {
                'options': ['1920x1080', '1280x720', '3840x2160']
            }
        }

        schema = self.resolution_field.output(field)

        options = schema['options']
        self.assertEqual(len(options), 3)
        self.assertEqual(options[0]['value'], '1920x1080')
        self.assertEqual(options[1]['value'], '1280x720')
        self.assertEqual(options[2]['value'], '3840x2160')

    def test_output_empty_options(self):
        """Test output schema with no options"""
        field = {
            'type': 'resolution',
            'name': 'resolution',
            'label': 'Resolution',
            'configuration': {}
        }

        schema = self.resolution_field.output(field)

        self.assertIn('options', schema)
        self.assertEqual(len(schema['options']), 0)

    def test_examples(self):
        """Test example configurations"""
        examples = Resolution.examples()

        self.assertEqual(len(examples), 4)

        # Check first example
        first_example = examples[0]
        self.assertIsNotNone(first_example.title)
        self.assertIsNotNone(first_example.description)
        self.assertIsNotNone(first_example.yaml_config)
        self.assertIsNotNone(first_example.rendered_output)

        # Verify rendered output structure
        output = first_example.rendered_output
        self.assertEqual(output['type'], 'resolution')
        self.assertIn('options', output)
        self.assertGreater(len(output['options']), 0)

        # Check file-based example
        file_example = examples[2]
        self.assertEqual(file_example.title, "File-based Resolutions")
        self.assertIn('file', file_example.yaml_config)

        # Check grouped example
        grouped_example = examples[3]
        self.assertEqual(grouped_example.title, "Grouped Resolution Collections")
        self.assertIn('files', grouped_example.yaml_config)

    def test_common_resolutions(self):
        """Test parsing common resolution formats"""
        test_cases = [
            '896x1152',
            '1152x896',
            '1024x1024',
            '1920x1080',
            '3840x2160',
            '1280x720',
        ]

        for resolution_str in test_cases:
            with self.subTest(resolution=resolution_str):
                result = self.resolution_field.input('resolution', resolution_str)
                self.assertEqual(result, resolution_str)


class TestComputeRatio(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.resolution_field = Resolution(self.preset_loader)

    def test_square_ratio(self):
        self.assertEqual(self.resolution_field._compute_ratio('1024x1024'), [1, 1])

    def test_16_9_ratio(self):
        self.assertEqual(self.resolution_field._compute_ratio('1920x1080'), [16, 9])

    def test_9_7_ratio(self):
        self.assertEqual(self.resolution_field._compute_ratio('1152x896'), [9, 7])

    def test_invalid_format_no_x(self):
        self.assertIsNone(self.resolution_field._compute_ratio('1920-1080'))

    def test_invalid_format_non_numeric(self):
        self.assertIsNone(self.resolution_field._compute_ratio('widthxheight'))

    def test_with_spaces(self):
        self.assertEqual(self.resolution_field._compute_ratio(' 1920 x 1080 '), [16, 9])

    def test_multiple_x(self):
        self.assertIsNone(self.resolution_field._compute_ratio('1920x1080x720'))


class TestNormalizeOption(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.resolution_field = Resolution(self.preset_loader)

    def test_string_option(self):
        result = self.resolution_field._normalize_option('1024x1024')
        self.assertEqual(result['value'], '1024x1024')
        self.assertEqual(result['ratio'], [1, 1])
        self.assertNotIn('group', result)

    def test_string_option_with_group(self):
        result = self.resolution_field._normalize_option('1024x1024', group='SDXL')
        self.assertEqual(result['value'], '1024x1024')
        self.assertEqual(result['ratio'], [1, 1])
        self.assertEqual(result['group'], 'SDXL')

    def test_dict_option_with_description(self):
        opt = {"value": "1024x1024", "description": "Square"}
        result = self.resolution_field._normalize_option(opt)
        self.assertEqual(result['value'], '1024x1024')
        self.assertEqual(result['description'], 'Square')
        self.assertEqual(result['ratio'], [1, 1])

    def test_dict_option_with_explicit_ratio(self):
        opt = {"value": "1024x1024", "ratio": [4, 4]}
        result = self.resolution_field._normalize_option(opt)
        self.assertEqual(result['ratio'], [4, 4])

    def test_dict_option_auto_computes_ratio(self):
        opt = {"value": "1920x1080"}
        result = self.resolution_field._normalize_option(opt)
        self.assertEqual(result['ratio'], [16, 9])

    def test_dict_option_with_group(self):
        opt = {"value": "1920x1080"}
        result = self.resolution_field._normalize_option(opt, group='Social Media')
        self.assertEqual(result['group'], 'Social Media')

    def test_non_string_non_dict_option(self):
        result = self.resolution_field._normalize_option(12345)
        self.assertEqual(result['value'], '12345')
        self.assertIsNone(result['ratio'])

    def test_dict_option_with_own_tier(self):
        opt = {"value": "768x768", "description": "Square (Compact)", "tier": "Compact"}
        result = self.resolution_field._normalize_option(opt)
        self.assertEqual(result['tier'], 'Compact')

    def test_dict_option_tier_falls_back_to_group(self):
        opt = {"value": "2048x2048"}
        result = self.resolution_field._normalize_option(opt, group='2K')
        self.assertEqual(result['tier'], '2K')

    def test_dict_option_own_tier_wins_over_group(self):
        opt = {"value": "768x768", "tier": "Compact"}
        result = self.resolution_field._normalize_option(opt, group='Flux')
        self.assertEqual(result['tier'], 'Compact')

    def test_option_tier_falls_back_to_standard(self):
        result = self.resolution_field._normalize_option('1920x1080')
        self.assertEqual(result['tier'], 'Standard')

    def test_string_option_tier_falls_back_to_group(self):
        result = self.resolution_field._normalize_option('1920x1080', group='Social Media')
        self.assertEqual(result['tier'], 'Social Media')


class TestResolutionInputBounds(unittest.TestCase):
    """Bounds enforced on every WIDTHxHEIGHT submission, including custom
    entries the picker's configured `options` never listed."""

    def setUp(self):
        self.preset_loader = Mock()
        self.resolution_field = Resolution(self.preset_loader)

    def test_accepts_a_well_formed_custom_value(self):
        result = self.resolution_field.input('resolution', '1536x1536')
        self.assertEqual(result, '1536x1536')

    def test_rejects_a_width_below_the_minimum(self):
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', '32x512')
        self.assertIn('must be between', str(context.exception))

    def test_rejects_a_height_above_the_maximum(self):
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', '1024x20000')
        self.assertIn('must be between', str(context.exception))

    def test_rejects_a_zero_dimension(self):
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', '0x1024')
        self.assertIn('must be between', str(context.exception))

    def test_rejects_a_negative_dimension(self):
        with self.assertRaises(ValueError) as context:
            self.resolution_field.input('resolution', '-1024x1024')
        self.assertIn('must be between', str(context.exception))

    # flux.yml's own shipped "Portrait" Full-tier option, 1140x1472, isn't a
    # multiple of 8 - the validator must accept shipped values exactly like
    # this rather than imposing a snap step no configured option actually
    # honors.
    def test_accepts_a_shipped_value_not_snapped_to_any_step(self):
        result = self.resolution_field.input('resolution', '1140x1472')
        self.assertEqual(result, '1140x1472')


class TestResolveTemplatePath(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.preset_loader.preset_files_path = '/presets'
        self.template_processor = Mock()
        self.resolution_field = Resolution(self.preset_loader, self.template_processor)

    def test_resolve_with_template_processor(self):
        mock_preset = Mock()
        mock_preset.id = 'test-preset'
        mock_preset.path = '/presets/test'
        self.preset_loader.presets = [mock_preset]
        self.template_processor.process_template.return_value = '/content/presets/_shared/resolutions/sdxl.yml'

        result = self.resolution_field._resolve_template_path(
            '{{paths._shared}}/resolutions/sdxl.yml', 'test-preset'
        )

        self.assertEqual(result, '/content/presets/_shared/resolutions/sdxl.yml')
        self.template_processor.process_template.assert_called_once()

    def test_resolve_without_template_processor(self):
        field = Resolution(self.preset_loader, template_processor=None)
        mock_preset = Mock()
        mock_preset.id = 'test-preset'
        mock_preset.path = '/presets/test'
        self.preset_loader.presets = [mock_preset]

        result = field._resolve_template_path('/some/path.yml', 'test-preset')
        self.assertEqual(result, '/some/path.yml')

    def test_resolve_preset_not_found(self):
        self.preset_loader.presets = []

        result = self.resolution_field._resolve_template_path('/some/path.yml', 'missing-preset')
        self.assertIsNone(result)


class TestLoadResolutionFile(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.resolution_field = Resolution(self.preset_loader)

    def test_load_list_format(self):
        data = [
            {"value": "1024x1024", "description": "Square"},
            "1920x1080"
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            result = self.resolution_field._load_resolution_file(f.name)
        os.unlink(f.name)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['value'], '1024x1024')
        self.assertEqual(result[1], '1920x1080')

    def test_load_dict_with_options_key(self):
        data = {
            'options': [
                {"value": "1024x1024", "description": "Square"},
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            result = self.resolution_field._load_resolution_file(f.name)
        os.unlink(f.name)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['value'], '1024x1024')

    def test_load_nonexistent_file(self):
        result = self.resolution_field._load_resolution_file('/nonexistent/file.yml')
        self.assertEqual(result, [])

    def test_load_empty_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write('')
            f.flush()
            result = self.resolution_field._load_resolution_file(f.name)
        os.unlink(f.name)
        self.assertEqual(result, [])


class TestOutputWithFileLoading(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.preset_loader.preset_files_path = '/presets'
        self.template_processor = Mock()
        self.resolution_field = Resolution(self.preset_loader, self.template_processor)

        self.mock_preset = Mock()
        self.mock_preset.id = 'test-preset'
        self.mock_preset.path = '/presets/test'
        self.preset_loader.presets = [self.mock_preset]

    def test_output_with_single_file(self):
        data = [
            {"value": "1024x1024", "description": "Square"},
            "1920x1080"
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            self.template_processor.process_template.return_value = f.name

            field = {
                'type': 'resolution',
                'name': 'resolution',
                'label': 'Resolution',
                'configuration': {
                    'file': {'path': '{{paths._shared}}/resolutions/sdxl.yml'}
                }
            }

            schema = self.resolution_field.output(field, preset_id='test-preset')
        os.unlink(f.name)

        options = schema['options']
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['value'], '1024x1024')
        self.assertEqual(options[0]['description'], 'Square')
        self.assertEqual(options[0]['ratio'], [1, 1])
        self.assertEqual(options[1]['value'], '1920x1080')
        self.assertEqual(options[1]['ratio'], [16, 9])

    def test_output_with_files_and_groups(self):
        data1 = [{"value": "1024x1024", "description": "Square"}]
        data2 = [{"value": "1920x1080", "description": "Full HD"}]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f1, \
             tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f2:
            yaml.dump(data1, f1)
            yaml.dump(data2, f2)
            f1.flush()
            f2.flush()

            self.template_processor.process_template.side_effect = [f1.name, f2.name]

            field = {
                'type': 'resolution',
                'name': 'resolution',
                'label': 'Resolution',
                'configuration': {
                    'files': [
                        {'path': '{{paths._shared}}/resolutions/sdxl.yml', 'group': 'SDXL'},
                        {'path': '{{paths._shared}}/resolutions/hd.yml', 'group': 'HD'},
                    ]
                }
            }

            schema = self.resolution_field.output(field, preset_id='test-preset')
        os.unlink(f1.name)
        os.unlink(f2.name)

        options = schema['options']
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['value'], '1024x1024')
        self.assertEqual(options[0]['group'], 'SDXL')
        self.assertEqual(options[1]['value'], '1920x1080')
        self.assertEqual(options[1]['group'], 'HD')

    def test_output_combines_inline_and_file_options(self):
        file_data = [{"value": "1920x1080", "description": "Full HD"}]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(file_data, f)
            f.flush()
            self.template_processor.process_template.return_value = f.name

            field = {
                'type': 'resolution',
                'name': 'resolution',
                'label': 'Resolution',
                'configuration': {
                    'options': ['1024x1024'],
                    'file': {'path': '{{paths._shared}}/resolutions/hd.yml'}
                }
            }

            schema = self.resolution_field.output(field, preset_id='test-preset')
        os.unlink(f.name)

        options = schema['options']
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['value'], '1024x1024')
        self.assertEqual(options[1]['value'], '1920x1080')
        self.assertEqual(options[1]['description'], 'Full HD')

    def test_output_file_without_preset_id_skips_file(self):
        """File loading should be skipped when no preset_id is provided"""
        field = {
            'type': 'resolution',
            'name': 'resolution',
            'label': 'Resolution',
            'configuration': {
                'options': ['1024x1024'],
                'file': {'path': '{{paths._shared}}/resolutions/sdxl.yml'}
            }
        }

        schema = self.resolution_field.output(field)

        options = schema['options']
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['value'], '1024x1024')

    def test_output_files_without_group(self):
        """Files entries without a group should not add group key"""
        file_data = [{"value": "1024x1024"}]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(file_data, f)
            f.flush()
            self.template_processor.process_template.return_value = f.name

            field = {
                'type': 'resolution',
                'name': 'resolution',
                'label': 'Resolution',
                'configuration': {
                    'files': [
                        {'path': '{{paths._shared}}/resolutions/sdxl.yml'}
                    ]
                }
            }

            schema = self.resolution_field.output(field, preset_id='test-preset')
        os.unlink(f.name)

        options = schema['options']
        self.assertEqual(len(options), 1)
        self.assertNotIn('group', options[0])


if __name__ == '__main__':
    unittest.main()
