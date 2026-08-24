import unittest
from unittest.mock import Mock

from src.features.fields.alert import Alert


class TestAlertField(unittest.TestCase):

    def setUp(self):
        self.preset_loader = Mock()
        self.alert_field = Alert(self.preset_loader)

    def test_can_handle(self):
        self.assertTrue(self.alert_field.can_handle('alert'))
        self.assertFalse(self.alert_field.can_handle('markdown'))

    def test_variant_authored_maps_through(self):
        """A preset authoring `configuration.variant` (the declared
        FieldConfigSpec name) must map straight through to `schema['variant']`
        - this is the correct shape, as opposed to the `configuration.type`
        typo alerts used to ship with."""
        field = {
            'type': 'alert',
            'name': 'warning_alert',
            'configuration': {
                'variant': 'warning',
                'content': 'Careful with this setting.',
            },
        }

        result = self.alert_field.map_field(field)

        self.assertEqual(result['variant'], 'warning')
        self.assertEqual(result['content'], 'Careful with this setting.')

    def test_missing_variant_defaults_to_default(self):
        field = {
            'type': 'alert',
            'configuration': {'content': 'Just some info.'},
        }

        result = self.alert_field.map_field(field)

        self.assertEqual(result['variant'], 'default')

    def test_configuration_type_key_is_not_read_as_variant(self):
        """The pre-fix shape: `configuration.type` (a leftover duplicate of
        the field's own `type: alert` discriminator) is NOT a declared config
        key and must NOT be read as the variant - it silently falls back to
        'default', which is exactly the bug this field's docstring history
        describes."""
        field = {
            'type': 'alert',
            'configuration': {'type': 'warning', 'content': 'Careful.'},
        }

        result = self.alert_field.map_field(field)

        self.assertEqual(result['variant'], 'default')

    def test_content_falls_back_to_description(self):
        field = {
            'type': 'alert',
            'description': 'Fallback description text',
            'configuration': {'variant': 'success'},
        }

        result = self.alert_field.map_field(field)

        self.assertEqual(result['content'], 'Fallback description text')


    def test_configuration_spec_declares_variant_and_content(self):
        names = {spec.name for spec in Alert.configuration()}
        self.assertIn('variant', names)
        self.assertIn('content', names)
        self.assertNotIn('type', names)
        self.assertNotIn('message', names)

    def test_configuration_spec_does_not_declare_dead_keys(self):
        """`icon`, `closable`, `startContent`, `endContent` and `radius` were
        computed into the schema but never read by AlertField.svelte /
        ui/Alert.svelte, and no preset in the tree authors any of them -
        dropped rather than kept as unread schema noise."""
        names = {spec.name for spec in Alert.configuration()}
        for dead_key in ('icon', 'closable', 'startContent', 'endContent', 'radius'):
            self.assertNotIn(dead_key, names)

    def test_map_field_does_not_echo_dead_keys(self):
        field = {
            'type': 'alert',
            'configuration': {
                'variant': 'warning',
                'content': 'Careful.',
                'icon': 'check',
                'closable': True,
                'startContent': 'x',
                'endContent': 'y',
                'radius': 'md',
            },
        }

        result = self.alert_field.map_field(field)

        for dead_key in ('icon', 'closable', 'startContent', 'endContent', 'radius'):
            self.assertNotIn(dead_key, result)


if __name__ == '__main__':
    unittest.main()
