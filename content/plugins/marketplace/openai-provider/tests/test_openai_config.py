import pytest

from backend.config import OpenAIConfig
from backend.provider import OpenAIProvider

from .fixtures import KEY


def settings(**overrides):
    return {"id": "oa-1", "name": "OpenAI", "engine": "cloud", "driver": "cloud.openai", **overrides}


def test_the_key_is_a_required_secret_and_the_driver_is_fixed():
    spec = next(field for field in OpenAIConfig.engine_fields() if field["name"] == "api_key")

    assert spec["required"] is True and spec["secret"] is True
    assert OpenAIConfig.secret_field_names() == frozenset({"api_key"})
    assert OpenAIProvider.key == "openai" and OpenAIConfig.model_fields["driver"].default == "cloud.openai"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_config_without_a_key_is_refused_in_plain_words(key):
    data = settings()
    if key is not None:
        data["api_key"] = key

    with pytest.raises(ValueError, match="Add your OpenAI API key."):
        OpenAIConfig(**data)


def test_the_defaults_send_nothing_extra():
    config = OpenAIConfig(**settings(api_key=f"  {KEY}  "))

    assert config.api_key == KEY
    assert config.send_user_hash is False and config.moderation == "auto"
    assert config.organization == "" and config.project == ""
    assert OpenAIProvider.auth_headers(config) == {"Authorization": f"Bearer {KEY}"}
    assert OpenAIProvider.api_base_url(config) == "https://api.openai.com/v1"


def test_the_content_filter_offers_only_openais_levels():
    spec = next(field for field in OpenAIConfig.engine_fields() if field["name"] == "moderation")

    assert spec["options"] == ["auto", "low"]
    assert OpenAIConfig(**settings(api_key=KEY, moderation=" LOW ")).moderation == "low"
    with pytest.raises(ValueError):
        OpenAIConfig(**settings(api_key=KEY, moderation="off"))


@pytest.mark.parametrize("value", ["org abc", "org-abc\r\nX-Evil: 1", "proj/123", "örg"])
def test_an_organization_or_project_that_is_not_a_plain_id_is_refused(value):
    for field in ("organization", "project"):
        with pytest.raises(ValueError):
            OpenAIConfig(**settings(api_key=KEY, **{field: value}))


def test_plain_ids_are_kept_trimmed():
    config = OpenAIConfig(**settings(api_key=KEY, organization=" org-AbC_1 ", project="proj_9"))

    assert (config.organization, config.project) == ("org-AbC_1", "proj_9")
