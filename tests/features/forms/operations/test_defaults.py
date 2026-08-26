"""Tests for src.features.forms.operations.defaults."""
from src.features.forms.operations.defaults import get_form_defaults


def test_get_form_defaults_success():
    result = get_form_defaults("test-preset-123")

    assert result == {}  # Currently returns empty defaults
