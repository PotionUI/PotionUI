import unittest
from unittest.mock import Mock, MagicMock
from src.features.presets.form_serializer import PresetFormSerializer
from src.features.presets import PresetTemplateLoader
from src.features.presets.templates import FieldTemplate
from src.features.fields.field_factory import FieldFactory


class MockFieldTemplate:
    """Mock field template for testing"""
    def __init__(self, name, field_type, required=False, children=None):
        self.name = name
        self.type = field_type
        self.required = required
        self.children = children or []


class MockFormConfig:
    """Mock form config for testing"""
    def __init__(self, fields=None):
        self.fields = fields or []


class TestPresetFormSerializer(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.mock_preset_loader = Mock(spec=PresetTemplateLoader)
        self.mapper = PresetFormSerializer(self.mock_preset_loader)
        
    def test_init(self):
        """Test mapper initialization"""
        self.assertIsInstance(self.mapper.preset_loader, PresetTemplateLoader)
        self.assertIsInstance(self.mapper.field_factory, FieldFactory)
        
    def test_process_form_fields_with_empty_config(self):
        """Test processing with empty or None form config"""
        # Test with None
        result = self.mapper.process_form_fields(None, "test_preset")
        self.assertEqual(result['type'], 'object')
        self.assertEqual(result['properties'], {})
        self.assertEqual(result['required'], [])
        
        # Test with config without fields attribute
        mock_config = Mock()
        del mock_config.fields  # Remove the fields attribute entirely
        result = self.mapper.process_form_fields(mock_config, "test_preset")
        self.assertEqual(result['type'], 'object')
        self.assertEqual(result['properties'], {})
        self.assertEqual(result['required'], [])
        
    def test_process_form_fields_with_simple_fields(self):
        """Test processing simple form fields"""
        # Mock field factory to return simple schemas
        self.mapper.field_factory.map_field = MagicMock(side_effect=lambda field, preset_id: {
            'type': self.mapper.get_json_schema_type(field.type),
            'title': getattr(field, 'label', field.name)
        })
        
        # Create test fields
        fields = [
            MockFieldTemplate('prompt', 'textbox', required=True),
            MockFieldTemplate('steps', 'slider', required=False),
            MockFieldTemplate('seed', 'number', required=False)
        ]
        
        form_config = MockFormConfig(fields=fields)
        
        result = self.mapper.process_form_fields(form_config, "test_preset")
        
        # Check schema structure
        self.assertEqual(result['type'], 'object')
        self.assertIn('prompt', result['properties'])
        self.assertIn('steps', result['properties'])
        self.assertIn('seed', result['properties'])
        self.assertEqual(result['required'], ['prompt'])
        
        # Verify field factory was called correctly
        self.assertEqual(self.mapper.field_factory.map_field.call_count, 3)
        
    def test_process_field_recursive_with_tabs(self):
        """Test processing tabs field without name"""
        # Mock field factory
        self.mapper.field_factory.map_field = MagicMock(return_value={'type': 'object'})
        
        # Create tabs field without name
        tabs_field = MockFieldTemplate(None, 'tabs')
        
        form_config = MockFormConfig(fields=[tabs_field])
        result = self.mapper.process_form_fields(form_config, "test_preset")
        
        # Should use 'tabs' as default name
        self.assertIn('tabs', result['properties'])
        
    def test_process_field_recursive_with_dict_input(self):
        """Test processing field as dictionary instead of object"""
        # Mock field factory
        self.mapper.field_factory.map_field = MagicMock(return_value={'type': 'string'})
        
        # Create field as dictionary
        dict_field = {
            'type': 'textbox',
            'name': 'test_field',
            'required': True
        }
        
        schema = {
            'type': 'object',
            'properties': {},
            'required': []
        }
        
        self.mapper._process_field_recursive(dict_field, schema, "test_preset")
        
        self.assertIn('test_field', schema['properties'])
        self.assertIn('test_field', schema['required'])
        
    def test_get_json_schema_type(self):
        """Test JSON schema type mapping"""
        # Test known types
        self.assertEqual(self.mapper.get_json_schema_type('textbox'), 'string')
        self.assertEqual(self.mapper.get_json_schema_type('select'), 'string')
        self.assertEqual(self.mapper.get_json_schema_type('slider'), 'number')
        self.assertEqual(self.mapper.get_json_schema_type('number'), 'number')
        self.assertEqual(self.mapper.get_json_schema_type('checkbox_group'), 'array')
        self.assertEqual(self.mapper.get_json_schema_type('button'), 'null')
        
        # Test unknown type (defaults to string)
        self.assertEqual(self.mapper.get_json_schema_type('unknown_type'), 'string')
        
    def test_process_form_fields_with_nested_fields(self):
        """Test processing form with nested/container fields"""
        # Mock field factory
        self.mapper.field_factory.map_field = MagicMock(side_effect=lambda field, preset_id: {
            'type': 'object' if field.type == 'container' else self.mapper.get_json_schema_type(field.type)
        })
        
        # Create nested structure
        child_field = MockFieldTemplate('child_field', 'textbox', required=True)
        container_field = MockFieldTemplate('settings', 'container', children=[child_field])
        
        form_config = MockFormConfig(fields=[container_field])
        result = self.mapper.process_form_fields(form_config, "test_preset")
        
        self.assertIn('settings', result['properties'])
        self.assertEqual(self.mapper.field_factory.map_field.call_count, 1)
        
    def test_process_field_recursive_without_name(self):
        """Test processing field without name (non-tabs)"""
        # Mock field factory
        self.mapper.field_factory.map_field = MagicMock(return_value={'type': 'string'})
        
        # Create field without name (not tabs)
        unnamed_field = MockFieldTemplate(None, 'textbox')
        
        schema = {
            'type': 'object',
            'properties': {},
            'required': []
        }
        
        # Should not process field without name (non-tabs)
        self.mapper._process_field_recursive(unnamed_field, schema, "test_preset")
        
        # Field factory should NOT be called for fields without names (except tabs)
        self.mapper.field_factory.map_field.assert_not_called()
        # Properties should remain empty since field has no name
        self.assertEqual(len(schema['properties']), 0)
        
    def test_process_form_fields_integration(self):
        """Test full integration with complex form structure"""
        # Mock field factory with more complex return values
        def mock_map_field(field, preset_id):
            if field.type == 'textbox':
                return {
                    'type': 'string',
                    'title': field.name.replace('_', ' ').title()
                }
            elif field.type == 'select':
                return {
                    'type': 'string',
                    'enum': ['option1', 'option2']
                }
            elif field.type == 'checkbox_group':
                return {
                    'type': 'array',
                    'items': {'type': 'string'}
                }
            return {'type': 'string'}
            
        self.mapper.field_factory.map_field = MagicMock(side_effect=mock_map_field)
        
        # Create complex form structure
        fields = [
            MockFieldTemplate('prompt', 'textbox', required=True),
            MockFieldTemplate('style', 'select', required=True),
            MockFieldTemplate('features', 'checkbox_group', required=False)
        ]
        
        form_config = MockFormConfig(fields=fields)
        result = self.mapper.process_form_fields(form_config, "test_preset")
        
        # Verify structure
        self.assertEqual(result['type'], 'object')
        self.assertEqual(len(result['properties']), 3)
        self.assertEqual(result['required'], ['prompt', 'style'])
        
        # Verify individual field schemas
        self.assertEqual(result['properties']['prompt']['type'], 'string')
        self.assertEqual(result['properties']['style']['type'], 'string')
        self.assertIn('enum', result['properties']['style'])
        self.assertEqual(result['properties']['features']['type'], 'array')


class TestProcessFormFieldsOverrides(unittest.TestCase):
    """The v2 seam: `overrides` is applied inside process_form_fields, right
    after @loop/external-children resolution, through the real field-type
    pipeline (not a mocked field_factory) - proves `readonly: true` actually
    reaches the rendered schema via base_field.create_base_schema."""

    def setUp(self):
        self.mock_preset_loader = Mock(spec=PresetTemplateLoader)
        preset_template = Mock()
        preset_template.path = "/presets/test_preset"
        self.mock_preset_loader.load_preset_by_id.return_value = preset_template
        self.mapper = PresetFormSerializer(self.mock_preset_loader)

    def test_visible_false_removes_the_field_from_properties(self):
        fields = [
            MockFieldTemplate('steps', 'slider'),
            MockFieldTemplate('checkpoint', 'textbox'),
        ]
        result = self.mapper.process_form_fields(
            MockFormConfig(fields=fields), "test_preset",
            overrides={"checkpoint": {"visible": False}},
        )
        self.assertIn('steps', result['properties'])
        self.assertNotIn('checkpoint', result['properties'])

    def test_editable_false_reaches_rendered_schema_as_readonly(self):
        fields = [MockFieldTemplate('steps', 'slider')]
        result = self.mapper.process_form_fields(
            MockFormConfig(fields=fields), "test_preset",
            overrides={"steps": {"editable": False}},
        )
        self.assertIs(result['properties']['steps']['readonly'], True)

    def test_no_overrides_key_means_no_readonly_key(self):
        fields = [MockFieldTemplate('steps', 'slider')]
        result = self.mapper.process_form_fields(MockFormConfig(fields=fields), "test_preset")
        self.assertNotIn('readonly', result['properties']['steps'])

    def test_default_override_reaches_rendered_schema(self):
        fields = [MockFieldTemplate('steps', 'slider')]
        result = self.mapper.process_form_fields(
            MockFormConfig(fields=fields), "test_preset",
            overrides={"steps": {"default": 30}},
        )
        self.assertEqual(result['properties']['steps']['default'], 30)

    def test_row_child_width_survives_end_to_end(self):
        """A field's `width:` must reach the rendered schema unmodified when
        nested under a `type: "row"` container's `children` - proves the real
        Container -> FieldFactory -> base_field.create_base_schema path
        (not a mocked field_factory) doesn't strip it."""
        row = FieldTemplate(
            type='row',
            name='layout_row',
            children=[
                FieldTemplate(type='slider', name='steps', width='3/5'),
                FieldTemplate(type='slider', name='cfg', width=2),
                FieldTemplate(type='slider', name='seed'),
            ],
        )
        result = self.mapper.process_form_fields(MockFormConfig(fields=[row]), "test_preset")

        row_schema = result['properties']['layout_row']
        children_by_name = {c['name']: c for c in row_schema['children']}

        self.assertEqual(children_by_name['steps']['width'], '3/5')
        self.assertEqual(children_by_name['cfg']['width'], 2)
        self.assertNotIn('width', children_by_name['seed'])

    def test_row_child_full_width_survives_end_to_end(self):
        """A field's `full_width: true` must reach the rendered schema when
        nested under a `type: "row"` container's `children`, and must be
        absent (not `false`) when not set - matches the width test's real
        Container -> FieldFactory -> base_field.create_base_schema path."""
        row = FieldTemplate(
            type='row',
            name='layout_row',
            children=[
                FieldTemplate(type='stepper', name='quantity', full_width=True),
                FieldTemplate(type='stepper', name='batch_size'),
            ],
        )
        result = self.mapper.process_form_fields(MockFormConfig(fields=[row]), "test_preset")

        row_schema = result['properties']['layout_row']
        children_by_name = {c['name']: c for c in row_schema['children']}

        self.assertIs(children_by_name['quantity']['full_width'], True)
        self.assertNotIn('full_width', children_by_name['batch_size'])


if __name__ == '__main__':
    unittest.main()