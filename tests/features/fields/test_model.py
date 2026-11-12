"""Focused tests for src.features.fields.model.Model.output() - filter_tags
resolution (the field's own static `configuration.filter_tags`, and any
`set_filter_tags` inside the field's `reactions`). Not full
coverage of the field (marketplace-recommendation serialization etc. is
pre-existing and untested elsewhere)."""

import unittest
from unittest.mock import Mock

from src.features.fields.model import Model


class TestModelField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.field = Model(self.preset_loader)

    def test_output_default_configuration(self):
        field = {'type': 'model', 'name': 'diffusion_model', 'configuration': {}}
        schema = self.field.output(field, preset_id='preset_1')

        self.assertEqual(schema['type'], 'model')
        config = schema['configuration']
        self.assertEqual(config['model_type'], 'checkpoint')
        self.assertIsNone(config['filter_tags'])
        self.assertEqual(schema['preset_id'], 'preset_1')

    def test_output_static_filter_tags_literal_list(self):
        field = {'type': 'model', 'name': 'm', 'configuration': {'filter_tags': ['tag_1']}}
        schema = self.field.output(field, preset_id='preset_1')
        self.assertEqual(schema['configuration']['filter_tags'], ['tag_1'])

    def test_output_static_filter_tags_without_preset_id_resolves_to_none(self):
        field = {'type': 'model', 'name': 'm', 'configuration': {'filter_tags': ['tag_1']}}
        schema = self.field.output(field)
        self.assertIsNone(schema['configuration']['filter_tags'])

    def test_output_resolves_set_filter_tags_in_reactions(self):
        field = {
            'type': 'model',
            'name': 'diffusion_model',
            'configuration': {'filter_tags': ['tag_base']},
            'reactions': [
                {
                    'when': {'field': 'speed_profile', 'equals': 'balanced'},
                    'then': {'set_filter_tags': ['tag_base']},
                },
                {
                    'when': {'field': 'speed_profile', 'equals': 'fast'},
                    'then': {'set_filter_tags': ['tag_distilled']},
                },
            ],
        }

        schema = self.field.output(field, preset_id='preset_1')
        reactions = schema['reactions']
        self.assertEqual(reactions[0]['then']['set_filter_tags'], ['tag_base'])
        self.assertEqual(reactions[1]['then']['set_filter_tags'], ['tag_distilled'])
        # The reaction's own `when` block is untouched by the resolution pass.
        self.assertEqual(reactions[1]['when'], {'field': 'speed_profile', 'equals': 'fast'})

    def test_output_reactions_without_set_filter_tags_are_untouched(self):
        field = {
            'type': 'model',
            'name': 'diffusion_model',
            'configuration': {},
            'reactions': [
                {'when': {'field': 'speed_profile', 'equals': 'fast'}, 'then': {'set_disabled': True}}
            ],
        }

        schema = self.field.output(field, preset_id='preset_1')
        self.assertEqual(schema['reactions'][0]['then'], {'set_disabled': True})

    def test_output_without_reactions_is_unaffected(self):
        field = {'type': 'model', 'name': 'm', 'configuration': {}}
        schema = self.field.output(field, preset_id='preset_1')
        self.assertNotIn('reactions', schema)


class TestModelFieldTypeList(unittest.TestCase):

    def setUp(self):
        self.field = Model(Mock())

    def _config(self, **configuration):
        return self.field.output({'type': 'model', 'name': 'm', 'configuration': configuration}, preset_id='p')['configuration']

    def test_model_types_are_served_and_the_first_stands_in_for_model_type(self):
        config = self._config(model_types=['diffusion_model', 'unet', 'checkpoint', 'text_encoder'])
        self.assertEqual(config['model_types'], ['diffusion_model', 'unet', 'checkpoint', 'text_encoder'])
        self.assertEqual(config['model_type'], 'diffusion_model')

    def test_an_explicit_model_type_is_kept(self):
        config = self._config(model_type='unet', model_types=['diffusion_model', 'unet'])
        self.assertEqual(config['model_type'], 'unet')

    def test_unknown_and_duplicate_types_are_dropped(self):
        config = self._config(model_types=['unet', 'bogus', 'unet', 7, 'lora'])
        self.assertEqual(config['model_types'], ['unet', 'lora'])

    def test_no_list_leaves_the_single_type_shape_untouched(self):
        config = self._config(model_type='lora')
        self.assertNotIn('model_types', config)
        self.assertNotIn('unquantized_only', config)
        self.assertEqual(config['model_type'], 'lora')

    def test_unquantized_only_is_served_only_when_true(self):
        self.assertIs(self._config(unquantized_only=True)['unquantized_only'], True)
        self.assertNotIn('unquantized_only', self._config(unquantized_only=False))
        self.assertNotIn('unquantized_only', self._config(unquantized_only='yes'))

    def test_families_are_served_trimmed_and_deduplicated_and_only_when_present(self):
        self.assertEqual(self._config(families=['minimax_h3', ' z_image ', 'minimax_h3', 7, ''])['families'], ['minimax_h3', 'z_image'])
        self.assertNotIn('families', self._config(families=[]))
        self.assertNotIn('families', self._config(families='minimax_h3'))
        self.assertNotIn('families', self._config())

    def test_families_is_declared_in_the_field_configuration_spec(self):
        self.assertIn('families', {spec.name for spec in Model.configuration()})

    def test_both_options_are_declared_in_the_field_configuration_spec(self):
        names = {spec.name for spec in Model.configuration()}
        self.assertTrue({'model_types', 'unquantized_only'} <= names)


if __name__ == '__main__':
    unittest.main()
