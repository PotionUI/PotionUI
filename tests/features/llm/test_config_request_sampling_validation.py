"""LLMConfigRequest validation of the shared top_k/top_p/min_p/repetition_penalty
sampling knobs in provider_options. Malformed values fail loudly at the DTO
boundary rather than being truncated or substituted downstream; omitted/null
stays valid — that's how a config leaves a knob to the provider/model."""

import pytest
from pydantic import ValidationError

from src.features.llm.dto import LLMConfigRequest


def _request(**provider_options) -> LLMConfigRequest:
    return LLMConfigRequest(
        name="n",
        type="native",
        enabled=True,
        base_url="",
        model="m",
        system_message="s",
        provider_options=provider_options,
    )


def test_no_provider_options_is_valid():
    req = LLMConfigRequest(
        name="n", type="native", enabled=True, base_url="", model="m", system_message="s"
    )
    assert req.provider_options is None


@pytest.mark.parametrize("value", [0, 1, 40, 200])
def test_top_k_accepts_non_negative_integers(value):
    req = _request(top_k=value)
    assert req.provider_options["top_k"] == value


@pytest.mark.parametrize("value", [-1, 3.5, "40", True, False, float("nan"), float("inf")])
def test_top_k_rejects_malformed_values(value):
    with pytest.raises(ValidationError, match="top_k"):
        _request(top_k=value)


def test_top_k_none_is_valid():
    req = _request(top_k=None)
    assert req.provider_options["top_k"] is None


@pytest.mark.parametrize("key", ["top_p", "min_p"])
@pytest.mark.parametrize("value", [0.0, 0.5, 1.0, 0])
def test_probability_keys_accept_the_0_to_1_domain(key, value):
    req = _request(**{key: value})
    assert req.provider_options[key] == value


@pytest.mark.parametrize("key", ["top_p", "min_p"])
@pytest.mark.parametrize("value", [-0.1, 1.1, "0.5", True, False, float("nan"), float("inf")])
def test_probability_keys_reject_out_of_domain_or_malformed_values(key, value):
    with pytest.raises(ValidationError, match=key):
        _request(**{key: value})


@pytest.mark.parametrize("key", ["top_p", "min_p"])
def test_probability_keys_none_is_valid(key):
    req = _request(**{key: None})
    assert req.provider_options[key] is None


@pytest.mark.parametrize("value", [0.01, 1.0, 1.1, 2, 5])
def test_repetition_penalty_accepts_positive_numbers(value):
    req = _request(repetition_penalty=value)
    assert req.provider_options["repetition_penalty"] == value


@pytest.mark.parametrize("value", [0, -1, "1.1", True, False, float("nan"), float("inf")])
def test_repetition_penalty_rejects_non_positive_or_malformed_values(value):
    with pytest.raises(ValidationError, match="repetition_penalty"):
        _request(repetition_penalty=value)


def test_repetition_penalty_none_is_valid():
    req = _request(repetition_penalty=None)
    assert req.provider_options["repetition_penalty"] is None


def test_unrelated_provider_options_keys_are_untouched():
    req = _request(keep_alive="5m", thinking=True, top_k=10)
    assert req.provider_options == {"keep_alive": "5m", "thinking": True, "top_k": 10}
