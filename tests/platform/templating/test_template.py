"""
Tests for TemplateProcessor - the native expression evaluator.

Covers: exact-expression -> native type, mixed-text -> string, multiline
preservation, StrictUndefined -> TemplateEvaluationError, sandbox denial
(attribute escapes + mutating methods), get_speed_profile, deleted globals
actually gone, non-string passthrough, dict/list recursion, and the `matches`
filter (unaffected by the rework).
"""

import pytest
from jinja2.exceptions import UndefinedError, SecurityError, TemplateSyntaxError

from src.platform.templating import TemplateProcessor, TemplateEvaluationError


@pytest.fixture
def processor(mock_settings_manager):
    """TemplateProcessor instance for testing."""
    return TemplateProcessor(mock_settings_manager)


class TestExactExpressionTyping:
    """A scalar that is exactly one `{{ expression }}` evaluates to its native type."""

    def test_int(self, processor):
        assert processor.process_template("{{ 1 + 1 }}", {}) == 2
        assert type(processor.process_template("{{ 1 + 1 }}", {})) is int

    def test_float(self, processor):
        result = processor.process_template("{{ 7.5 }}", {})
        assert result == 7.5
        assert type(result) is float

    def test_bool(self, processor):
        assert processor.process_template("{{ true }}", {}) is True
        assert processor.process_template("{{ form.steps == 8 }}", {"form": {"steps": 8}}) is True
        assert processor.process_template("{{ form.steps == 8 }}", {"form": {"steps": 4}}) is False

    def test_list(self, processor):
        result = processor.process_template("{{ [1, 2, 3] }}", {})
        assert result == [1, 2, 3]
        assert type(result) is list

    def test_dict(self, processor):
        result = processor.process_template("{{ {'steps': 6, 'guidance': 1.0} }}", {})
        assert result == {"steps": 6, "guidance": 1.0}
        assert type(result) is dict

    def test_none(self, processor):
        assert processor.process_template("{{ none }}", {}) is None

    def test_native_value_passthrough(self, processor):
        """A native object flowing straight through an exact expression (e.g. a
        form field already holding a list) keeps its identity/type, not just
        an equal-looking string rendering."""
        loras = [{"name": "a", "weight": 0.8}, {"name": "b", "weight": 0.5}]
        result = processor.process_template("{{ form.loras }}", {"form": {"loras": loras}})
        assert result == loras
        assert result is loras

    def test_surrounding_whitespace_is_still_exact(self, processor):
        assert processor.process_template("   {{ 1 + 1 }}  \n", {}) == 2

    def test_default_filter_on_missing(self, processor):
        assert processor.process_template("{{ form.missing | default(5) }}", {"form": {}}) == 5
        assert processor.process_template("{{ form.missing | default([]) }}", {"form": {}}) == []


class TestStringTemplatePath:
    """Mixed text / multiple blocks / statements render as strings."""

    def test_mixed_text_stays_string(self, processor):
        result = processor.process_template("Hello {{ name }}", {"name": "World"})
        assert result == "Hello World"
        assert isinstance(result, str)

    def test_multiple_blocks_is_string_template(self, processor):
        result = processor.process_template("{{ a }}-{{ b }}", {"a": 1, "b": 2})
        assert result == "1-2"

    def test_statement_block_is_string_template(self, processor):
        template = "{% if flag %}yes{% else %}no{% endif %}"
        assert processor.process_template(template, {"flag": True}) == "yes"
        assert processor.process_template(template, {"flag": False}) == "no"

    def test_multiline_output_preserves_newlines(self, processor):
        template = "Line 1: {{ a }}\nLine 2: {{ b }}\n"
        result = processor.process_template(template, {"a": "x", "b": "y"})
        assert result == "Line 1: x\nLine 2: y\n"

    def test_plain_string_no_template_syntax(self, processor):
        assert processor.process_template("just plain text", {}) == "just plain text"


class TestStrictUndefinedErrors:
    """Missing values raise TemplateEvaluationError; only `| default(...)` suppresses it."""

    def test_missing_variable_in_exact_expression_raises(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ missing_var }}", {})
        err = exc_info.value
        assert err.expression == "missing_var"
        assert isinstance(err.cause, UndefinedError)
        assert "missing_var" in str(err)

    def test_missing_attribute_access_raises(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ form.missing.nested }}", {"form": {}})
        assert isinstance(exc_info.value.cause, UndefinedError)

    def test_missing_variable_in_string_template_raises(self, processor):
        with pytest.raises(TemplateEvaluationError):
            processor.process_template("Hello {{ missing_var }}", {})

    def test_syntax_error_raises_template_evaluation_error(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ 1 +++ }}", {})
        assert isinstance(exc_info.value.cause, TemplateSyntaxError)

    def test_default_filter_suppresses_the_error(self, processor):
        # Sanity check that StrictUndefined + | default(...) is the *only* suppression path.
        assert processor.process_template("{{ missing | default('fallback') }}", {}) == "fallback"


class TestSandboxDenial:
    """SandboxedEnvironment blocks attribute escapes and mutating methods."""

    def test_dunder_class_chain_blocked(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template(
                "{{ ().__class__.__bases__[0].__subclasses__() }}", {}
            )
        assert isinstance(exc_info.value.cause, SecurityError)

    def test_subclasses_escape_blocked(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template(
                "{{ [].__class__.__base__.__subclasses__() }}", {}
            )
        assert isinstance(exc_info.value.cause, SecurityError)

    def test_import_blocked(self, processor):
        with pytest.raises(TemplateEvaluationError):
            processor.process_template("{{ __import__('os') }}", {})

    def test_list_mutation_blocked(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ items.append(1) }}", {"items": []})
        assert isinstance(exc_info.value.cause, SecurityError)

    def test_dict_mutation_blocked(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ d.update({'x': 1}) }}", {"d": {}})
        assert isinstance(exc_info.value.cause, SecurityError)


class TestGetSpeedProfile:
    """get_speed_profile stays available (ergonomic, typed lookup)."""

    def test_known_profile(self, processor):
        context = {"preset": {"speed_profiles": {"draft": {"steps": 6}}}}
        result = processor.process_template("{{ get_speed_profile('draft') }}", context)
        assert result == {"steps": 6}

    def test_missing_profile_without_default_raises(self, processor):
        context = {
            "preset": {"speed_profiles": {"draft": {"steps": 6}}, "name": "Test Preset"},
        }
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ get_speed_profile('turbo') }}", context)
        assert isinstance(exc_info.value.cause, ValueError)
        assert "Test Preset" in str(exc_info.value.cause)
        assert "turbo" in str(exc_info.value.cause)

    def test_missing_profile_with_explicit_default(self, processor):
        context = {"preset": {"speed_profiles": {}}}
        result = processor.process_template("{{ get_speed_profile('turbo', {}) }}", context)
        assert result == {}


class TestDeletedGlobalsAreGone:
    """get_form, value/get, contains/get_is_in, setting/config no longer exist."""

    @pytest.mark.parametrize("template", [
        "{{ get_form('custom', ['x']) }}",
        "{{ value(form, 'x') }}",
        "{{ get(form, 'x') }}",
        "{{ contains(form, 'x', ['a']) }}",
        "{{ get_is_in(form, 'x', ['a']) }}",
        "{{ setting('SYSTEM', 'file_storage_directory') }}",
        "{{ config('SYSTEM', 'file_storage_directory') }}",
    ])
    def test_deleted_global_raises(self, processor, template):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template(template, {"form": {"x": 1}})
        assert isinstance(exc_info.value.cause, UndefinedError)

    def test_dict_global_no_longer_our_custom_helper(self, processor):
        """`dict` isn't registered by us anymore; Jinja's own builtin `dict()`
        constructor (unrelated to the old path-lookup helper) still exists as
        one of Jinja's default globals, so this fails on argument shape
        (TypeError), not as an undefined name - either way it's no longer our
        `get_dict_value` path-lookup semantics."""
        with pytest.raises(TemplateEvaluationError):
            processor.process_template("{{ dict(form, 'x') }}", {"form": {"x": 1}})


class TestNonStringPassthroughAndRecursion:
    """Non-string scalars pass through untouched; dict/list configs recurse."""

    def test_int_passthrough(self, processor):
        assert processor.process_template(5, {}) == 5

    def test_bool_passthrough(self, processor):
        assert processor.process_template(True, {}) is True

    def test_none_passthrough(self, processor):
        assert processor.process_template(None, {}) is None

    def test_dict_recursion(self, processor):
        config = {"steps": "{{ 1 + 1 }}", "label": "n={{ n }}", "flag": True}
        result = processor.process_template(config, {"n": 3})
        assert result == {"steps": 2, "label": "n=3", "flag": True}

    def test_list_recursion(self, processor):
        config = ["{{ 1 + 1 }}", "plain", 3]
        result = processor.process_template(config, {})
        assert result == [2, "plain", 3]

    def test_nested_dict_list_recursion(self, processor):
        config = {
            "pipes": [
                {"name": "generator", "cfg": "{{ form.cfg }}"},
                {"name": "upscaler", "enabled": "{{ form.enabled }}"},
            ]
        }
        result = processor.process_template(config, {"form": {"cfg": 7.5, "enabled": False}})
        assert result == {
            "pipes": [
                {"name": "generator", "cfg": 7.5},
                {"name": "upscaler", "enabled": False},
            ]
        }


class TestPathAndIconGlobalsStillWork:
    """path/get_path_for and icon/get_icon stay allowlisted globals."""

    def test_path_function(self, processor):
        result = processor.process_template("{{ path('lora', 'style.safetensors') }}", {})
        assert result == "models/loras/style.safetensors"

    def test_get_path_for_alias(self, processor):
        result = processor.process_template("{{ get_path_for('checkpoint') }}", {})
        assert result == "models/checkpoints"

    def test_icon_function(self, processor):
        assert processor.process_template("{{ icon('prompt') }}", {}) == "pencil-square"

    def test_get_icon_alias(self, processor):
        assert processor.process_template("{{ get_icon('lora') }}", {}) == "puzzle-piece"

    def test_invalid_path_type_raises(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.process_template("{{ path('invalid_type') }}", {})
        assert isinstance(exc_info.value.cause, ValueError)


class TestMatchesFilter:
    """The `matches`/`regex_search` filter is unaffected by the rework."""

    def test_matches_filter(self, processor):
        result = processor.process_template(
            "{{ email | matches('@example\\.com$') }}", {"email": "user@example.com"}
        )
        assert result is True

    def test_regex_search_alias(self, processor):
        result = processor.process_template(
            "{{ email | regex_search('@example\\.com$') }}", {"email": "user@example.com"}
        )
        assert result is True


class TestActiveLorasFilter:
    """The `active_loras` filter, as preset `@loop` items expressions use it."""

    def test_drops_zero_strength_keeps_the_rest(self, processor):
        result = processor.process_template(
            "{{ form.loras | default([]) | active_loras }}",
            {
                "form": {
                    "loras": [
                        {"model": "keep.safetensors", "strength": 0.9},
                        {"model": "zero.safetensors", "strength": 0},
                        {"model": "inverted.safetensors", "strength": -0.4},
                        {"model": "unset.safetensors"},
                    ]
                }
            },
        )
        assert result == [
            {"model": "keep.safetensors", "strength": 0.9},
            {"model": "inverted.safetensors", "strength": -0.4},
            {"model": "unset.safetensors"},
        ]

    def test_undefined_form_field_via_default(self, processor):
        result = processor.process_template(
            "{{ form.loras | default([]) | active_loras }}", {"form": {}}
        )
        assert result == []


class TestEvaluateExpression:
    """evaluate_expression: the direct hook `@loop` items will call."""

    def test_evaluates_exact_expression(self, processor):
        result = processor.evaluate_expression("{{ [1, 2, 3] }}", {})
        assert result == [1, 2, 3]

    def test_evaluates_dict(self, processor):
        result = processor.evaluate_expression("{{ form.loras }}", {"form": {"loras": [1, 2]}})
        assert result == [1, 2]

    def test_raises_if_not_exact_expression(self, processor):
        with pytest.raises(TemplateEvaluationError):
            processor.evaluate_expression("not an expression at all", {})

    def test_raises_if_mixed_text(self, processor):
        with pytest.raises(TemplateEvaluationError):
            processor.evaluate_expression("prefix {{ [1, 2] }}", {})

    def test_raises_on_missing_variable(self, processor):
        with pytest.raises(TemplateEvaluationError) as exc_info:
            processor.evaluate_expression("{{ missing }}", {})
        assert isinstance(exc_info.value.cause, UndefinedError)


class TestPathResolverAndIconMapperDirect:
    """Direct (non-template) method access, unaffected by the rework."""

    def test_get_path_for_all_types(self, processor):
        expected_paths = {
            "checkpoint": "models/checkpoints",
            "lora": "models/loras",
            "embedding": "models/embeddings",
            "upscaler": "models/upscalers",
            "detector": "models/detectors",
            "wildcard": "models/wildcards",
            "diffusion_model": "models/diffusion_models",
            "controlnet": "models/controlnet",
            "std": "src/std",
        }
        for path_type, expected_base in expected_paths.items():
            assert processor.get_path_for(path_type) == expected_base

    def test_get_path_for_with_filename(self, processor):
        assert processor.get_path_for("embedding", "negative.pt") == "models/embeddings/negative.pt"

    def test_get_path_for_invalid_type(self, processor):
        with pytest.raises(ValueError, match="Unsupported path type: invalid_type"):
            processor.get_path_for("invalid_type")

    def test_get_icon_predefined_and_custom(self, processor):
        assert processor.get_icon("prompt") == "pencil-square"
        assert processor.get_icon("PROMPT") == "pencil-square"
        assert processor.get_icon("custom-icon-name") == "custom-icon-name"

    def test_regex_search_direct(self, processor):
        assert processor.regex_search("hello world", r"wor\w+") is True
        assert processor.regex_search("hello world", r"\d+") is False
