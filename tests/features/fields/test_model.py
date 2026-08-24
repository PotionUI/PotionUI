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

    def test_can_handle(self):
        self.assertTrue(self.field.can_handle('model'))
        self.assertFalse(self.field.can_handle('lora_picker'))

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


if __name__ == '__main__':
    unittest.main()
