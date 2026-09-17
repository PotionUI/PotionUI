from unittest.mock import patch

import pytest

import backend.api as api
from backend.settings import OIDCSettings
from src.plugin_api.identity import DuplicateLoginProviderError


def _settings(label="SSO"):
    return OIDCSettings(
        issuer_url=None,
        client_id=None,
        client_secret=None,
        scopes="openid profile email",
        label=label,
        redirect_uri_override=None,
    )


def test_enable_registers_with_the_start_path_and_configured_label():
    calls = []

    def fake_register(provider_id, label, start_path):
        calls.append((provider_id, label, start_path))

    with patch.object(api, "register_login_provider", fake_register), patch.object(
        api, "load_settings", lambda: _settings(label="Company SSO")
    ):
        api.enable()

    assert calls == [(api.PLUGIN_ID, "Company SSO", api.START_PATH)]


def test_enable_registers_even_without_settings_configured():
    calls = []

    def fake_register(provider_id, label, start_path):
        calls.append((provider_id, label, start_path))

    with patch.object(api, "register_login_provider", fake_register), patch.object(
        api, "load_settings", lambda: _settings()
    ):
        api.enable()

    assert len(calls) == 1


def test_disable_unregisters_the_provider():
    calls = []

    def fake_unregister(provider_id):
        calls.append(provider_id)

    with patch.object(api, "unregister_login_provider", fake_unregister):
        api.disable()

    assert calls == [api.PLUGIN_ID]


def test_enable_twice_without_disable_raises_duplicate_error():
    registered = set()

    def fake_register(provider_id, label, start_path):
        if provider_id in registered:
            raise DuplicateLoginProviderError(f"already registered: {provider_id!r}")
        registered.add(provider_id)

    def fake_unregister(provider_id):
        registered.discard(provider_id)

    with patch.object(api, "register_login_provider", fake_register), patch.object(
        api, "unregister_login_provider", fake_unregister
    ), patch.object(api, "load_settings", lambda: _settings()):
        api.enable()
        with pytest.raises(DuplicateLoginProviderError):
            api.enable()
        api.disable()
