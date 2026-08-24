import unittest
from unittest.mock import Mock

from src.features.fields.gate import Gate
from src.features.fields.field_factory import FieldFactory
from src.platform.plugins.field_types import FieldTypeRegistry


class TestGate(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.gate_field = Gate(self.preset_loader)

    def test_can_handle(self):
        """Test that gate field handler correctly identifies gate fields"""
        self.assertTrue(self.gate_field.can_handle('gate'))
        self.assertFalse(self.gate_field.can_handle('section'))
        self.assertFalse(self.gate_field.can_handle(''))
        self.assertFalse(self.gate_field.can_handle(None))

    def test_output_keeps_name(self):
        """Unlike section, a gate carries a real boolean value and must keep
        its `name` in the emitted schema"""
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'default': False
        }

        result = self.gate_field.output(field)

        self.assertEqual(result['type'], 'gate')
        self.assertEqual(result['name'], 'enhance')
        self.assertEqual(result['title'], 'Enhance')
        self.assertIs(result['default'], False)

    def test_output_default_true(self):
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'default': True
        }

        result = self.gate_field.output(field)

        self.assertIs(result['default'], True)

    def test_output_omits_summary_and_experimental_when_absent(self):
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance'
        }

        result = self.gate_field.output(field)

        self.assertNotIn('summary', result)
        self.assertNotIn('experimental', result)

    def test_output_with_summary_configuration(self):
        """`summary` is a static string, emitted verbatim - no interpolation"""
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'configuration': {
                'summary': 'Second pass at 2048x2048 - Balanced (2 steps)'
            }
        }

        result = self.gate_field.output(field)

        self.assertEqual(result['summary'], 'Second pass at 2048x2048 - Balanced (2 steps)')

    def test_output_omits_empty_summary(self):
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'configuration': {'summary': ''}
        }

        result = self.gate_field.output(field)

        self.assertNotIn('summary', result)

    def test_output_with_experimental_configuration(self):
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'configuration': {'experimental': True}
        }

        result = self.gate_field.output(field)

        self.assertIs(result['experimental'], True)

    def test_output_omits_experimental_when_false(self):
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'configuration': {'experimental': False}
        }

        result = self.gate_field.output(field)

        self.assertNotIn('experimental', result)

    def test_output_childless_gate_emits_no_children_key(self):
        """A gate with no `children:` degrades to a self-contained toggle -
        no `children` key at all"""
        field_factory = Mock()
        gate_field = Gate(self.preset_loader, field_factory)

        field = {
            'type': 'gate',
            'name': 'verbose_logging',
            'label': 'Verbose Logging'
        }

        result = gate_field.output(field)

        self.assertNotIn('children', result)
        field_factory.map_field.assert_not_called()

    def test_output_gate_with_children_maps_through_factory(self):
        """A gate's children are mapped through the real field factory,
        mirroring section/container"""
        field_factory = Mock()
        child1 = {'type': 'resolution', 'name': 'enhance_resolution'}
        child2 = {'type': 'select', 'name': 'enhance_detail'}
        field_factory.map_field.side_effect = [
            {'type': 'resolution', 'name': 'enhance_resolution', 'mapped': True},
            {'type': 'select', 'name': 'enhance_detail', 'mapped': True},
        ]

        gate_field = Gate(self.preset_loader, field_factory)
        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'children': [child1, child2]
        }

        result = gate_field.output(field, 'test_preset')

        self.assertEqual(result['type'], 'gate')
        self.assertEqual(result['name'], 'enhance')
        self.assertEqual(len(result['children']), 2)
        self.assertEqual(field_factory.map_field.call_count, 2)
        field_factory.map_field.assert_any_call(child1, 'test_preset')
        field_factory.map_field.assert_any_call(child2, 'test_preset')

    def test_nested_children_render_through_real_factory(self):
        """Integration: a gate's children map through the real FieldFactory,
        including a nested container child"""
        registry = FieldTypeRegistry()
        factory = FieldFactory(self.preset_loader, field_registry=registry)

        field = {
            'type': 'gate',
            'name': 'enhance',
            'label': 'Enhance',
            'default': False,
            'children': [
                {
                    'type': 'row',
                    'name': 'enhance_row',
                    'children': [
                        {'type': 'resolution', 'name': 'enhance_resolution', 'label': 'Resolution'},
                        {'type': 'select', 'name': 'enhance_detail', 'label': 'Detail'},
                    ]
                }
            ]
        }

        result = factory.map_field(field)

        self.assertEqual(result['type'], 'gate')
        self.assertEqual(result['name'], 'enhance')
        self.assertEqual(len(result['children']), 1)

        row_schema = result['children'][0]
        self.assertEqual(row_schema['type'], 'row')
        self.assertEqual(len(row_schema['children']), 2)
        self.assertEqual(row_schema['children'][0]['name'], 'enhance_resolution')
        self.assertEqual(row_schema['children'][1]['name'], 'enhance_detail')


    def test_configuration_specs(self):
        config_specs = Gate.configuration()

        summary_spec = next((spec for spec in config_specs if spec.name == 'summary'), None)
        self.assertIsNotNone(summary_spec)
        self.assertEqual(summary_spec.param_type, str)

        experimental_spec = next((spec for spec in config_specs if spec.name == 'experimental'), None)
        self.assertIsNotNone(experimental_spec)
        self.assertEqual(experimental_spec.param_type, bool)
        self.assertEqual(experimental_spec.default, False)

    def test_validation_rules_empty(self):
        self.assertEqual(Gate.validation_rules(), [])

    def test_examples(self):
        examples = Gate.examples()

        self.assertGreater(len(examples), 0)
        for example in examples:
            self.assertIsNotNone(example.title)
            self.assertIsNotNone(example.description)
            self.assertIsNotNone(example.yaml_config)
            self.assertIn('type', example.rendered_output)
            self.assertEqual(example.rendered_output['type'], 'gate')

    def test_description(self):
        description = Gate.description()
        self.assertIsNotNone(description)
        self.assertIsInstance(description, str)
        self.assertGreater(len(description), 0)


class TestGateRegistration(unittest.TestCase):
    """The gate type must be discoverable through the same registry path as
    every other builtin field type."""

    def test_registered_in_builtin_registry(self):
        from src.features.fields.builtin import register_builtin_fields

        registry = FieldTypeRegistry()
        register_builtin_fields(registry)

        definition = registry.get('gate')
        self.assertIsNotNone(definition)
        self.assertIs(definition.schema_cls, Gate)
        self.assertEqual(definition.frontend_component, 'core:GateField')


if __name__ == '__main__':
    unittest.main()
