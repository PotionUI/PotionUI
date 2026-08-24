import unittest
from unittest.mock import Mock

from src.features.fields.section import Section
from src.features.fields.field_factory import FieldFactory
from src.platform.plugins.field_types import FieldTypeRegistry


class TestSection(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.section_field = Section(self.preset_loader)

    def test_can_handle(self):
        """Test that section field handler correctly identifies section fields"""
        self.assertTrue(self.section_field.can_handle('section'))
        self.assertFalse(self.section_field.can_handle('header'))
        self.assertFalse(self.section_field.can_handle('text'))
        self.assertFalse(self.section_field.can_handle(''))
        self.assertFalse(self.section_field.can_handle(None))

    def test_output_basic_section(self):
        """Test output transformation for a basic section field"""
        field = {
            'type': 'section',
            'name': 'model_section',
            'label': 'Model',
            'description': 'Checkpoint and LoRA selection'
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)  # Sections don't have names
        self.assertEqual(result['title'], 'Model')
        self.assertEqual(result['description'], 'Checkpoint and LoRA selection')
        self.assertNotIn('badge', result)
        self.assertNotIn('tooltip', result)

    def test_output_minimal_section(self):
        """Test output transformation for a minimal section field"""
        field = {
            'type': 'section',
            'label': 'Sampling'
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)
        self.assertEqual(result['title'], 'Sampling')
        self.assertNotIn('default', result)

    def test_output_with_badge_configuration(self):
        """Test output transformation with a trailing badge/meta text"""
        field = {
            'type': 'section',
            'label': 'Image',
            'configuration': {
                'badge': '4 fields'
            }
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)
        self.assertEqual(result['title'], 'Image')
        self.assertEqual(result['badge'], '4 fields')

    def test_output_with_tooltip_configuration(self):
        """Test output transformation with a hint tooltip"""
        field = {
            'type': 'section',
            'label': 'Refiner',
            'configuration': {
                'tooltip': 'Only used when hires fix is enabled'
            }
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertEqual(result['tooltip'], 'Only used when hires fix is enabled')

    def test_output_omits_empty_badge_and_tooltip(self):
        """Empty configuration values should not leak empty-string keys into the schema"""
        field = {
            'type': 'section',
            'label': 'Model',
            'configuration': {
                'badge': '',
                'tooltip': ''
            }
        }

        result = self.section_field.output(field)

        self.assertNotIn('badge', result)
        self.assertNotIn('tooltip', result)

    def test_output_with_collapsed_configuration(self):
        """Test output transformation with collapsed: true"""
        field = {
            'type': 'section',
            'label': 'Advanced',
            'configuration': {
                'collapsed': True
            }
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertIs(result['collapsed'], True)

    def test_output_omits_collapsed_when_false(self):
        """collapsed: false (or absent) should not appear in the schema - the
        client defaults to expanded when the key is missing"""
        field = {
            'type': 'section',
            'label': 'Advanced',
            'configuration': {
                'collapsed': False
            }
        }

        result = self.section_field.output(field)

        self.assertNotIn('collapsed', result)

        field_without_config = {
            'type': 'section',
            'label': 'Advanced'
        }
        self.assertNotIn('collapsed', self.section_field.output(field_without_config))

    def test_output_with_experimental_configuration(self):
        """Test output transformation with experimental: true"""
        field = {
            'type': 'section',
            'label': 'Live Upscale',
            'configuration': {
                'experimental': True
            }
        }

        result = self.section_field.output(field)

        self.assertEqual(result['type'], 'section')
        self.assertIs(result['experimental'], True)

    def test_output_omits_experimental_when_false(self):
        """experimental: false (or absent) should not appear in the schema"""
        field = {
            'type': 'section',
            'label': 'Live Upscale',
            'configuration': {
                'experimental': False
            }
        }

        result = self.section_field.output(field)

        self.assertNotIn('experimental', result)

        field_without_config = {
            'type': 'section',
            'label': 'Live Upscale'
        }
        self.assertNotIn('experimental', self.section_field.output(field_without_config))

    def test_output_with_object_field(self):
        """Test output transformation with object-style field"""
        mock_field = Mock()
        mock_field.type = 'section'
        mock_field.name = 'object_section'
        mock_field.label = 'Object Section'
        mock_field.description = 'A section from an object-style field'
        mock_field.default = None
        mock_field.required = False
        mock_field.configuration = {'badge': '2 fields'}

        result = self.section_field.output(mock_field)

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)
        self.assertEqual(result['title'], 'Object Section')
        self.assertEqual(result['badge'], '2 fields')


    def test_map_field(self):
        """Test that map_field delegates to output"""
        field = {
            'type': 'section',
            'label': 'Test Section',
            'configuration': {'badge': '1 changed'}
        }

        output_result = self.section_field.output(field, 'preset_id')
        map_result = self.section_field.map_field(field, 'preset_id')

        self.assertEqual(output_result, map_result)

    def test_configuration_specs(self):
        """Test configuration specifications"""
        config_specs = Section.configuration()

        self.assertGreater(len(config_specs), 0)

        badge_spec = next((spec for spec in config_specs if spec.name == 'badge'), None)
        self.assertIsNotNone(badge_spec)
        self.assertEqual(badge_spec.param_type, str)

        tooltip_spec = next((spec for spec in config_specs if spec.name == 'tooltip'), None)
        self.assertIsNotNone(tooltip_spec)
        self.assertEqual(tooltip_spec.param_type, str)

        collapsed_spec = next((spec for spec in config_specs if spec.name == 'collapsed'), None)
        self.assertIsNotNone(collapsed_spec)
        self.assertEqual(collapsed_spec.param_type, bool)
        self.assertEqual(collapsed_spec.default, False)

        experimental_spec = next((spec for spec in config_specs if spec.name == 'experimental'), None)
        self.assertIsNotNone(experimental_spec)
        self.assertEqual(experimental_spec.param_type, bool)
        self.assertEqual(experimental_spec.default, False)

    def test_validation_rules_empty(self):
        """Section is display-only - it has no validation rules"""
        self.assertEqual(Section.validation_rules(), [])

    def test_examples(self):
        """Test example configurations"""
        examples = Section.examples()

        self.assertGreater(len(examples), 0)

        for example in examples:
            self.assertIsNotNone(example.title)
            self.assertIsNotNone(example.description)
            self.assertIsNotNone(example.yaml_config)
            self.assertIsNotNone(example.rendered_output)
            self.assertIn('type', example.rendered_output)
            self.assertEqual(example.rendered_output['type'], 'section')

    def test_description(self):
        """Test field description"""
        description = Section.description()
        self.assertIsNotNone(description)
        self.assertIsInstance(description, str)
        self.assertGreater(len(description), 0)

    def test_name_removal_from_output(self):
        """Test that name is always removed from output schema"""
        field_with_name = {
            'type': 'section',
            'name': 'should_be_removed',
            'label': 'Section With Name'
        }

        result = self.section_field.output(field_with_name)
        self.assertNotIn('name', result)

    def test_output_childless_section_emits_no_children_key(self):
        """A section with no `children:` must not gain a `children` key at all -
        the client treats `children` presence as the container signal"""
        field_factory = Mock()
        section_field = Section(self.preset_loader, field_factory)

        field = {
            'type': 'section',
            'label': 'Sampling'
        }

        result = section_field.output(field)

        self.assertNotIn('children', result)
        field_factory.map_field.assert_not_called()

        field_with_empty_children = {
            'type': 'section',
            'label': 'Sampling',
            'children': []
        }
        result = section_field.output(field_with_empty_children)
        self.assertNotIn('children', result)

    def test_output_section_with_children_maps_through_factory(self):
        """A section declaring `children:` maps each one through the field
        factory, mirroring Container"""
        field_factory = Mock()
        child1 = {'type': 'slider', 'name': 'strength'}
        child2 = {'type': 'select', 'name': 'style'}
        field_factory.map_field.side_effect = [
            {'type': 'slider', 'name': 'strength', 'mapped': True},
            {'type': 'select', 'name': 'style', 'mapped': True},
        ]

        section_field = Section(self.preset_loader, field_factory)
        field = {
            'type': 'section',
            'label': 'Advanced',
            'children': [child1, child2]
        }

        result = section_field.output(field, 'test_preset')

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)
        self.assertEqual(len(result['children']), 2)
        self.assertEqual(field_factory.map_field.call_count, 2)
        field_factory.map_field.assert_any_call(child1, 'test_preset')
        field_factory.map_field.assert_any_call(child2, 'test_preset')

    def test_output_section_with_children_no_factory(self):
        """Without a field factory, a section that declares children still
        emits an (empty) `children` key rather than raising"""
        section_field = Section(self.preset_loader, None)
        field = {
            'type': 'section',
            'label': 'Advanced',
            'children': [{'type': 'slider', 'name': 'strength'}]
        }

        result = section_field.output(field)

        self.assertEqual(result['children'], [])

    def test_output_section_with_children_and_other_configuration(self):
        """badge/tooltip/collapsed/experimental all still ride through on a
        section that also declares children"""
        field_factory = Mock()
        field_factory.map_field.return_value = {'type': 'slider', 'name': 'strength'}
        section_field = Section(self.preset_loader, field_factory)

        field = {
            'type': 'section',
            'label': 'Advanced',
            'configuration': {
                'badge': '3 fields',
                'tooltip': 'Only used in advanced mode',
                'collapsed': True,
                'experimental': True,
            },
            'children': [{'type': 'slider', 'name': 'strength'}]
        }

        result = section_field.output(field)

        self.assertEqual(result['badge'], '3 fields')
        self.assertEqual(result['tooltip'], 'Only used in advanced mode')
        self.assertIs(result['collapsed'], True)
        self.assertIs(result['experimental'], True)
        self.assertEqual(len(result['children']), 1)

    def test_output_with_object_field_and_children(self):
        """Object-style (FieldTemplate-like) fields expose children as a
        plain attribute, mirroring Container's `_get_children`"""
        field_factory = Mock()
        field_factory.map_field.return_value = {'type': 'slider', 'name': 'strength'}
        section_field = Section(self.preset_loader, field_factory)

        mock_field = Mock()
        mock_field.type = 'section'
        mock_field.name = None
        mock_field.label = 'Advanced'
        mock_field.description = ''
        mock_field.default = None
        mock_field.required = False
        mock_field.configuration = {}
        mock_field.children = [{'type': 'slider', 'name': 'strength'}]

        result = section_field.output(mock_field)

        self.assertEqual(len(result['children']), 1)
        field_factory.map_field.assert_called_once()

    def test_nested_children_render_through_real_container_and_factory(self):
        """Integration: a section's children are mapped through the real
        FieldFactory (not a mocked map_field), including a nested container
        child whose own children go through Container's real recursion"""
        registry = FieldTypeRegistry()
        factory = FieldFactory(self.preset_loader, field_registry=registry)

        field = {
            'type': 'section',
            'label': 'Advanced',
            'children': [
                {
                    'type': 'row',
                    'name': 'advanced_row',
                    'children': [
                        {'type': 'slider', 'name': 'strength', 'label': 'Strength'},
                        {'type': 'select', 'name': 'style', 'label': 'Style'},
                    ]
                }
            ]
        }

        result = factory.map_field(field)

        self.assertEqual(result['type'], 'section')
        self.assertNotIn('name', result)
        self.assertEqual(len(result['children']), 1)

        row_schema = result['children'][0]
        self.assertEqual(row_schema['type'], 'row')
        self.assertEqual(len(row_schema['children']), 2)
        self.assertEqual(row_schema['children'][0]['name'], 'strength')
        self.assertEqual(row_schema['children'][1]['name'], 'style')

    def test_foldable_config_key_removed(self):
        """The removed `foldable` config no longer exists as a declared
        configuration spec, and a stray `foldable: true` in preset YAML is
        silently ignored rather than surfaced in the schema"""
        config_names = [spec.name for spec in Section.configuration()]
        self.assertNotIn('foldable', config_names)

        field = {
            'type': 'section',
            'label': 'Advanced',
            'configuration': {'foldable': True}
        }
        result = self.section_field.output(field)
        self.assertNotIn('foldable', result)


if __name__ == '__main__':
    unittest.main()
