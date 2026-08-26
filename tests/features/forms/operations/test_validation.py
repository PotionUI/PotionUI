"""Tests for src.features.forms.operations.validation."""
import pytest
from unittest.mock import Mock

from src.features.forms.operations.validation import validate_form_data, _validate_field_type
from src.platform.plugins.hooks import HookContext


@pytest.fixture
def plugin_registry():
    """Mock PluginRegistry with default (unblocked, pass-through) hook behavior."""
    mock = Mock()

    def execute_hook_side_effect(hook_name, initial_data=None):
        context = HookContext(hook_name=hook_name, plugin_id="system", data=initial_data or {})
        return context, []
    mock.execute_hook.side_effect = execute_hook_side_effect
    return mock


class TestValidateFormData:

    def test_success(self, plugin_registry):
        form_schema = {
            "required": ["prompt", "steps"],
            "properties": {
                "prompt": {"type": "string"},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                "cfg": {"type": "number", "minimum": 0.1, "maximum": 20.0}
            }
        }
        form_data = {"prompt": "A beautiful landscape", "steps": 20, "cfg": 7.5}

        result = validate_form_data(plugin_registry, form_schema, form_data)

        assert result["valid"] is True

    def test_missing_required(self, plugin_registry):
        form_schema = {
            "required": ["prompt", "steps"],
            "properties": {"prompt": {"type": "string"}, "steps": {"type": "integer"}}
        }
        form_data = {"prompt": "A beautiful landscape"}

        with pytest.raises(ValueError) as exc_info:
            validate_form_data(plugin_registry, form_schema, form_data)

        assert "steps" in str(exc_info.value)
        assert "required" in str(exc_info.value)

    def test_invalid_type(self, plugin_registry):
        form_schema = {"required": ["prompt"], "properties": {"prompt": {"type": "string"}}}
        form_data = {"prompt": 123}  # Should be string

        with pytest.raises(ValueError) as exc_info:
            validate_form_data(plugin_registry, form_schema, form_data)

        assert "prompt" in str(exc_info.value)
        assert "invalid type" in str(exc_info.value)

    def test_range_validation(self, plugin_registry):
        form_schema = {"required": ["steps"], "properties": {"steps": {"type": "integer", "minimum": 1, "maximum": 100}}}
        form_data = {"steps": 150}  # Maximum is 100

        with pytest.raises(ValueError) as exc_info:
            validate_form_data(plugin_registry, form_schema, form_data)

        assert "steps" in str(exc_info.value)
        assert "at most 100" in str(exc_info.value)

    def test_hook_blocked(self, plugin_registry):
        def blocked_hook(hook_name, initial_data=None):
            context = HookContext(
                hook_name=hook_name, plugin_id="system",
                data={"blocked": True, "block_reason": "Validation blocked by plugin"},
            )
            return context, []
        plugin_registry.execute_hook.side_effect = blocked_hook

        with pytest.raises(ValueError) as exc_info:
            validate_form_data(plugin_registry, {}, {})

        assert "Validation blocked by plugin" in str(exc_info.value)


class TestValidateFieldType:

    def test_string(self):
        assert _validate_field_type("hello", "string") is True
        assert _validate_field_type(123, "string") is False

    def test_number(self):
        assert _validate_field_type(123, "number") is True
        assert _validate_field_type(123.45, "number") is True
        assert _validate_field_type("123", "number") is False

    def test_integer(self):
        assert _validate_field_type(123, "integer") is True
        assert _validate_field_type(123.45, "integer") is False

    def test_boolean(self):
        assert _validate_field_type(True, "boolean") is True
        assert _validate_field_type(False, "boolean") is True
        assert _validate_field_type("true", "boolean") is False

    def test_array(self):
        assert _validate_field_type([1, 2, 3], "array") is True
        assert _validate_field_type([], "array") is True
        assert _validate_field_type("not array", "array") is False

    def test_object(self):
        assert _validate_field_type({"key": "value"}, "object") is True
        assert _validate_field_type({}, "object") is True
        assert _validate_field_type([1, 2, 3], "object") is False

    def test_unknown(self):
        assert _validate_field_type("anything", "unknown_type") is True
