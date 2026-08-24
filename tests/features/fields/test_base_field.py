import unittest
from unittest.mock import Mock

from src.features.fields.base_field import BaseField


class MockField(BaseField):
    """Mock implementation of BaseField for testing"""
    
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'mock'
    
    def map_field(self, field, preset_id: str = None):
        return {'type': 'mock', 'mapped': True}


class TestBaseField(unittest.TestCase):
    
    def setUp(self):
        self.preset_loader = Mock()
        self.mock_field = MockField(self.preset_loader)
    
    def test_output_default_implementation(self):
        """Test default output implementation calls map_field"""
        field = {'type': 'mock', 'name': 'test'}
        
        result = self.mock_field.output(field, 'preset_id')
        
        # Should call map_field by default
        self.assertEqual(result, {'type': 'mock', 'mapped': True})
    
    def test_input_default_implementation(self):
        """Test default input implementation passes through value"""
        test_value = 'test_value'
        
        result = self.mock_field.input('field_name', test_value)
        self.assertEqual(result, test_value)
        
        # Test with validation rules (should still pass through)
        result = self.mock_field.input('field_name', test_value, {'some': 'rule'})
        self.assertEqual(result, test_value)
    
    def test_validate_required_field(self):
        """Test validation of required fields"""
        rules = {'required': True}
        
        # Valid value
        errors = self.mock_field.validate('some_value', rules)
        self.assertEqual(errors, [])
        
        # Invalid values
        errors = self.mock_field.validate('', rules)
        self.assertIn('This field is required', errors[0])
        
        errors = self.mock_field.validate(None, rules)
        self.assertIn('This field is required', errors[0])
    
    def test_validate_min_length(self):
        """Test minimum length validation"""
        rules = {'min_length': 5}
        
        # Valid length
        errors = self.mock_field.validate('12345', rules)
        self.assertEqual(errors, [])
        
        # Invalid length
        errors = self.mock_field.validate('123', rules)
        self.assertIn('Minimum length is 5', errors[0])
    
    def test_validate_max_length(self):
        """Test maximum length validation"""
        rules = {'max_length': 10}
        
        # Valid length
        errors = self.mock_field.validate('1234567890', rules)
        self.assertEqual(errors, [])
        
        # Invalid length
        errors = self.mock_field.validate('12345678901', rules)
        self.assertIn('Maximum length is 10', errors[0])
    
    def test_validate_multiple_rules(self):
        """Test validation with multiple rules"""
        rules = {
            'required': True,
            'min_length': 3,
            'max_length': 8
        }
        
        # Valid value
        errors = self.mock_field.validate('12345', rules)
        self.assertEqual(errors, [])
        
        # Multiple violations
        errors = self.mock_field.validate('', rules)
        self.assertGreater(len(errors), 1)
        self.assertTrue(any('required' in error for error in errors))
    
    def test_get_field_info_object_format(self):
        """Test field info extraction from object format"""
        mock_field_obj = Mock()
        mock_field_obj.type = 'test_type'
        mock_field_obj.name = 'test_name'
        mock_field_obj.label = 'Test Label'
        mock_field_obj.description = 'Test Description'
        mock_field_obj.required = True
        mock_field_obj.configuration = {'option': 'value'}
        mock_field_obj.default = 'default_value'
        
        info = self.mock_field.get_field_info(mock_field_obj)
        
        self.assertEqual(info['type'], 'test_type')
        self.assertEqual(info['name'], 'test_name')
        self.assertEqual(info['label'], 'Test Label')
        self.assertEqual(info['description'], 'Test Description')
        self.assertTrue(info['required'])
        self.assertEqual(info['configuration'], {'option': 'value'})
        self.assertEqual(info['default'], 'default_value')
    
    def test_get_field_info_dict_format(self):
        """Test field info extraction from dictionary format"""
        field_dict = {
            'type': 'dict_type',
            'name': 'dict_name',
            'label': 'Dict Label',
            'description': 'Dict Description',
            'required': False,
            'configuration': {'dict_option': 'dict_value'},
            'default': 'dict_default'
        }
        
        info = self.mock_field.get_field_info(field_dict)
        
        self.assertEqual(info['type'], 'dict_type')
        self.assertEqual(info['name'], 'dict_name')
        self.assertEqual(info['label'], 'Dict Label')
        self.assertEqual(info['description'], 'Dict Description')
        self.assertFalse(info['required'])
        self.assertEqual(info['configuration'], {'dict_option': 'dict_value'})
        self.assertEqual(info['default'], 'dict_default')
    
    def test_get_field_info_missing_values(self):
        """Test field info extraction with missing values"""
        field_dict = {
            'type': 'minimal_type',
            'name': 'minimal_name'
        }
        
        info = self.mock_field.get_field_info(field_dict)
        
        self.assertEqual(info['type'], 'minimal_type')
        self.assertEqual(info['name'], 'minimal_name')
        self.assertEqual(info['label'], 'minimal_name')  # Uses name as label
        self.assertEqual(info['description'], '')
        self.assertFalse(info['required'])
        self.assertEqual(info['configuration'], {})
        self.assertIsNone(info['default'])
    
    def test_get_field_info_label_fallback(self):
        """Test that label falls back to name when missing"""
        # Object format
        mock_field_obj = Mock()
        mock_field_obj.type = 'test'
        mock_field_obj.name = 'test_name'
        mock_field_obj.label = None  # Missing label
        mock_field_obj.required = False
        mock_field_obj.configuration = {}
        
        info = self.mock_field.get_field_info(mock_field_obj)
        self.assertEqual(info['label'], 'test_name')
        
        # Dict format
        field_dict = {'type': 'test', 'name': 'dict_name'}
        info = self.mock_field.get_field_info(field_dict)
        self.assertEqual(info['label'], 'dict_name')
    
    def test_create_base_schema(self):
        """Test base schema creation"""
        field_info = {
            'type': 'test_type',
            'name': 'test_name',
            'label': 'Test Label',
            'description': 'Test Description',
            'default': 'test_default'
        }
        
        schema = self.mock_field.create_base_schema(field_info)
        
        self.assertEqual(schema['type'], 'test_type')
        self.assertEqual(schema['name'], 'test_name')
        self.assertEqual(schema['title'], 'Test Label')
        self.assertEqual(schema['description'], 'Test Description')
        self.assertEqual(schema['default'], 'test_default')
    
    def test_create_base_schema_no_default(self):
        """Test base schema creation without default value"""
        field_info = {
            'type': 'test_type',
            'name': 'test_name',
            'label': 'Test Label',
            'description': 'Test Description',
            'default': None
        }

        schema = self.mock_field.create_base_schema(field_info)

        # Default should not be included when None
        self.assertNotIn('default', schema)

    def test_get_field_info_audience_object_format(self):
        """Test that `audience` is extracted from an object-format field"""
        mock_field_obj = Mock()
        mock_field_obj.type = 'test_type'
        mock_field_obj.name = 'test_name'
        mock_field_obj.label = 'Test Label'
        mock_field_obj.required = False
        mock_field_obj.configuration = {}
        mock_field_obj.default = None
        mock_field_obj.audience = 'advanced'

        info = self.mock_field.get_field_info(mock_field_obj)

        self.assertEqual(info['audience'], 'advanced')

    def test_get_field_info_audience_dict_format(self):
        """Test that `audience` is extracted from a dict-format field"""
        field_dict = {'type': 'test', 'name': 'n', 'audience': 'advanced'}
        info = self.mock_field.get_field_info(field_dict)
        self.assertEqual(info['audience'], 'advanced')

    def test_get_field_info_audience_defaults_to_simple(self):
        """A field with no `audience` key at all defaults to 'simple'"""
        field_dict = {'type': 'test', 'name': 'n'}
        info = self.mock_field.get_field_info(field_dict)
        self.assertEqual(info['audience'], 'simple')

    def test_create_base_schema_always_includes_audience(self):
        """`audience` is always present in the serialized schema, defaulting
        to 'simple', so the frontend never special-cases a missing key."""
        field_info = {
            'type': 'test_type',
            'name': 'test_name',
            'label': 'Test Label',
            'description': 'Test Description',
            'default': None,
        }

        schema = self.mock_field.create_base_schema(field_info)
        self.assertEqual(schema['audience'], 'simple')

        field_info['audience'] = 'advanced'
        schema = self.mock_field.create_base_schema(field_info)
        self.assertEqual(schema['audience'], 'advanced')

    def test_get_field_info_hidden_when_video_director_object_format(self):
        """Test that `hidden_when_video_director` is extracted from an object-format field"""
        mock_field_obj = Mock()
        mock_field_obj.type = 'test_type'
        mock_field_obj.name = 'test_name'
        mock_field_obj.label = 'Test Label'
        mock_field_obj.required = False
        mock_field_obj.configuration = {}
        mock_field_obj.default = None
        mock_field_obj.hidden_when_video_director = True

        info = self.mock_field.get_field_info(mock_field_obj)

        self.assertTrue(info['hidden_when_video_director'])

    def test_get_field_info_hidden_when_video_director_defaults_to_false(self):
        """A field with no `hidden_when_video_director` key at all defaults to False"""
        field_dict = {'type': 'test', 'name': 'n'}
        info = self.mock_field.get_field_info(field_dict)
        self.assertFalse(info['hidden_when_video_director'])

    def test_create_base_schema_omits_hidden_when_video_director_by_default(self):
        """Only emitted when true - mirrors `full_width`'s pattern, so the
        frontend never has to special-case an absent key as anything but hidden=false."""
        field_info = {
            'type': 'test_type',
            'name': 'test_name',
            'label': 'Test Label',
            'description': 'Test Description',
            'default': None,
        }

        schema = self.mock_field.create_base_schema(field_info)
        self.assertNotIn('hidden_when_video_director', schema)

        field_info['hidden_when_video_director'] = True
        schema = self.mock_field.create_base_schema(field_info)
        self.assertTrue(schema['hidden_when_video_director'])

    def test_find_preset_by_id(self):
        """Test finding preset by ID"""
        # Setup mock preset data
        preset1 = Mock()
        preset1.id = 'preset_1'
        preset2 = Mock()
        preset2.id = 'preset_2'
        
        self.preset_loader.presets = [preset1, preset2]
        
        # Test finding existing preset
        found = self.mock_field._find_preset_by_id('preset_2')
        self.assertEqual(found, preset2)
        
        # Test not finding preset
        not_found = self.mock_field._find_preset_by_id('nonexistent')
        self.assertIsNone(not_found)
        
        # Test with None ID
        none_result = self.mock_field._find_preset_by_id(None)
        self.assertIsNone(none_result)
        
        # Test with empty ID
        empty_result = self.mock_field._find_preset_by_id('')
        self.assertIsNone(empty_result)
    
    def test_validation_edge_cases(self):
        """Test validation edge cases"""
        # Test numeric value for length validation
        errors = self.mock_field.validate(12345, {'min_length': 3})
        self.assertEqual(errors, [])  # str(12345) = '12345' has length 5
        
        # Test boolean value
        errors = self.mock_field.validate(True, {'required': True})
        self.assertEqual(errors, [])  # True is truthy
        
        errors = self.mock_field.validate(False, {'required': True})
        self.assertIn('required', errors[0])  # False is falsy
        
        # Test empty list/dict (falsy but has length)
        errors = self.mock_field.validate([], {'required': True})
        self.assertIn('required', errors[0])
        
        errors = self.mock_field.validate({}, {'required': True})
        self.assertIn('required', errors[0])


if __name__ == '__main__':
    unittest.main()