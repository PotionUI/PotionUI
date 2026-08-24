"""The cipher itself: envelope, key resolution, tamper detection, rotation."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from src.platform.security.secrets import (
    ENVELOPE_PREFIX,
    SecretCipher,
    SecretDecryptionError,
    SecretKeyError,
    default_key_path,
    generate_key,
    read_key_file,
    resolve_secret_keys,
    write_key_file,
)


@pytest.fixture
def cipher():
    return SecretCipher([generate_key()])


def test_roundtrip(cipher):
    assert cipher.decrypt(cipher.encrypt("sk-live-abc"), context="t") == "sk-live-abc"


def test_ciphertext_does_not_contain_the_plaintext(cipher):
    token = cipher.encrypt("sk-live-abc")
    assert "sk-live-abc" not in token
    assert token.startswith(ENVELOPE_PREFIX)


def test_encryption_is_randomised(cipher):
    """Two encryptions of one value differ - no deterministic/ECB-style leak."""
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_is_encrypted_recognises_only_envelopes(cipher):
    assert SecretCipher.is_encrypted(cipher.encrypt("x"))
    assert not SecretCipher.is_encrypted("plain-value")
    assert not SecretCipher.is_encrypted(None)
    assert not SecretCipher.is_encrypted(42)


def test_wrong_key_raises_rather_than_returning_empty():
    written = SecretCipher([generate_key()])
    other = SecretCipher([generate_key()])
    token = written.encrypt("sk-live-abc")
    with pytest.raises(SecretDecryptionError):
        other.decrypt(token, context="plugin_settings:p/api_key")


def test_decrypt_error_names_the_location_not_the_value():
    written = SecretCipher([generate_key()])
    other = SecretCipher([generate_key()])
    token = written.encrypt("sk-live-abc")
    with pytest.raises(SecretDecryptionError) as excinfo:
        other.decrypt(token, context="plugin_settings:p/api_key")
    message = str(excinfo.value)
    assert "plugin_settings:p/api_key" in message
    assert "sk-live-abc" not in message


def test_tampered_ciphertext_is_detected(cipher):
    token = cipher.encrypt("sk-live-abc")
    body = list(token[len(ENVELOPE_PREFIX):])
    # Flip a character in the middle of the token, keeping it base64-legal.
    index = len(body) // 2
    body[index] = "A" if body[index] != "A" else "B"
    tampered = ENVELOPE_PREFIX + "".join(body)
    with pytest.raises(SecretDecryptionError):
        cipher.decrypt(tampered, context="t")


def test_truncated_ciphertext_is_detected(cipher):
    token = cipher.encrypt("sk-live-abc")
    with pytest.raises(SecretDecryptionError):
        cipher.decrypt(token[:-8], context="t")


def test_decrypt_if_encrypted_passes_plaintext_through(cipher):
    assert cipher.decrypt_if_encrypted("legacy-plaintext", context="t") == "legacy-plaintext"
    assert cipher.decrypt_if_encrypted(None, context="t") is None


def test_encrypt_is_idempotent(cipher):
    once = cipher.encrypt("v")
    assert cipher.encrypt(once) == once


def test_retired_key_still_decrypts():
    old, new = generate_key(), generate_key()
    token = SecretCipher([old]).encrypt("sk-live-abc")
    keyring = SecretCipher([new, old])
    assert keyring.decrypt(token, context="t") == "sk-live-abc"


def test_rotate_reencrypts_under_the_primary_key():
    old, new = generate_key(), generate_key()
    token = SecretCipher([old]).encrypt("sk-live-abc")
    rotated = SecretCipher([new, old]).rotate(token, context="t")
    assert rotated != token
    assert SecretCipher([new]).decrypt(rotated, context="t") == "sk-live-abc"


def test_rotate_refuses_a_value_it_cannot_read():
    token = SecretCipher([generate_key()]).encrypt("sk-live-abc")
    with pytest.raises(SecretDecryptionError):
        SecretCipher([generate_key()]).rotate(token, context="t")


def test_malformed_key_raises_loudly():
    with pytest.raises(SecretKeyError):
        SecretCipher([b"not-a-key"])


def test_empty_keyring_raises():
    with pytest.raises(SecretKeyError):
        SecretCipher([])


# --- key resolution --------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in ("POTIONUI_SECRET_KEY", "POTIONUI_SECRET_KEYS_RETIRED", "POTIONUI_SECRET_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POTIONUI_DB_PATH", str(tmp_path / "db.sqlite"))
    return tmp_path


def test_env_key_wins(clean_env, monkeypatch):
    key = generate_key().decode()
    monkeypatch.setenv("POTIONUI_SECRET_KEY", key)
    assert resolve_secret_keys() == [key.encode()]


def test_env_retired_keys_are_appended(clean_env, monkeypatch):
    primary, retired = generate_key().decode(), generate_key().decode()
    monkeypatch.setenv("POTIONUI_SECRET_KEY", primary)
    monkeypatch.setenv("POTIONUI_SECRET_KEYS_RETIRED", retired)
    assert resolve_secret_keys() == [primary.encode(), retired.encode()]


def test_key_file_is_generated_beside_the_database(clean_env):
    keys = resolve_secret_keys()
    path = clean_env / "secret.key"
    assert path.exists()
    assert read_key_file(path) == keys


def test_generated_key_file_is_owner_only(clean_env):
    resolve_secret_keys()
    mode = stat.S_IMODE((clean_env / "secret.key").stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_generation_is_stable_across_calls(clean_env):
    """A second boot reads the file back rather than minting a second key,
    which would make everything written on the first boot unreadable."""
    assert resolve_secret_keys() == resolve_secret_keys()


def test_key_file_env_override(clean_env, monkeypatch):
    target = clean_env / "nested" / "custom.key"
    monkeypatch.setenv("POTIONUI_SECRET_KEY_FILE", str(target))
    assert default_key_path() == target
    resolve_secret_keys()
    assert target.exists()


def test_missing_key_without_generation_raises(clean_env):
    with pytest.raises(SecretKeyError):
        resolve_secret_keys(allow_generate=False)


def test_empty_key_file_raises_rather_than_regenerating(clean_env):
    """An emptied key file means the operator lost their key. Minting a new one
    would silently orphan every stored credential."""
    path = clean_env / "secret.key"
    path.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(SecretKeyError):
        resolve_secret_keys()


def test_key_file_roundtrip_preserves_order(clean_env):
    keys = [generate_key(), generate_key()]
    path = clean_env / "ring.key"
    write_key_file(path, keys)
    assert read_key_file(path) == keys
