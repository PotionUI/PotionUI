"""Tests for template functions documenter (post-templating-rework surface)."""
import pytest
from src.features.developer.template_functions_documenter import TemplateFunctionsDocumenter


class TestTemplateFunctionsDocumenter:
    """Test suite for TemplateFunctionsDocumenter."""

    def test_initialization(self):
        """Test documenter can be initialized."""
        documenter = TemplateFunctionsDocumenter()
        assert documenter is not None

    def test_get_function_categories_structure(self):
        """Test _get_function_categories returns proper structure."""
        documenter = TemplateFunctionsDocumenter()
        categories = documenter._get_function_categories()

        assert isinstance(categories, dict)
        assert len(categories) > 0

        # The categories reflect the ACTUAL post-rework surface: three global
        # helpers, filters, and the template context roots. The deleted
        # globals' old categories ("Value Access", "Form Access", "Settings",
        # "Conditionals", "Dictionary Access") must NOT reappear.
        expected_categories = [
            "Path Helpers", "Icon Mapping", "Speed Profiles",
            "Filters", "Template Context",
        ]
        for category in expected_categories:
            assert category in categories

        deleted_categories = [
            "Value Access", "Form Access", "Settings",
            "Conditionals", "Dictionary Access",
        ]
        for category in deleted_categories:
            assert category not in categories

    def test_function_documentation_structure(self):
        """Test each entry has required fields."""
        documenter = TemplateFunctionsDocumenter()
        categories = documenter._get_function_categories()

        for category_name, functions in categories.items():
            for func in functions:
                # Required fields
                assert 'name' in func
                assert 'signature' in func
                assert 'description' in func
                assert 'parameters' in func
                assert 'return_type' in func
                assert 'examples' in func

                # Optional field
                assert 'alias' in func

                # Validate parameters structure
                assert isinstance(func['parameters'], list)
                for param in func['parameters']:
                    assert 'name' in param
                    assert 'type' in param
                    assert 'description' in param

                # Validate examples structure
                assert isinstance(func['examples'], list)
                for example in func['examples']:
                    assert 'code' in example
                    assert 'result' in example

    def test_generate_documentation_structure(self):
        """Test generate_documentation returns correct structure."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        assert 'functions' in result
        assert 'total' in result
        assert 'categories' in result

        assert isinstance(result['functions'], list)
        assert isinstance(result['total'], int)
        assert isinstance(result['categories'], list)

    def test_generate_documentation_completeness(self):
        """Test all entries are documented."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        assert result['total'] > 0
        assert len(result['functions']) == result['total']
        assert len(result['categories']) > 0

    def test_generate_documentation_includes_category(self):
        """Test each entry includes its category."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        for func_doc in result['functions']:
            assert 'category' in func_doc
            assert func_doc['category'] in result['categories']

    def test_surviving_globals_are_documented(self):
        """The three allowlisted globals + filters + context roots are present."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        names = [func['name'] for func in result['functions']]

        for name in ['path', 'icon', 'get_speed_profile', 'matches', 'default',
                     'form', 'request', 'generation', 'preset', 'runtime', 'paths']:
            assert name in names, f"Expected template surface '{name}' not documented"

    def test_deleted_globals_are_absent(self):
        """The removed render globals must NOT be documented as current syntax."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        names = {func['name'] for func in result['functions']}

        for name in ['get_form', 'value', 'get', 'setting', 'config',
                     'contains', 'get_is_in', 'dict', 'get_dict_value']:
            assert name not in names, f"Deleted global '{name}' still documented"

    def test_no_example_uses_deleted_syntax(self):
        """No example may show a deleted global, @object:/@dict:, or input.* context."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        forbidden = ["get_form(", "value(input", "setting(", "@object:", "@dict:", "input."]
        for func_doc in result['functions']:
            for example in func_doc['examples']:
                for token in forbidden:
                    assert token not in example['code'], (
                        f"Entry '{func_doc['name']}' example uses deleted syntax '{token}'"
                    )

    def test_path_function_documentation(self):
        """Test the 'path' function is properly documented."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        path_func = next((f for f in result['functions'] if f['name'] == 'path'), None)

        assert path_func is not None
        assert path_func['alias'] == 'get_path_for'
        assert 'path_type' in str(path_func['parameters'])
        assert len(path_func['examples']) > 0
        assert path_func['category'] == 'Path Helpers'

    def test_get_speed_profile_documentation(self):
        """Test the 'get_speed_profile' global is properly documented."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        gsp = next((f for f in result['functions'] if f['name'] == 'get_speed_profile'), None)

        assert gsp is not None
        assert gsp['category'] == 'Speed Profiles'
        assert 'profile_name' in str(gsp['parameters'])
        assert len(gsp['examples']) > 0

    def test_form_context_documentation(self):
        """Test the 'form' context root is documented under Template Context."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        form_ctx = next((f for f in result['functions'] if f['name'] == 'form'), None)

        assert form_ctx is not None
        assert form_ctx['category'] == 'Template Context'
        assert len(form_ctx['examples']) > 0

    def test_default_filter_documentation(self):
        """Test the builtin 'default' filter is documented (the only miss-suppressor)."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        default_filter = next((f for f in result['functions'] if f['name'] == 'default'), None)

        assert default_filter is not None
        assert default_filter['category'] == 'Filters'
        assert len(default_filter['examples']) > 0

    def test_all_functions_have_examples(self):
        """Test all entries have at least one example."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        for func_doc in result['functions']:
            assert len(func_doc['examples']) > 0, f"Entry {func_doc['name']} has no example"

    def test_all_functions_have_descriptions(self):
        """Test all entries have non-empty descriptions."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        for func_doc in result['functions']:
            assert func_doc['description']
            assert len(func_doc['description']) > 10, f"Entry {func_doc['name']} has too short a description"
