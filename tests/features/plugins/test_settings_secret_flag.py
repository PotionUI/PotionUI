"""Which plugin settings count as credentials, and who changed them.

The manifest is the only authority on what is a secret: core does not guess from
key names and knows no plugin by name. Before this, every save through
update_plugin_settings forced ``is_secret=False``, so a manifest that declared
a credential got it stored - and returned - in the clear.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.plugins import operations
from src.features.plugins.operations import PluginManifestUnavailableError
from src.features.plugins.records import Plugin, PluginSetting
from src.platform.plugins.manifest import SettingSpec


@pytest.fixture
def repo():
    repository = Mock()
    repository.get_plugin_by_id.return_value = Plugin(
        id="acme-provider", name="Acme", version="1.0.0", type="backend-only",
        enabled=True, manifest_path="plugins/acme/manifest.yml",
    )
    repository.set_plugin_setting.side_effect = lambda **kwargs: PluginSetting(
        id=1,
        plugin_id=kwargs['plugin_id'],
        setting_key=kwargs['setting_key'],
        setting_value=kwargs['setting_value'],
        user_id=kwargs.get('user_id'),
        is_secret=kwargs.get('is_secret', False),
    )
    return repository


@pytest.fixture
def registry():
    manifest = Mock()
    manifest.settings = [
        SettingSpec(name="api_key", type="string", is_secret=True),
        SettingSpec(name="base_url", type="string"),
    ]
    reg = Mock()
    reg.get_plugin.return_value = manifest
    return reg


def test_manifest_declared_secret_is_stored_as_a_secret(repo, registry):
    operations.update_plugin_settings(repo, registry, "acme-provider", {"api_key": "sk-live-abc"})
    kwargs = repo.set_plugin_setting.call_args.kwargs
    assert kwargs['setting_key'] == "api_key"
    assert kwargs['is_secret'] is True


def test_ordinary_setting_is_not_flagged(repo, registry):
    operations.update_plugin_settings(repo, registry, "acme-provider", {"base_url": "https://example.test"})
    assert repo.set_plugin_setting.call_args.kwargs['is_secret'] is False


def test_a_save_without_a_manifest_is_refused_outright(repo, registry):
    """No manifest means no way to tell a credential from an ordinary setting.

    This used to fall through to is_secret=False, which stored a freshly typed
    credential in the clear AND unflagged - and unflagged is the worse half: the
    value is then handed back unmasked, and encrypt_declared_secrets keys off
    the flag, so nothing later finds it. Refusing costs the operator a reload;
    guessing costs them the key.
    """
    registry.get_plugin.return_value = None

    with pytest.raises(PluginManifestUnavailableError):
        operations.update_plugin_settings(repo, registry, "acme-provider", {"api_key": "sk-live-abc"})

    repo.set_plugin_setting.assert_not_called()


def test_a_refused_save_writes_nothing_at_all(repo, registry):
    """Not even the ordinary keys in the same batch - a half-applied settings
    form is how an operator concludes the save worked."""
    registry.get_plugin.return_value = None

    with pytest.raises(PluginManifestUnavailableError):
        operations.update_plugin_settings(
            repo, registry, "acme-provider",
            {"base_url": "https://example.test", "api_key": "sk-live-abc"},
        )

    repo.set_plugin_setting.assert_not_called()
    repo.record_setting_change.assert_not_called()


def test_the_refusal_message_never_contains_the_credential(repo, registry):
    registry.get_plugin.return_value = None

    with pytest.raises(PluginManifestUnavailableError) as exc_info:
        operations.update_plugin_settings(repo, registry, "acme-provider", {"api_key": "sk-live-abc"})

    assert "sk-live-abc" not in str(exc_info.value)


def test_a_manifest_declaring_no_settings_still_saves(repo, registry):
    """"Declares no secrets" is a real answer and must not be mistaken for
    "we could not find out" - a plugin with plain settings still saves."""
    manifest = Mock()
    manifest.settings = []
    registry.get_plugin.return_value = manifest

    operations.update_plugin_settings(repo, registry, "acme-provider", {"base_url": "https://example.test"})

    assert repo.set_plugin_setting.call_args.kwargs['is_secret'] is False


def test_secret_response_is_masked(repo, registry):
    responses = operations.update_plugin_settings(repo, registry, "acme-provider", {"api_key": "sk-live-abc"})
    assert responses[0].setting_value == "***"
    assert responses[0].is_secret is True


def test_audit_entry_records_the_actor_and_never_the_value(repo, registry):
    operations.update_plugin_settings(
        repo, registry, "acme-provider", {"api_key": "sk-live-abc"},
        actor_user_id="user-1", actor_username="admin",
    )
    kwargs = repo.record_setting_change.call_args.kwargs
    assert kwargs['plugin_id'] == "acme-provider"
    assert kwargs['setting_key'] == "api_key"
    assert kwargs['actor_user_id'] == "user-1"
    assert kwargs['actor_username'] == "admin"
    assert kwargs['action'] == "set"
    assert kwargs['is_secret'] is True
    assert "sk-live-abc" not in repr(kwargs)


def test_every_updated_key_is_audited(repo, registry):
    operations.update_plugin_settings(
        repo, registry, "acme-provider", {"api_key": "sk-live-abc", "base_url": "https://example.test"},
    )
    audited = {c.kwargs['setting_key'] for c in repo.record_setting_change.call_args_list}
    assert audited == {"api_key", "base_url"}


def test_encrypt_declared_secrets_tolerates_a_missing_manifest(repo, registry):
    """The startup promotion pass walks every DB plugin, including ones whose
    manifest is gone. It only ever *adds* a flag a manifest asks for, so "no
    manifest" is nothing to do here - not a reason to fail boot."""
    repo.get_all_plugins.return_value = [
        Plugin(
            id="ghost", name="Ghost", version="1.0.0", type="backend-only",
            enabled=True, manifest_path="plugins/ghost/manifest.yml",
        )
    ]
    registry.get_plugin.return_value = None

    assert operations.encrypt_declared_secrets(repo, registry) == 0
