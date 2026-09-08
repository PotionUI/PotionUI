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

        # The categories reflect the ACTUAL post-rework surface: the one
        # allowlisted global, filters, and the template context roots. The
        # deleted globals' old categories ("Value Access", "Form Access",
        # "Settings", "Conditionals", "Dictionary Access", "Path Helpers",
        # "Icon Mapping" - neither path/icon ever had a real preset consumer)
        # must NOT reappear.
        expected_categories = ["Speed Profiles", "Filters", "Template Context"]
        for category in expected_categories:
            assert category in categories

        deleted_categories = [
            "Value Access", "Form Access", "Settings",
            "Conditionals", "Dictionary Access",
            "Path Helpers", "Icon Mapping",
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
        """The one allowlisted global + filters + context roots are present."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        names = [func['name'] for func in result['functions']]

        for name in ['get_speed_profile', 'active_loras',
                     'strip_model_dir', 'default',
                     'form', 'request', 'generation', 'preset', 'runtime', 'paths']:
            assert name in names, f"Expected template surface '{name}' not documented"

    def test_deleted_globals_are_absent(self):
        """The removed render globals/filters must NOT be documented as
        current syntax - path/icon never had a real preset consumer, and
        neither did the matches/regex_search filter."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        names = {func['name'] for func in result['functions']}

        for name in ['get_form', 'value', 'get', 'setting', 'config',
                     'contains', 'get_is_in', 'dict', 'get_dict_value',
                     'path', 'get_path_for', 'icon', 'get_icon',
                     'matches', 'regex_search']:
            assert name not in names, f"Deleted global/filter '{name}' still documented"

    def test_no_example_uses_deleted_syntax(self):
        """No example may show a deleted global/filter, @object:/@dict:,
        input.* context, or the removed preset.speed_profiles/
        preset.configuration/generation.seed/generation.quantity roots."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        forbidden = [
            "get_form(", "value(input", "setting(", "@object:", "@dict:", "input.",
            "path(", "get_path_for(", "icon(", "get_icon(", "matches(", "regex_search(",
            "preset.speed_profiles", "preset.configuration",
            "generation.seed", "generation.quantity",
        ]
        for func_doc in result['functions']:
            for example in func_doc['examples']:
                for token in forbidden:
                    assert token not in example['code'], (
                        f"Entry '{func_doc['name']}' example uses deleted syntax '{token}'"
                    )

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

    def test_generation_context_documents_profile_not_seed_or_quantity(self):
        """generation.profile replaces generation.seed/generation.quantity
        (dead aliases for form.seed/form.quantity - see docs/presets.md)."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        generation_ctx = next((f for f in result['functions'] if f['name'] == 'generation'), None)

        assert generation_ctx is not None
        param_names = {p['name'] for p in generation_ctx['parameters']}
        assert 'profile' in param_names
        assert 'seed' not in param_names
        assert 'quantity' not in param_names

    def test_preset_context_no_longer_documents_speed_profiles_or_configuration(self):
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        preset_ctx = next((f for f in result['functions'] if f['name'] == 'preset'), None)

        assert preset_ctx is not None
        param_names = {p['name'] for p in preset_ctx['parameters']}
        assert 'speed_profiles' not in param_names
        assert 'configuration' not in param_names

    def test_strip_model_dir_filter_documentation(self):
        """Test the 'strip_model_dir' filter is properly documented."""
        documenter = TemplateFunctionsDocumenter()
        result = documenter.generate_documentation()

        strip_filter = next((f for f in result['functions'] if f['name'] == 'strip_model_dir'), None)

        assert strip_filter is not None
        assert strip_filter['category'] == 'Filters'
        assert len(strip_filter['examples']) > 0

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
