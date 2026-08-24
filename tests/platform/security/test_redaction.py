"""Key-name-based credential masking.

The rule is deliberately anchored at the end of the key. Both halves matter:
a miss leaks a credential, a false positive hides a real value from the admin
UI (and from the logs, where it is the only debugging signal there is).
"""

from __future__ import annotations

import pytest

from src.platform.security.redaction import (
    SECRET_MASK,
    is_secret_key,
    mask_secret_value,
    redact_mapping,
)


@pytest.mark.parametrize("key", [
    "auth_secret_key",
    "api_key",
    "civitai_api_key",
    "hf_api_key",
    "password",
    "user_password",
    "client_secret",
    "access_token",
    "refresh_token",
    "private_key",
    "passphrase",
    "credentials",
    "API_KEY",
    "  api_key  ",
    "token",
    "secret",
])
def test_credential_shaped_keys_are_recognized(key):
    assert is_secret_key(key) is True


@pytest.mark.parametrize("key", [
    # `token` appears, but these are not credentials - masking them would break
    # real settings and hide the numbers that make a log line useful.
    "max_tokens",
    "token_budget",
    "num_tokens",
    "tokenizer",
    "keyframes",
    "keep_alive",
    "models_dir",
    "nsfw_filter",
    "attention_mechanism",
    "tools_system_prompt",
    "",
])
def test_ordinary_keys_are_not_recognized(key):
    assert is_secret_key(key) is False


def test_a_set_credential_is_masked():
    assert mask_secret_value("auth_secret_key", "s3cr3t-jwt-key") == SECRET_MASK


def test_an_unset_credential_stays_distinguishable_from_a_set_one():
    """The admin UI tells "configured" from "not configured" by exactly this.

    Masking a blank would claim a credential exists where none does.
    """
    assert mask_secret_value("auth_secret_key", "") == ""
    assert mask_secret_value("auth_secret_key", None) is None


def test_non_string_values_pass_through():
    """A number or bool whose key happens to match a suffix is not a credential."""
    assert mask_secret_value("max_tokens", 4096) == 4096
    assert mask_secret_value("use_api_key", True) is True


def test_ordinary_value_is_untouched():
    assert mask_secret_value("models_dir", "/data/models") == "/data/models"


def test_redact_mapping_masks_nested_credentials():
    config = {
        "model": "flux-dev",
        "api_key": "sk-live-do-not-log",
        "backend_config": {
            "url": "https://comfy.example.test",
            "api_key": "sk-nested-do-not-log",
            "retries": 3,
        },
        "servers": [
            {"host": "a.example.test", "access_token": "tok-do-not-log"},
        ],
    }

    redacted = redact_mapping(config)

    dumped = repr(redacted)
    assert "sk-live-do-not-log" not in dumped
    assert "sk-nested-do-not-log" not in dumped
    assert "tok-do-not-log" not in dumped
    assert redacted["api_key"] == SECRET_MASK
    assert redacted["backend_config"]["api_key"] == SECRET_MASK
    assert redacted["servers"][0]["access_token"] == SECRET_MASK
    # Everything else survives - a scrubbed log line still has to be readable.
    assert redacted["model"] == "flux-dev"
    assert redacted["backend_config"]["url"] == "https://comfy.example.test"
    assert redacted["backend_config"]["retries"] == 3


def test_redact_mapping_does_not_mutate_its_input():
    """The config being logged is the config about to be handed to the pipe."""
    config = {"api_key": "sk-live", "nested": {"api_key": "sk-nested"}}

    redact_mapping(config)

    assert config["api_key"] == "sk-live"
    assert config["nested"]["api_key"] == "sk-nested"


def test_redact_mapping_leaves_empty_credentials_alone():
    assert redact_mapping({"api_key": ""}) == {"api_key": ""}
    assert redact_mapping({"api_key": None}) == {"api_key": None}
