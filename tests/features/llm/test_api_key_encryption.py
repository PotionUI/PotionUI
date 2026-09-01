"""An LLM configuration's API key is unreadable in a database dump.

Same shape as the plugin/backend credential tests: the real repository write
path, a real on-disk SQLite file, then a grep of that file's bytes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from src.features.llm.records import LLMConfiguration
from src.features.llm.repository import LLMConfigurationRepository
from src.platform.security.secrets import (
    SecretCipher,
    SecretDecryptionError,
    configure_secret_cipher,
    generate_key,
)

from tests.features.plugins.test_credential_encryption import FileDatabase

_MIGRATIONS = Path("src/platform/database/migrations")

PLAINTEXT_KEY = "sk-openai-DUMPGREP-4c7e1a"


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(config_id="llm-1", api_key=PLAINTEXT_KEY):
    return LLMConfiguration(
        id=config_id, name="OpenAI", type="openai", enabled=True,
        base_url="https://api.openai.com/v1", api_key=api_key,
        model="gpt-4", system_message="You are helpful.",
        temperature=0.7, max_tokens=1000, timeout=30,
    )


@pytest.fixture
def key():
    return generate_key()


@pytest.fixture
def db(tmp_path, key):
    database = FileDatabase(tmp_path / "db.sqlite")
    configure_secret_cipher(SecretCipher([key]))
    with patch("src.platform.database.database.db", database):
        _load("001_baseline", f"m001_{id(database)}").up()
        # The migration sets WAL (correct for a real install) on this same
        # persistent connection - put DELETE back so a committed write keeps
        # landing in the main file, which is what raw_bytes() below relies on.
        database._connection.execute("PRAGMA journal_mode=DELETE").close()
        yield database
    configure_secret_cipher(None)
    database.close()


def test_api_key_is_absent_from_the_raw_database_file(db):
    assert LLMConfigurationRepository().create(_config()) is True
    blob = db.raw_bytes()
    assert PLAINTEXT_KEY.encode() not in blob
    # Control: a neighbouring non-secret field IS greppable, so the assertion
    # above cannot pass merely because the row failed to write.
    assert b"https://api.openai.com/v1" in blob


def test_api_key_roundtrips(db):
    repo = LLMConfigurationRepository()
    repo.create(_config())
    assert repo.get_by_id("llm-1").api_key == PLAINTEXT_KEY
    assert repo.get_all()["llm-1"].api_key == PLAINTEXT_KEY


def test_stored_column_holds_an_envelope(db):
    LLMConfigurationRepository().create(_config())
    with db.get_cursor() as cursor:
        cursor.execute("SELECT api_key FROM llm_configurations WHERE id = 'llm-1'")
        stored = cursor.fetchone()[0]
    assert stored.startswith("enc:v1:")
    assert PLAINTEXT_KEY not in stored


def test_update_keeps_the_key_encrypted(db):
    repo = LLMConfigurationRepository()
    repo.create(_config())
    repo.update("llm-1", _config(api_key="sk-openai-ROTATED"))
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()
    assert b"sk-openai-ROTATED" not in db.raw_bytes()
    assert repo.get_by_id("llm-1").api_key == "sk-openai-ROTATED"


def test_wrong_key_withholds_the_key_and_flags_it_instead_of_raising(db):
    # A read must not raise: the admin LLM screen a raise would break is the
    # very place the operator re-enters the key (2026-07-27 lockout). "Never
    # silently used" is preserved by withholding the key entirely (None, so a
    # provider fails as "no API key") and flagging the record.
    repo = LLMConfigurationRepository()
    repo.create(_config())
    repo.create(_config(config_id="llm-2", api_key=None))
    configure_secret_cipher(SecretCipher([generate_key()]))

    broken = repo.get_by_id("llm-1")
    assert broken.api_key is None
    assert broken.api_key_unreadable is True

    # The listing that locked the operator out must survive, and rows without
    # a stored key stay unflagged.
    listed = repo.get_all()
    assert listed["llm-1"].api_key_unreadable is True
    assert listed["llm-2"].api_key is None
    assert listed["llm-2"].api_key_unreadable is False


def test_empty_api_key_is_left_alone(db):
    repo = LLMConfigurationRepository()
    repo.create(_config(api_key=None))
    assert repo.get_by_id("llm-1").api_key is None


def test_rotation_reencrypts_without_loss(db, key):
    repo = LLMConfigurationRepository()
    repo.create(_config())

    new_key = generate_key()
    reader, writer = SecretCipher([key]), SecretCipher([new_key])
    for row in repo.iter_encrypted_api_keys():
        repo.replace_api_key(row["id"], writer.encrypt(reader.decrypt(row["api_key"], context="t")))

    configure_secret_cipher(writer)
    assert repo.get_by_id("llm-1").api_key == PLAINTEXT_KEY
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()
