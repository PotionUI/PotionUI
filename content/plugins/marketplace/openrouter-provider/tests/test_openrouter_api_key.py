import pytest

from backend.config import OpenRouterConfig

from .fixtures import KEY

MESSAGE = "Add your OpenRouter API key."


def settings(**overrides):
    return {"id": "or-1", "name": "OpenRouter", "engine": "cloud", "driver": "cloud.openrouter", **overrides}


def test_the_key_is_a_required_secret_in_the_form_schema():
    spec = next(field for field in OpenRouterConfig.engine_fields() if field["name"] == "api_key")

    assert spec["required"] is True
    assert spec["secret"] is True


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_config_without_a_key_is_refused_in_plain_words(key):
    data = settings()
    if key is not None:
        data["api_key"] = key

    with pytest.raises(ValueError, match=MESSAGE):
        OpenRouterConfig(**data)


def test_a_valid_key_is_accepted_and_stripped():
    assert OpenRouterConfig(**settings(api_key=f"  {KEY}  ")).api_key == KEY
