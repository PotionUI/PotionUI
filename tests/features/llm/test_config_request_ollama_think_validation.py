"""LLMConfigRequest validation of provider_options.think for type == "ollama":
null/omitted (automatic), a real bool, or one of the named effort levels
Ollama accepts for models that support graded reasoning. No truthiness
coercion — a stray 1/"true" is rejected, not silently accepted as True."""

import pytest
from pydantic import ValidationError

from src.features.llm.dto import LLMConfigRequest


def _ollama_request(**provider_options) -> LLMConfigRequest:
    return LLMConfigRequest(
        name="n",
        type="ollama",
        enabled=True,
        base_url="http://localhost:11434",
        model="llama3",
        system_message="s",
        provider_options=provider_options,
    )


def test_think_omitted_is_valid():
    req = _ollama_request(keep_alive="5m")
    assert "think" not in req.provider_options


def test_think_none_is_valid():
    req = _ollama_request(think=None)
    assert req.provider_options["think"] is None


@pytest.mark.parametrize("value", [True, False])
def test_think_accepts_a_real_bool(value):
    req = _ollama_request(think=value)
    assert req.provider_options["think"] is value


@pytest.mark.parametrize("value", ["low", "medium", "high"])
def test_think_accepts_named_levels(value):
    req = _ollama_request(think=value)
    assert req.provider_options["think"] == value


@pytest.mark.parametrize("value", [1, 0, "true", "false", "LOW", "off", "on", 1.0, [], {}])
def test_think_rejects_malformed_or_truthiness_coerced_values(value):
    with pytest.raises(ValidationError, match="think"):
        _ollama_request(think=value)


def test_think_key_is_unvalidated_for_a_non_ollama_type():
    """provider_options.think is Ollama-specific; a native or other config
    leaves it as an unvalidated free-form key (meaningless there, so it is
    not this validator's business)."""
    req = LLMConfigRequest(
        name="n",
        type="native",
        enabled=True,
        base_url="",
        model="m",
        system_message="s",
        provider_options={"think": "not-a-real-value"},
    )
    assert req.provider_options["think"] == "not-a-real-value"
