import unittest
from unittest.mock import Mock

from src.features.fields.lora_picker import LoraPicker
from src.features.fields.specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class TestLoraPickerField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.field = LoraPicker(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.field.can_handle('lora_picker'))
        self.assertFalse(self.field.can_handle('model'))
        self.assertFalse(self.field.can_handle('select'))

    # --- output() ---

    def test_output_default_configuration(self):
        field = {
            'type': 'lora_picker',
            'name': 'loras',
            'label': 'LoRAs',
            'configuration': {}
        }

        schema = self.field.output(field)

        self.assertEqual(schema['type'], 'lora_picker')
        self.assertEqual(schema['name'], 'loras')
        config = schema['configuration']
        self.assertEqual(config['model_type'], 'lora')
        self.assertEqual(config['placeholder'], 'Select a LoRA...')
        self.assertEqual(config['strength_min'], -2.0)
        self.assertEqual(config['strength_max'], 2.0)
        self.assertEqual(config['strength_step'], 0.1)
        self.assertEqual(config['strength_default'], 1.0)
        self.assertEqual(config['max_items'], 6)
        self.assertTrue(config['allow_info_modal'])
        self.assertTrue(config['show_triggers'])

    def test_output_author_overrides(self):
        field = {
            'type': 'lora_picker',
            'name': 'loras',
            'label': 'LoRAs',
            'configuration': {
                'placeholder': 'Add a LoRA...',
                'strength_min': -1.0,
                'strength_max': 1.0,
                'strength_step': 0.05,
                'strength_default': 0.8,
                'max_items': 3,
                'allow_info_modal': False,
                'show_triggers': False,
            }
        }

        schema = self.field.output(field)
        config = schema['configuration']
        self.assertEqual(config['placeholder'], 'Add a LoRA...')
        self.assertEqual(config['strength_min'], -1.0)
        self.assertEqual(config['strength_max'], 1.0)
        self.assertEqual(config['strength_step'], 0.05)
        self.assertEqual(config['strength_default'], 0.8)
        self.assertEqual(config['max_items'], 3)
        self.assertFalse(config['allow_info_modal'])
        self.assertFalse(config['show_triggers'])

    def test_output_max_items_none_allowed(self):
        field = {
            'type': 'lora_picker',
            'name': 'loras',
            'configuration': {'max_items': None}
        }

        schema = self.field.output(field)
        self.assertIsNone(schema['configuration']['max_items'])

    def test_output_resolves_set_filter_tags_in_reactions(self):
        field = {
            'type': 'lora_picker',
            'name': 'loras',
            'configuration': {},
            'reactions': [
                {
                    'when': {'field': 'speed_profile', 'equals': 'fast'},
                    'then': {'set_filter_tags': ['tag_1']},
                }
            ],
        }

        schema = self.field.output(field, preset_id='preset_1')
        self.assertEqual(schema['reactions'][0]['then']['set_filter_tags'], ['tag_1'])

    def test_output_without_reactions_is_unaffected(self):
        field = {'type': 'lora_picker', 'name': 'loras', 'configuration': {}}
        schema = self.field.output(field)
        self.assertNotIn('reactions', schema)

    # --- input() ---

    def test_input_none_returns_empty_list(self):
        self.assertEqual(self.field.input('loras', None), [])

    def test_input_empty_list_returns_empty_list(self):
        self.assertEqual(self.field.input('loras', []), [])

    def test_input_non_list_raises(self):
        with self.assertRaises(ValueError):
            self.field.input('loras', "not-a-list")

        with self.assertRaises(ValueError):
            self.field.input('loras', {'model': 'x'})

    def test_input_drops_non_dict_items(self):
        with self.assertRaises(ValueError):
            self.field.input('loras', ["not-a-dict"])

    def test_input_drops_rows_with_missing_or_empty_model(self):
        value = [
            {'model': 'lora_a.safetensors', 'strength': 1.0},
            {'strength': 0.5},  # missing model
            {'model': '', 'strength': 0.5},  # empty model
            {'model': '   ', 'strength': 0.5},  # whitespace only
            {'model': 123, 'strength': 0.5},  # non-str model
        ]

        result = self.field.input('loras', value)
        self.assertEqual(result, [{'model': 'lora_a.safetensors', 'strength': 1.0}])

    def test_input_clamps_strength(self):
        value = [
            {'model': 'lora_a.safetensors', 'strength': 10.0},
            {'model': 'lora_b.safetensors', 'strength': -10.0},
        ]

        result = self.field.input('loras', value)
        self.assertEqual(result[0]['strength'], 2.0)
        self.assertEqual(result[1]['strength'], -2.0)

    def test_input_clamps_strength_with_custom_bounds(self):
        value = [{'model': 'lora_a.safetensors', 'strength': 5.0}]
        validation_rules = {'strength_min': -1.0, 'strength_max': 1.0}

        result = self.field.input('loras', value, validation_rules)
        self.assertEqual(result[0]['strength'], 1.0)

    def test_input_coerces_string_strength(self):
        value = [{'model': 'lora_a.safetensors', 'strength': "0.75"}]
        result = self.field.input('loras', value)
        self.assertEqual(result[0]['strength'], 0.75)

    def test_input_invalid_strength_falls_back_to_default(self):
        value = [{'model': 'lora_a.safetensors', 'strength': "not-a-number"}]
        result = self.field.input('loras', value)
        self.assertEqual(result[0]['strength'], 1.0)

    def test_input_missing_strength_uses_default(self):
        value = [{'model': 'lora_a.safetensors'}]
        result = self.field.input('loras', value)
        self.assertEqual(result[0]['strength'], 1.0)

    def test_input_missing_strength_uses_custom_default(self):
        value = [{'model': 'lora_a.safetensors'}]
        validation_rules = {'strength_default': 0.5}
        result = self.field.input('loras', value, validation_rules)
        self.assertEqual(result[0]['strength'], 0.5)

    def test_input_strips_model_whitespace(self):
        value = [{'model': '  lora_a.safetensors  ', 'strength': 1.0}]
        result = self.field.input('loras', value)
        self.assertEqual(result[0]['model'], 'lora_a.safetensors')

    def test_input_enforces_max_items(self):
        value = [{'model': f'lora_{i}.safetensors', 'strength': 1.0} for i in range(7)]
        with self.assertRaises(ValueError):
            self.field.input('loras', value)

    def test_input_max_items_within_limit_ok(self):
        value = [{'model': f'lora_{i}.safetensors', 'strength': 1.0} for i in range(6)]
        result = self.field.input('loras', value)
        self.assertEqual(len(result), 6)

    def test_input_max_items_none_allows_unlimited(self):
        value = [{'model': f'lora_{i}.safetensors', 'strength': 1.0} for i in range(20)]
        validation_rules = {'max_items': None}
        result = self.field.input('loras', value, validation_rules)
        self.assertEqual(len(result), 20)

    # --- classmethod specs ---

    def test_configuration_spec_shape(self):
        specs = LoraPicker.configuration()
        self.assertTrue(all(isinstance(spec, FieldConfigSpec) for spec in specs))
        names = [spec.name for spec in specs]
        for expected in ('model_type', 'placeholder', 'strength_min', 'strength_max',
                          'strength_step', 'strength_default', 'max_items',
                          'allow_info_modal', 'show_triggers'):
            self.assertIn(expected, names)

    def test_validation_rules_spec_shape(self):
        specs = LoraPicker.validation_rules()
        self.assertTrue(all(isinstance(spec, FieldValidationSpec) for spec in specs))
        names = [spec.rule_name for spec in specs]
        self.assertIn('strength_min', names)
        self.assertIn('strength_max', names)
        self.assertIn('max_items', names)

    def test_examples_spec_shape(self):
        examples = LoraPicker.examples()
        self.assertTrue(len(examples) > 0)
        self.assertTrue(all(isinstance(ex, FieldExampleSpec) for ex in examples))


if __name__ == '__main__':
    unittest.main()
