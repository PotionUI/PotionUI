"""LLMConfigRequest validation of the OpenAI-only provider_options knobs:
seed, reasoning_effort, parallel_tool_calls, stop. Checked only for
type == "openai"; every other provider type leaves these free-form."""

import pytest
from pydantic import ValidationError

from src.features.llm.dto import LLMConfigRequest


def _request(config_type="openai", **provider_options) -> LLMConfigRequest:
    return LLMConfigRequest(
        name="n",
        type=config_type,
        enabled=True,
        base_url="",
        model="m",
        system_message="s",
        provider_options=provider_options,
    )


@pytest.mark.parametrize("value", [0, 1, -1, 12345])
def test_seed_accepts_integers(value):
    req = _request(seed=value)
    assert req.provider_options["seed"] == value


@pytest.mark.parametrize("value", [1.5, "42", True, False])
def test_seed_rejects_malformed_values(value):
    with pytest.raises(ValidationError, match="seed"):
        _request(seed=value)


def test_seed_none_is_valid():
    req = _request(seed=None)
    assert req.provider_options["seed"] is None


@pytest.mark.parametrize("value", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_reasoning_effort_accepts_named_levels(value):
    req = _request(reasoning_effort=value)
    assert req.provider_options["reasoning_effort"] == value


@pytest.mark.parametrize("value", ["ultra", "", 1, True])
def test_reasoning_effort_rejects_malformed_values(value):
    with pytest.raises(ValidationError, match="reasoning_effort"):
        _request(reasoning_effort=value)


def test_reasoning_effort_none_is_valid():
    req = _request(reasoning_effort=None)
    assert req.provider_options["reasoning_effort"] is None


@pytest.mark.parametrize("value", [True, False])
def test_parallel_tool_calls_accepts_bool(value):
    req = _request(parallel_tool_calls=value)
    assert req.provider_options["parallel_tool_calls"] == value


@pytest.mark.parametrize("value", ["true", 1, 0])
def test_parallel_tool_calls_rejects_malformed_values(value):
    with pytest.raises(ValidationError, match="parallel_tool_calls"):
        _request(parallel_tool_calls=value)


def test_parallel_tool_calls_none_is_valid():
    req = _request(parallel_tool_calls=None)
    assert req.provider_options["parallel_tool_calls"] is None


@pytest.mark.parametrize("value", ["\n\n", ["END"], ["a", "b", "c", "d"]])
def test_stop_accepts_string_or_up_to_four_strings(value):
    req = _request(stop=value)
    assert req.provider_options["stop"] == value


@pytest.mark.parametrize("value", [[], ["a", "b", "c", "d", "e"], [1, 2], 5])
def test_stop_rejects_malformed_values(value):
    with pytest.raises(ValidationError, match="stop"):
        _request(stop=value)


def test_stop_none_is_valid():
    req = _request(stop=None)
    assert req.provider_options["stop"] is None


def test_openai_validators_are_skipped_for_other_provider_types():
    req = _request(config_type="ollama", seed="not-an-int", reasoning_effort="ultra", parallel_tool_calls="true", stop=5)
    assert req.provider_options == {
        "seed": "not-an-int",
        "reasoning_effort": "ultra",
        "parallel_tool_calls": "true",
        "stop": 5,
    }
