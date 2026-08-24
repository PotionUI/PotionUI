import unittest
import tempfile
import os
import yaml
from unittest.mock import Mock, patch
from pathlib import Path

from src.features.fields.carousel import CarouselField


class TestCarouselField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.carousel_field = CarouselField(self.preset_loader)

        # Mock preset template
        self.mock_preset = Mock()
        self.mock_preset.path = '/test/presets/test_author/test_model/v1/standard'
        self.mock_preset.id = 'test_preset_id'

        self.preset_loader.preset_files_path = '/test/presets'
        self.preset_loader.presets = [self.mock_preset]

    def test_can_handle(self):
        """Test that carousel field handles 'carousel' type"""
        self.assertTrue(self.carousel_field.can_handle('carousel'))
        self.assertFalse(self.carousel_field.can_handle('select'))
        self.assertFalse(self.carousel_field.can_handle('slider'))

    def test_configuration(self):
        """Test that configuration includes all expected parameters"""
        config_specs = CarouselField.configuration()

        # Check that we have the expected configuration parameters
        config_names = [spec.name for spec in config_specs]
        expected_params = ['items', 'file', 'images', 'multi_select', 'rows', 'columns', 'item_width', 'item_height', 'mode', 'show_labels']

        for param in expected_params:
            self.assertIn(param, config_names, f"Missing configuration parameter: {param}")

        # Check items parameter
        items_spec = next((spec for spec in config_specs if spec.name == 'items'), None)
        self.assertIsNotNone(items_spec)
        self.assertEqual(items_spec.param_type, list)

        # Check multi_select parameter
        multi_select_spec = next((spec for spec in config_specs if spec.name == 'multi_select'), None)
        self.assertIsNotNone(multi_select_spec)
        self.assertEqual(multi_select_spec.param_type, bool)
        self.assertEqual(multi_select_spec.default, False)

    def test_examples(self):
        """Test that examples are provided"""
        examples = CarouselField.examples()
        self.assertGreater(len(examples), 0)

        # Check first example has required fields
        first_example = examples[0]
        self.assertIsNotNone(first_example.title)
        self.assertIsNotNone(first_example.description)
        self.assertIsNotNone(first_example.yaml_config)


    def test_output_static_items(self):
        """Test output with static items"""
        field = {
            'type': 'carousel',
            'name': 'film_grain',
            'label': 'Film Grain Effect',
            'configuration': {
                'items': [
                    {
                        'label': 'Fine Grain',
                        'value': 'fine',
                        'image': 'files/carousel/grain_fine.png',
                        'description': 'Subtle film grain'
                    },
                    {
                        'label': 'Heavy Grain',
                        'value': 'heavy',
                        'image': 'files/carousel/grain_heavy.png',
                        'description': 'Strong film grain'
                    }
                ],
                'rows': 1,
                'columns': 2
            }
        }

        schema = self.carousel_field.output(field, 'test_preset_id')

        self.assertEqual(schema['type'], 'carousel')
        self.assertEqual(schema['name'], 'film_grain')
        self.assertEqual(schema['title'], 'Film Grain Effect')
        self.assertEqual(schema['preset_id'], 'test_preset_id')
        self.assertIn('options', schema)
        self.assertIn('configuration', schema)

        # Check items were correctly processed
        options = schema['options']
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], 'Fine Grain')
        self.assertEqual(options[0]['value'], 'fine')
        self.assertEqual(options[0]['image'], 'files/carousel/grain_fine.png')
        self.assertEqual(options[0]['description'], 'Subtle film grain')

        # Check configuration
        config = schema['configuration']
        self.assertEqual(config['rows'], 1)
        self.assertEqual(config['columns'], 2)
        self.assertEqual(config['multi_select'], False)
        self.assertEqual(config['mode'], 'grid')

    def test_output_with_preset_id(self):
        """Test that output includes preset_id"""
        field = {
            'type': 'carousel',
            'name': 'test_carousel',
            'label': 'Test Carousel',
            'configuration': {
                'items': []
            }
        }

        schema = self.carousel_field.output(field, 'test_preset_id')
        self.assertEqual(schema['preset_id'], 'test_preset_id')

    def test_output_configuration_defaults(self):
        """Test that configuration includes default values"""
        field = {
            'type': 'carousel',
            'name': 'test_carousel',
            'label': 'Test Carousel',
            'configuration': {
                'items': []
            }
        }

        schema = self.carousel_field.output(field)

        config = schema['configuration']
        self.assertEqual(config['multi_select'], False)
        self.assertEqual(config['rows'], 2)
        self.assertEqual(config['columns'], 3)
        self.assertEqual(config['item_width'], 150)
        self.assertEqual(config['item_height'], 150)
        self.assertEqual(config['mode'], 'grid')
        self.assertEqual(config['show_labels'], True)

    def test_output_file_loading(self):
        """Test output with file-based items loading"""
        # Create temporary YAML file with carousel items
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml_data = [
                {
                    'label': 'Style 1',
                    'value': 'style1',
                    'image': 'files/styles/style1.png',
                    'description': 'First style'
                },
                {
                    'label': 'Style 2',
                    'value': 'style2',
                    'image': 'files/styles/style2.png'
                }
            ]
            yaml.dump(yaml_data, f)
            temp_file = f.name

        try:
            field = {
                'type': 'carousel',
                'name': 'art_style',
                'label': 'Art Style',
                'configuration': {
                    'file': {
                        'path': temp_file
                    }
                }
            }

            # Override template processor to return the temp file path as-is
            self.carousel_field.template_processor = None

            schema = self.carousel_field.output(field, 'test_preset_id')

            options = schema['options']
            self.assertEqual(len(options), 2)
            self.assertEqual(options[0]['label'], 'Style 1')
            self.assertEqual(options[0]['value'], 'style1')
            self.assertEqual(options[0]['image'], 'files/styles/style1.png')
            self.assertEqual(options[1]['label'], 'Style 2')
            self.assertNotIn('description', options[1])  # No description for second item

        finally:
            os.unlink(temp_file)

    def test_output_directory_scanning(self):
        """Test output with directory scanning for images"""
        # Create temporary directory with image files
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create some test image files
            test_files = ['grain_fine.png', 'grain_medium.png', 'grain_heavy.png']
            for filename in test_files:
                Path(temp_dir, filename).touch()

            field = {
                'type': 'carousel',
                'name': 'grain_preset',
                'label': 'Grain Preset',
                'configuration': {
                    'images': {
                        'in': temp_dir,
                        'pattern': '*.png',
                        'recursive': False
                    }
                }
            }

            # Set preset path to temp dir so relative paths work
            self.preset_loader.preset_files_path = temp_dir
            self.mock_preset.path = temp_dir

            schema = self.carousel_field.output(field, 'test_preset_id')

            options = schema['options']
            self.assertEqual(len(options), 3)

            # Check that files were found and labeled correctly
            labels = [opt['label'] for opt in options]
            self.assertIn('Grain Fine', labels)
            self.assertIn('Grain Medium', labels)
            self.assertIn('Grain Heavy', labels)

    def test_get_static_items(self):
        """Test _get_static_items helper method"""
        configuration = {
            'items': [
                {'label': 'Item 1', 'value': 'item1', 'image': 'img1.png'},
                {'label': 'Item 2', 'value': 'item2', 'image': 'img2.png', 'description': 'Second item'}
            ]
        }

        items = self.carousel_field._get_static_items(configuration)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['label'], 'Item 1')
        self.assertEqual(items[0]['value'], 'item1')
        self.assertEqual(items[0]['image'], 'img1.png')
        self.assertNotIn('description', items[0])
        self.assertEqual(items[1]['description'], 'Second item')

    def test_parse_yaml_items_list_format(self):
        """Test _parse_yaml_items with list format"""
        yaml_data = [
            {'label': 'Item 1', 'value': 'item1', 'image': 'img1.png'},
            {'label': 'Item 2', 'value': 'item2', 'image': 'img2.png', 'description': 'Second item'}
        ]

        items = self.carousel_field._parse_yaml_items(yaml_data)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['label'], 'Item 1')
        self.assertEqual(items[1]['description'], 'Second item')

    def test_parse_yaml_items_dict_format(self):
        """Test _parse_yaml_items with dictionary format"""
        yaml_data = {
            'items': [
                {'label': 'Item 1', 'value': 'item1', 'image': 'img1.png'},
                {'label': 'Item 2', 'value': 'item2', 'image': 'img2.png'}
            ]
        }

        items = self.carousel_field._parse_yaml_items(yaml_data)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['label'], 'Item 1')
        self.assertEqual(items[1]['label'], 'Item 2')

    def test_multiple_sources(self):
        """Test that items from multiple sources are combined"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create YAML file
            yaml_file = Path(temp_dir, 'items.yml')
            with open(yaml_file, 'w') as f:
                yaml.dump([{'label': 'File Item', 'value': 'file_item', 'image': 'file.png'}], f)

            # Create image file
            Path(temp_dir, 'dir_item.png').touch()

            field = {
                'type': 'carousel',
                'name': 'test',
                'label': 'Test',
                'configuration': {
                    'items': [
                        {'label': 'Static Item', 'value': 'static_item', 'image': 'static.png'}
                    ],
                    'file': {
                        'path': str(yaml_file)
                    },
                    'images': {
                        'in': temp_dir,
                        'pattern': '*.png',
                        'recursive': False
                    }
                }
            }

            # Set preset path
            self.preset_loader.preset_files_path = temp_dir
            self.mock_preset.path = temp_dir
            self.carousel_field.template_processor = None

            schema = self.carousel_field.output(field, 'test_preset_id')

            # Should have items from all sources
            options = schema['options']
            self.assertGreaterEqual(len(options), 2)  # At least static + file


if __name__ == '__main__':
    unittest.main()
