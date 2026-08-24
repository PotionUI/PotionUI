"""Tests for fields documenter."""
import pytest
from unittest.mock import Mock, MagicMock
from src.features.developer.fields_documenter import FieldsDocumenter


class TestFieldsDocumenter:
    """Test suite for FieldsDocumenter."""

    @pytest.fixture
    def mock_field_factory(self):
        """Create a mock field factory."""
        factory = Mock()
        factory.fields = []
        return factory

    @pytest.fixture
    def mock_field_impl(self):
        """Create a mock field implementation."""
        field = Mock()
        field.__class__.__name__ = 'TextField'
        field.__class__.__doc__ = 'Text field documentation'

        # Mock static methods
        field.__class__.description = Mock(return_value='Text input field')
        field.__class__.configuration = Mock(return_value=[])
        field.__class__.validation_rules = Mock(return_value=[])
        field.__class__.examples = Mock(return_value=[])

        return field

    def test_initialization(self, mock_field_factory):
        """Test documenter can be initialized."""
        documenter = FieldsDocumenter(mock_field_factory)
        assert documenter is not None
        assert documenter.field_factory == mock_field_factory

    def test_normalize_field_type_standard(self):
        """Test normalizing standard field type names."""
        documenter = FieldsDocumenter(Mock())

        assert documenter._normalize_field_type('TextField') == 'textfield'
        assert documenter._normalize_field_type('SelectField') == 'selectfield'

    def test_normalize_field_type_with_mappings(self):
        """Test normalizing field types with special mappings."""
        documenter = FieldsDocumenter(Mock())

        assert documenter._normalize_field_type('DefaultField') == 'default'
        assert documenter._normalize_field_type('CheckboxGroup') == 'checkbox_group'

    def test_get_data_sources_select(self):
        """Test getting data sources for select fields."""
        documenter = FieldsDocumenter(Mock())

        sources = documenter._get_data_sources('select')
        assert 'static' in sources
        assert 'file' in sources
        assert 'filesystem' in sources

    def test_get_data_sources_model(self):
        """Test getting data sources for model fields."""
        documenter = FieldsDocumenter(Mock())

        sources = documenter._get_data_sources('model')
        assert sources == ['database']

    def test_get_data_sources_default(self):
        """Test getting data sources for other field types."""
        documenter = FieldsDocumenter(Mock())

        sources = documenter._get_data_sources('text')
        assert sources == ['static']

    def test_get_can_handle_container(self):
        """Test getting can_handle for container field."""
        documenter = FieldsDocumenter(Mock())
        field = Mock()

        can_handle = documenter._get_can_handle('container', field)
        assert 'container' in can_handle
        assert 'accordion' in can_handle
        assert 'tabs' in can_handle

    def test_get_can_handle_default(self):
        """Test getting can_handle for default field."""
        documenter = FieldsDocumenter(Mock())
        field = Mock()

        can_handle = documenter._get_can_handle('default', field)
        assert '*' in can_handle

    def test_get_can_handle_standard(self):
        """Test getting can_handle for standard fields."""
        documenter = FieldsDocumenter(Mock())
        field = Mock()

        can_handle = documenter._get_can_handle('text', field)
        assert 'text' in can_handle

    def test_document_field_basic_info(self, mock_field_impl):
        """Test documenting a field returns basic info."""
        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(mock_field_impl)

        assert 'type' in result
        assert 'class_name' in result
        assert 'description' in result

        assert result['class_name'] == 'TextField'
        assert result['description'] == 'Text input field'

    def test_document_field_with_configuration(self, mock_field_impl):
        """Test documenting field with configuration."""
        config_spec = Mock()
        config_spec.name = 'max_length'
        config_spec.param_type = int
        config_spec.default = 100
        config_spec.description = 'Maximum length'
        config_spec.required = False
        config_spec.choices = None
        config_spec.example = '100'

        mock_field_impl.__class__.configuration = Mock(return_value=[config_spec])

        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(mock_field_impl)

        assert 'configuration' in result
        assert len(result['configuration']) == 1

        config_doc = result['configuration'][0]
        assert config_doc['name'] == 'max_length'
        assert config_doc['param_type'] == 'int'
        assert config_doc['default'] == 100
        assert config_doc['description'] == 'Maximum length'
        assert config_doc['required'] is False
        assert config_doc['example'] == '100'

    def test_document_field_with_validation_rules(self, mock_field_impl):
        """Test documenting field with validation rules."""
        validation_rule = Mock()
        validation_rule.rule_name = 'required'
        validation_rule.description = 'Field is required'
        validation_rule.param_type = bool
        validation_rule.example = 'required: true'

        mock_field_impl.__class__.validation_rules = Mock(return_value=[validation_rule])

        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(mock_field_impl)

        assert 'validation_rules' in result
        assert len(result['validation_rules']) == 1

        rule_doc = result['validation_rules'][0]
        assert rule_doc['rule_name'] == 'required'
        assert rule_doc['description'] == 'Field is required'
        assert rule_doc['param_type'] == 'bool'
        assert rule_doc['example'] == 'required: true'

    def test_document_field_with_examples(self, mock_field_impl):
        """Test documenting field with examples."""
        example = Mock()
        example.title = 'Basic text field'
        example.description = 'Simple text input'
        example.yaml_config = 'type: text'
        example.rendered_output = '<input type="text">'
        example.frontend_preview = 'Preview image'

        mock_field_impl.__class__.examples = Mock(return_value=[example])

        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(mock_field_impl)

        assert 'examples' in result
        assert len(result['examples']) == 1

        example_doc = result['examples'][0]
        assert example_doc['title'] == 'Basic text field'
        assert example_doc['description'] == 'Simple text input'
        assert example_doc['yaml_config'] == 'type: text'

    def test_document_field_includes_metadata(self, mock_field_impl):
        """Test documented field includes data_sources and can_handle."""
        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(mock_field_impl)

        assert 'data_sources' in result
        assert 'can_handle' in result
        assert isinstance(result['data_sources'], list)
        assert isinstance(result['can_handle'], list)

    def test_generate_documentation_empty_factory(self, mock_field_factory):
        """Test generating documentation with empty field factory."""
        mock_field_factory.fields = []

        documenter = FieldsDocumenter(mock_field_factory)
        result = documenter.generate_documentation()

        assert 'fields' in result
        assert 'total' in result
        assert result['fields'] == []
        assert result['total'] == 0

    def test_generate_documentation_with_fields(self, mock_field_factory, mock_field_impl):
        """Test generating documentation with fields."""
        mock_field_factory.fields = [mock_field_impl, mock_field_impl]

        documenter = FieldsDocumenter(mock_field_factory)
        result = documenter.generate_documentation()

        assert 'fields' in result
        assert 'total' in result
        assert len(result['fields']) == 2
        assert result['total'] == 2

    def test_generate_documentation_handles_errors(self, mock_field_factory):
        """Test generating documentation handles field documentation errors gracefully."""
        # Create a field that will raise an error when documented
        error_field = Mock()
        error_field.__class__.__name__ = 'ErrorField'
        error_field.__class__.description = Mock(side_effect=Exception("Test error"))

        mock_field_factory.fields = [error_field]

        documenter = FieldsDocumenter(mock_field_factory)
        result = documenter.generate_documentation()

        # Should still return a result
        assert 'fields' in result
        assert len(result['fields']) == 1

        # Error field should have error description
        error_doc = result['fields'][0]
        assert error_doc['type'] == 'unknown'
        assert error_doc['class_name'] == 'ErrorField'
        assert 'Error documenting field' in error_doc['description']

    def test_generate_documentation_is_sorted(self, mock_field_factory):
        """Test generated documentation is sorted by type."""
        field_a = Mock()
        field_a.__class__.__name__ = 'ZField'
        field_a.__class__.description = Mock(return_value='Z field')
        field_a.__class__.configuration = Mock(return_value=[])
        field_a.__class__.validation_rules = Mock(return_value=[])
        field_a.__class__.examples = Mock(return_value=[])

        field_b = Mock()
        field_b.__class__.__name__ = 'AField'
        field_b.__class__.description = Mock(return_value='A field')
        field_b.__class__.configuration = Mock(return_value=[])
        field_b.__class__.validation_rules = Mock(return_value=[])
        field_b.__class__.examples = Mock(return_value=[])

        mock_field_factory.fields = [field_a, field_b]

        documenter = FieldsDocumenter(mock_field_factory)
        result = documenter.generate_documentation()

        # Should be sorted alphabetically by type
        assert result['fields'][0]['type'] == 'afield'
        assert result['fields'][1]['type'] == 'zfield'

    def test_document_field_without_description_method(self):
        """Test documenting a field without description method."""
        # Create a proper mock class without description attribute
        class NoDescField:
            __doc__ = 'Fallback documentation'

            @staticmethod
            def configuration():
                return []

            @staticmethod
            def validation_rules():
                return []

            @staticmethod
            def examples():
                return []

        field = Mock()
        field.__class__ = NoDescField

        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(field)

        assert result['description'] == 'Fallback documentation'

    def test_document_field_without_any_documentation(self):
        """Test documenting a field without any documentation."""
        # Create a proper mock class without any documentation
        class NoDocField:
            __doc__ = None

            @staticmethod
            def configuration():
                return []

            @staticmethod
            def validation_rules():
                return []

            @staticmethod
            def examples():
                return []

        field = Mock()
        field.__class__ = NoDocField

        documenter = FieldsDocumenter(Mock())
        result = documenter._document_field(field)

        assert result['description'] == 'No description available'
