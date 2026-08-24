"""Migrations 109-111 run in sequence against a realistic pre-existing database.

Each of 109 (remote_executions), 110 (model_availability.digest), and 111
(credential encryption) was authored and tested in isolation. Nothing exercises
them back-to-back against a database that already has 108 migrations of real
history in it - plugin settings (including a plaintext credential that was
never flagged `is_secret`, which is exactly the shape the pre-existing
`is_secret` bug produced), an LLM configuration with a live api_key, a backend
with a config blob, and model/model_availability rows with and without a
digest.

Uses the real (file-backed) `Database` singleton, migrated with the production
`MigrationManager`, not the in-memory `TestDatabase` - the point is to catch
anything that only shows up against the real migration path.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationManager
from src.platform.security.secrets import (
    ENV_KEY,
    ENV_RETIRED_KEYS,
    SecretDecryptionError,
    configure_secret_cipher,
    generate_key,
    get_secret_cipher,
)


def _migrations_up_to(manager: MigrationManager, max_number: int) -> None:
    manager.get_applied_migrations()  # ensures applied_migrations exists
    available = manager.get_available_migrations()
    for name in available:
        number = int(name.split("_", 1)[0])
        if number <= max_number:
            manager._run_migration(name)
            manager._mark_migration_applied(name)


def _migrations_after(manager: MigrationManager, min_number: int) -> list[str]:
    available = manager.get_available_migrations()
    applied = set(manager.get_applied_migrations())
    return [
        name for name in available
        if int(name.split("_", 1)[0]) > min_number and name not in applied
    ]


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """Point the global DB singleton at a scratch file. Never the live DB."""
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "chain.db"
    # Give this test its own key file location too, in case anything falls
    # back to the file-beside-the-database resolution path instead of the
    # session's POTIONUI_SECRET_KEY env var.
    monkeypatch.setenv("POTIONUI_DB_PATH", str(global_db.db_path))
    try:
        yield global_db
    finally:
        global_db.db_path = original_path


@pytest.fixture
def pre_109_db(scratch_db):
    """Schema + realistic rows as of migration 108, nothing from 109-111 yet."""
    manager = MigrationManager()
    _migrations_up_to(manager, 108)

    with scratch_db.get_cursor() as cursor:
        # A plugin, with two settings: one correctly flagged+plaintext (the
        # shape a pre-encryption install has), and one that is a real secret
        # but was never flagged - the exact damage the old
        # `is_secret=False` bug left behind, and the case migration 111
        # cannot fix on its own (it only touches `is_secret = 1` rows).
        cursor.execute(
            "INSERT INTO plugins (id, name, version, type, enabled, manifest_path) "
            "VALUES ('acme-provider','Acme','1.0.0','backend-only',1,'plugins/acme/manifest.yml')"
        )
        cursor.execute(
            "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
            "VALUES ('acme-provider', 'api_key', 'sk-legacy-flagged-plaintext', 1)"
        )
        cursor.execute(
            "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
            "VALUES ('acme-provider', 'webhook_secret', 'whsec-legacy-unflagged', 0)"
        )
        cursor.execute(
            "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
            "VALUES ('acme-provider', 'base_url', 'https://example.test', 0)"
        )

        # An LLM configuration with a live plaintext api_key.
        cursor.execute(
            "INSERT INTO llm_configurations "
            "(id, name, type, enabled, base_url, api_key, model, system_message, temperature, max_tokens, timeout) "
            "VALUES ('llm-1','OpenAI','openai',1,'https://api.openai.com','sk-live-real-key',"
            "'gpt-4','You are helpful.',0.7,1000,30)"
        )
        # ... and one with no api_key at all (legacy row, local/self-hosted model).
        cursor.execute(
            "INSERT INTO llm_configurations "
            "(id, name, type, enabled, base_url, api_key, model, system_message, temperature, max_tokens, timeout) "
            "VALUES ('llm-2','Local','ollama',1,'http://localhost:11434',NULL,"
            "'llama3','You are helpful.',0.7,1000,30)"
        )

        # The native backend, auto-provisioned in every real install.
        cursor.execute(
            "INSERT INTO backends (id, name, engine, enabled, is_default, config) "
            "VALUES ('native','Local Generation','native',1,1,'{}')"
        )
        # A backend with a config blob holding what would be a secret field
        # once its engine's config class is known - migration 111 explicitly
        # does not touch this; BackendConfigManager does, at startup.
        cursor.execute(
            "INSERT INTO backends (id, name, engine, enabled, is_default, config) "
            "VALUES ('comfy-1','Remote ComfyUI','comfyui',1,0,?)",
            (json.dumps({"api_key": "comfy-plaintext-key", "url": "http://10.0.0.5:8188"}),),
        )

        # Models: one with a digest already (native-indexed), one without.
        cursor.execute(
            "INSERT INTO models (id, filename, file_path, file_size, sha256, model_type) "
            "VALUES ('model-1', 'sdxl.safetensors', 'models/checkpoints/sdxl.safetensors', 1024, "
            "'deadbeef' || substr(hex(randomblob(28)), 1, 56), 'checkpoint')"
        )
        cursor.execute(
            "INSERT INTO models (id, filename, file_path, file_size, sha256, model_type) "
            "VALUES ('model-2', 'lora.safetensors', 'models/loras/lora.safetensors', 512, NULL, 'lora')"
        )

        cursor.execute("PRAGMA table_info(model_availability)")
        avail_cols = {row["name"] for row in cursor.fetchall()}
        assert "digest" not in avail_cols, "digest should not exist before migration 110"

        cursor.execute(
            "INSERT INTO model_availability (id, model_id, backend_id, ref) "
            "VALUES ('avail-1', 'model-1', 'native', 'models/checkpoints/sdxl.safetensors')"
        )
        cursor.execute(
            "INSERT INTO model_availability (id, model_id, backend_id, ref) "
            "VALUES ('avail-2', 'model-2', 'comfy-1', 'style/lora.safetensors')"
        )

    return scratch_db


def _run_109_111(scratch_db) -> MigrationManager:
    manager = MigrationManager()
    expected = [
        "109_add_remote_executions",
        "110_model_availability_digest",
        "111_encrypt_stored_credentials",
    ]
    pending = _migrations_after(manager, 108)
    # Prefix rather than equality, and `expected` rather than `pending` in the
    # loop: this helper is about the 109-111 chain, so a migration added after
    # 111 is not its concern and must not fail it or be dragged into its runs.
    assert pending[:len(expected)] == expected, f"unexpected pending set: {pending}"
    for name in expected:
        manager._run_migration(name)
        manager._mark_migration_applied(name)
    return manager


class TestMigrationChain:
    def test_109_creates_remote_executions(self, pre_109_db):
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM remote_executions")
            assert cursor.fetchone()["n"] == 0
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='remote_executions'")
            assert cursor.fetchone() is not None

    def test_110_adds_digest_column_nullable_on_existing_rows(self, pre_109_db):
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(model_availability)")
            cols = {row["name"] for row in cursor.fetchall()}
            assert "digest" in cols

            cursor.execute("SELECT digest FROM model_availability WHERE id = 'avail-1'")
            assert cursor.fetchone()["digest"] is None
            cursor.execute("SELECT digest FROM model_availability WHERE id = 'avail-2'")
            assert cursor.fetchone()["digest"] is None

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_hash_cache'")
            assert cursor.fetchone() is not None

    def test_111_encrypts_flagged_plugin_setting(self, pre_109_db):
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value, is_secret FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            row = cursor.fetchone()
            assert row["is_secret"] == 1
            assert row["setting_value"].startswith("enc:v1:")
            cipher = get_secret_cipher()
            assert cipher.decrypt(row["setting_value"], context="test") == "sk-legacy-flagged-plaintext"

    def test_111_does_not_touch_unflagged_plugin_secret(self, pre_109_db):
        """The known gap: migration 111 keys off `is_secret = 1`. A credential
        that was written unflagged (the pre-fix bug's exact damage) is left in
        the clear by the migration - closing that gap requires the manifest
        (via PluginManager.encrypt_declared_secrets at startup), which a
        migration cannot reach. This is a documented limitation, verified
        here rather than assumed."""
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value, is_secret FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='webhook_secret'"
            )
            row = cursor.fetchone()
            assert row["is_secret"] == 0
            assert row["setting_value"] == "whsec-legacy-unflagged"

    def test_111_encrypts_llm_api_key_and_leaves_null_alone(self, pre_109_db):
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute("SELECT api_key FROM llm_configurations WHERE id='llm-1'")
            key = cursor.fetchone()["api_key"]
            assert key.startswith("enc:v1:")
            assert get_secret_cipher().decrypt(key, context="test") == "sk-live-real-key"

            cursor.execute("SELECT api_key FROM llm_configurations WHERE id='llm-2'")
            assert cursor.fetchone()["api_key"] is None

    def test_111_does_not_touch_backend_config(self, pre_109_db):
        """Documented: backend secrets are handled by BackendConfigManager at
        startup, not by this migration, because which fields are secret is
        engine-specific knowledge that only exists once the plugin registry
        has been built."""
        _run_109_111(pre_109_db)
        with pre_109_db.get_cursor() as cursor:
            cursor.execute("SELECT config FROM backends WHERE id='comfy-1'")
            config = json.loads(cursor.fetchone()["config"])
            assert config["api_key"] == "comfy-plaintext-key"

    def test_111_is_idempotent_no_double_encryption(self, pre_109_db):
        """Re-running 111's up() (e.g. a hand re-apply, or a migration runner
        that doesn't track `applied_migrations` correctly) must not wrap an
        already-encrypted value a second time."""
        manager = _run_109_111(pre_109_db)

        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            before_setting = cursor.fetchone()["setting_value"]
            cursor.execute("SELECT api_key FROM llm_configurations WHERE id='llm-1'")
            before_llm = cursor.fetchone()["api_key"]

        manager._run_migration("111_encrypt_stored_credentials")

        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            after_setting = cursor.fetchone()["setting_value"]
            cursor.execute("SELECT api_key FROM llm_configurations WHERE id='llm-1'")
            after_llm = cursor.fetchone()["api_key"]

        assert after_setting == before_setting, "re-running the migration re-encrypted an already-encrypted value"
        assert after_llm == before_llm
        assert not after_setting.startswith("enc:v1:enc:v1:")
        assert not after_llm.startswith("enc:v1:enc:v1:")
        # Both values must still decrypt cleanly to the original plaintext.
        cipher = get_secret_cipher()
        assert cipher.decrypt(after_setting, context="test") == "sk-legacy-flagged-plaintext"
        assert cipher.decrypt(after_llm, context="test") == "sk-live-real-key"

    def test_111_idempotent_when_key_rotates_between_runs(self, pre_109_db, monkeypatch):
        """A value encrypted under key A, when 111 is re-run after the key
        rotated to key B (A now only in POTIONUI_SECRET_KEYS_RETIRED), must
        still be left alone (skipped via the envelope prefix, not decrypted
        and compared) - and must still be decryptable via the retired key."""
        manager = _run_109_111(pre_109_db)

        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            encrypted_under_a = cursor.fetchone()["setting_value"]

        # Recover key A's raw bytes from the env var the session fixture set.
        import os
        key_a_raw = os.environ[ENV_KEY]

        key_b_raw = generate_key().decode("ascii")
        monkeypatch.setenv(ENV_KEY, key_b_raw)
        monkeypatch.setenv(ENV_RETIRED_KEYS, key_a_raw)
        configure_secret_cipher(None)
        try:
            manager._run_migration("111_encrypt_stored_credentials")

            with pre_109_db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT setting_value FROM plugin_settings "
                    "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
                )
                after_rotation = cursor.fetchone()["setting_value"]

            assert after_rotation == encrypted_under_a, (
                "the migration re-touched a value already in an envelope, even though "
                "the active key changed - it should be a structural (prefix) skip, not "
                "a decrypt-and-compare"
            )
            # Readable via the retired key.
            cipher_with_retired = get_secret_cipher()
            assert cipher_with_retired.decrypt(after_rotation, context="test") == "sk-legacy-flagged-plaintext"
        finally:
            configure_secret_cipher(None)

    def test_undecryptable_after_key_lost_raises_not_corrupts(self, pre_109_db, monkeypatch):
        """If the key is lost entirely (not even retired), a stored envelope
        must raise SecretDecryptionError on read - never silently return
        garbage or an empty credential, and the stored bytes must be
        untouched (not overwritten)."""
        manager = _run_109_111(pre_109_db)

        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            stored_before = cursor.fetchone()["setting_value"]

        monkeypatch.setenv(ENV_KEY, generate_key().decode("ascii"))
        monkeypatch.delenv(ENV_RETIRED_KEYS, raising=False)
        configure_secret_cipher(None)
        try:
            cipher = get_secret_cipher()
            with pytest.raises(SecretDecryptionError):
                cipher.decrypt(stored_before, context="plugin_settings:acme-provider/api_key")
            assert cipher.can_decrypt(stored_before) is False
        finally:
            configure_secret_cipher(None)

        with pre_109_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT setting_value FROM plugin_settings "
                "WHERE plugin_id='acme-provider' AND setting_key='api_key'"
            )
            stored_after = cursor.fetchone()["setting_value"]
        assert stored_after == stored_before, "a failed decrypt must never rewrite the stored value"
