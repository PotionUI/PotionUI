"""Credentials are unreadable in a database dump.

Every test here goes through the real write path - PluginRepository.set_plugin_setting
and BackendConfigManager.add_backend/update_backend - against a real on-disk SQLite file, and
then greps the bytes of that file. A fixture that constructed its own ciphertext
would prove nothing about the code that actually stores credentials.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pydantic import Field

from src.bootstrap.secrets_preflight import run_secret_preflight
from src.features.backends.backend_config import BackendConfigManager, BaseBackendConfig
from src.features.backends.records import Backend
from src.features.plugins.repository import PluginRepository
from src.features.backends.repository import BackendRepository
from src.platform.security.secrets import (
    SecretCipher,
    SecretDecryptionError,
    configure_secret_cipher,
    generate_key,
)

_MIGRATIONS = Path("src/platform/database/migrations")

PLAINTEXT_KEY = "sk-live-DUMPGREP-9f3a2b1c"


class FileDatabase:
    """A real on-disk SQLite database, so a test can read the stored bytes.

    Journalling is DELETE rather than WAL so a committed write lands in the main
    file; the dump assertions still read every sidecar the engine may have left,
    so the test cannot pass merely because the value went somewhere else.
    """

    def __init__(self, path: Path):
        self.db_path = path
        self._connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=DELETE").close()
        self._connection.execute("PRAGMA foreign_keys = ON").close()

    @contextmanager
    def get_connection(self):
        yield self._connection

    @contextmanager
    def get_cursor(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def raw_bytes(self) -> bytes:
        """Everything on disk for this database: the file and any sidecars."""
        self._connection.commit()
        blob = b""
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                blob += candidate.read_bytes()
        return blob

    def close(self):
        self._connection.close()


def _create_users_table(database):
    """plugin_settings.user_id carries an FK to users; the plugin migration
    predates nothing here, so the referenced table has to exist."""
    with database.get_cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def key():
    return generate_key()


@pytest.fixture
def db(tmp_path, key):
    """A file-backed DB with the plugin + backend schema, and a live cipher."""
    database = FileDatabase(tmp_path / "db.sqlite")
    configure_secret_cipher(SecretCipher([key]))
    with patch("src.platform.database.database.db", database), \
         patch("src.platform.database.db", database), \
         patch("src.features.plugins.repository.db", database), \
         patch("src.features.backends.repository.db", database):
        _create_users_table(database)
        _load("041_create_plugins", f"m041_{id(database)}").up()
        _load("011_create_backends", f"m011_{id(database)}").up()
        with database.get_cursor() as cursor:
            cursor.execute("ALTER TABLE backends RENAME COLUMN type TO engine")
        _load("111_encrypt_stored_credentials", f"m111_{id(database)}").up()
        _load("119_add_backend_driver", f"m119_{id(database)}").up()
        yield database
    configure_secret_cipher(None)
    database.close()


def _install_plugin(database, plugin_id="acme-provider"):
    with database.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO plugins (id, name, version, manifest_path) VALUES (?, ?, ?, ?)",
            (plugin_id, plugin_id, "1.0.0", f"plugins/{plugin_id}/manifest.yml"),
        )
    return plugin_id


# --- plugin settings -------------------------------------------------------


def test_stored_secret_is_absent_from_the_raw_database_file(db):
    plugin_id = _install_plugin(db)
    PluginRepository().set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()


def test_the_dump_check_can_actually_see_a_plaintext_value(db):
    """Control: a value written WITHOUT the secret flag is trivially greppable.

    Without this, the assertion above could pass for reasons unrelated to
    encryption - a wrong table, an unflushed write, a typo in the needle.
    """
    plugin_id = _install_plugin(db)
    PluginRepository().set_plugin_setting(
        plugin_id=plugin_id, setting_key="endpoint",
        setting_value=PLAINTEXT_KEY, is_secret=False,
    )
    assert PLAINTEXT_KEY.encode() in db.raw_bytes()


def test_secret_roundtrips_through_the_repository(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    assert repo.get_plugin_setting(plugin_id, "api_key").setting_value == PLAINTEXT_KEY
    listed = {s.setting_key: s.setting_value for s in repo.get_plugin_settings(plugin_id)}
    assert listed["api_key"] == PLAINTEXT_KEY


def test_stored_column_holds_an_envelope_not_the_value(db):
    plugin_id = _install_plugin(db)
    PluginRepository().set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings WHERE setting_key = 'api_key'")
        stored = cursor.fetchone()[0]
    assert stored.startswith("enc:v1:")
    assert PLAINTEXT_KEY not in stored


def test_wrong_key_withholds_the_value_and_flags_it_instead_of_raising(db):
    # A read must not raise: the settings screen a raise would break is the
    # very place the operator re-enters the credential (2026-07-27 lockout).
    # "Never silently used" is preserved by withholding the value entirely
    # and flagging the record.
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    configure_secret_cipher(SecretCipher([generate_key()]))
    setting = repo.get_plugin_setting(plugin_id, "api_key")
    assert setting.setting_value is None
    assert setting.value_unreadable is True
    listed = repo.get_plugin_settings(plugin_id)
    assert any(s.setting_key == "api_key" and s.value_unreadable for s in listed)


def test_tampered_stored_value_is_withheld_and_flagged_on_read(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    with db.get_cursor() as cursor:
        cursor.execute("SELECT id, setting_value FROM plugin_settings WHERE setting_key = 'api_key'")
        row = cursor.fetchone()
        body = row[1]
        flipped = body[:-6] + ("A" if body[-6] != "A" else "B") + body[-5:]
        cursor.execute("UPDATE plugin_settings SET setting_value = ? WHERE id = ?", (flipped, row[0]))
    setting = repo.get_plugin_setting(plugin_id, "api_key")
    assert setting.setting_value is None
    assert setting.value_unreadable is True


def test_reentering_over_an_unreadable_value_recovers_it(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    configure_secret_cipher(SecretCipher([generate_key()]))
    assert repo.get_plugin_setting(plugin_id, "api_key").value_unreadable is True

    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value="sk-reentered-after-key-loss", is_secret=True,
    )
    recovered = repo.get_plugin_setting(plugin_id, "api_key")
    assert recovered.setting_value == "sk-reentered-after-key-loss"
    assert recovered.value_unreadable is False


def test_writing_the_mask_preserves_the_stored_credential(db):
    """A settings form saved without touching the credential round-trips the
    mask a read handed out; it must not become the new value."""
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value=PLAINTEXT_KEY, is_secret=True,
    )
    repo.set_plugin_setting(
        plugin_id=plugin_id, setting_key="api_key",
        setting_value="***", is_secret=True,
    )
    assert repo.get_plugin_setting(plugin_id, "api_key").setting_value == PLAINTEXT_KEY


def test_a_real_new_value_still_overwrites(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value="sk-live-REPLACED", is_secret=True)
    assert repo.get_plugin_setting(plugin_id, "api_key").setting_value == "sk-live-REPLACED"


def test_mask_never_overwrites_a_stored_credential_even_unflagged(db):
    """The mask must be refused as a value regardless of the is_secret argument.

    is_secret is recomputed from the manifest on every save, so a plugin that is
    uninstalled, mid-reload, or momentarily missing from the registry yields
    is_secret=False - and the guard would be skipped exactly when the caller has
    lost the knowledge that this row holds a credential.
    """
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)

    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value="***", is_secret=False)

    stored = repo.get_plugin_setting(plugin_id, "api_key")
    assert stored.setting_value == PLAINTEXT_KEY
    assert stored.is_secret is True


def test_a_save_with_no_manifest_is_refused_and_leaves_the_credential_intact(db):
    """End to end on the real path: real manager, real repository, real cipher.

    The registry returns no manifest (uninstalled / mid-reload / discovery
    race), so the manager cannot compute which keys are credentials. It used to
    fall through to is_secret=False for everything, which is how a newly typed
    key landed in the database in plaintext and unflagged. It now refuses the
    whole batch, so nothing is written under a guess - including the ordinary
    keys sharing the form, since a half-applied save reads as a successful one.

    The stored credential is untouched either way, which the mask guard in the
    repository still enforces independently for any other caller.
    """
    from src.features.plugins.manager import (
        PluginManager,
        PluginManifestUnavailableError,
    )

    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)

    registry = Mock()
    registry.get_plugin.return_value = None
    manager = PluginManager(plugin_repository=repo, plugin_registry=registry)

    with pytest.raises(PluginManifestUnavailableError):
        manager.update_plugin_settings(
            plugin_id, {"api_key": "***", "base_url": "https://edited.test"}
        )

    stored = repo.get_plugin_setting(plugin_id, "api_key")
    assert stored.setting_value == PLAINTEXT_KEY
    assert stored.is_secret is True
    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings WHERE setting_key = 'api_key'")
        assert cursor.fetchone()[0].startswith("enc:v1:")
    assert repo.get_plugin_setting(plugin_id, "base_url") is None


def test_a_new_credential_with_no_manifest_never_reaches_the_database(db):
    """The hole itself: a credential typed while the manifest is unavailable.

    Nothing downstream can recover from this one - the row is plaintext AND
    unflagged, so it is served back unmasked and the startup promotion pass,
    which keys off the flag, cannot find it.
    """
    from src.features.plugins.manager import (
        PluginManager,
        PluginManifestUnavailableError,
    )

    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    registry = Mock()
    registry.get_plugin.return_value = None
    manager = PluginManager(plugin_repository=repo, plugin_registry=registry)

    with pytest.raises(PluginManifestUnavailableError):
        manager.update_plugin_settings(plugin_id, {"api_key": PLAINTEXT_KEY})

    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings")
        assert [row[0] for row in cursor.fetchall()] == []


def test_the_mask_flow_still_works_once_the_manifest_is_back(db):
    """The refusal is about the missing manifest, not about the mask: with a
    manifest present, saving a form that round-trips the mask still preserves
    the credential and applies the edited fields."""
    from src.features.plugins.manager import PluginManager
    from src.platform.plugins.manifest import SettingSpec

    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)

    manifest = Mock()
    manifest.settings = [
        SettingSpec(name="api_key", type="string", is_secret=True),
        SettingSpec(name="base_url", type="string"),
    ]
    registry = Mock()
    registry.get_plugin.return_value = manifest
    manager = PluginManager(plugin_repository=repo, plugin_registry=registry)

    manager.update_plugin_settings(
        plugin_id, {"api_key": "***", "base_url": "https://edited.test"}
    )

    stored = repo.get_plugin_setting(plugin_id, "api_key")
    assert stored.setting_value == PLAINTEXT_KEY
    assert stored.is_secret is True
    assert repo.get_plugin_setting(plugin_id, "base_url").setting_value == "https://edited.test"


def test_non_secret_settings_are_untouched(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="base_url",
                            setting_value="https://example.test", is_secret=False)
    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings WHERE setting_key = 'base_url'")
        assert cursor.fetchone()[0] == "https://example.test"


def test_legacy_plaintext_value_is_still_readable(db):
    """A value written before encryption existed has no envelope; reading it
    must return it, not raise."""
    plugin_id = _install_plugin(db)
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
            "VALUES (?, ?, ?, 1)",
            (plugin_id, "api_key", PLAINTEXT_KEY),
        )
    assert PluginRepository().get_plugin_setting(plugin_id, "api_key").setting_value == PLAINTEXT_KEY


# --- migration 111 ---------------------------------------------------------


def test_migration_encrypts_preexisting_plaintext_credentials(tmp_path, key):
    """A user who configured credentials before this landed keeps them, and they
    are no longer in the dump."""
    database = FileDatabase(tmp_path / "legacy.sqlite")
    configure_secret_cipher(SecretCipher([key]))
    try:
        with patch("src.platform.database.database.db", database), \
             patch("src.platform.database.db", database), \
             patch("src.features.plugins.repository.db", database):
            _create_users_table(database)
            _load("041_create_plugins", f"m041_legacy_{id(database)}").up()
            _install_plugin(database)
            with database.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
                    "VALUES (?, ?, ?, 1)",
                    ("acme-provider", "api_key", PLAINTEXT_KEY),
                )
                cursor.execute(
                    "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
                    "VALUES (?, ?, ?, 0)",
                    ("acme-provider", "base_url", "https://example.test"),
                )
            assert PLAINTEXT_KEY.encode() in database.raw_bytes()

            _load("111_encrypt_stored_credentials", f"m111_legacy_{id(database)}").up()

            assert PLAINTEXT_KEY.encode() not in database.raw_bytes()
            repo = PluginRepository()
            assert repo.get_plugin_setting("acme-provider", "api_key").setting_value == PLAINTEXT_KEY
            assert repo.get_plugin_setting("acme-provider", "base_url").setting_value == "https://example.test"
    finally:
        configure_secret_cipher(None)
        database.close()


def test_migration_is_idempotent(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    _load("111_encrypt_stored_credentials", f"m111_again_{id(db)}").up()
    assert repo.get_plugin_setting(plugin_id, "api_key").setting_value == PLAINTEXT_KEY


def test_migration_creates_the_audit_table(db):
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_setting_audit'")
        assert cursor.fetchone() is not None


# --- audit trail -----------------------------------------------------------


def test_audit_records_who_changed_what_and_never_the_value(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    repo.record_setting_change(
        plugin_id=plugin_id, setting_key="api_key", action="set",
        actor_user_id="user-1", actor_username="admin", is_secret=True,
    )
    entries = repo.get_setting_audit(plugin_id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["setting_key"] == "api_key"
    assert entry["actor_username"] == "admin"
    assert entry["action"] == "set"
    assert entry["is_secret"] == 1
    assert PLAINTEXT_KEY not in json.dumps(entry)


def test_audit_table_never_holds_the_credential_in_the_dump(db):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    repo.record_setting_change(plugin_id=plugin_id, setting_key="api_key",
                               action="set", actor_user_id="user-1", is_secret=True)
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()


# --- rotation --------------------------------------------------------------


def test_rotation_reencrypts_without_loss(db, key):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)

    new_key = generate_key()
    reader, writer = SecretCipher([key]), SecretCipher([new_key])
    for row in repo.iter_encrypted_settings():
        plaintext = reader.decrypt(row["setting_value"], context="t")
        repo.replace_encrypted_value(row["id"], writer.encrypt(plaintext))

    configure_secret_cipher(writer)
    assert repo.get_plugin_setting(plugin_id, "api_key").setting_value == PLAINTEXT_KEY
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()

    # The old key really can no longer read it - proof the rotation
    # re-encrypted rather than kept the old envelope.
    configure_secret_cipher(SecretCipher([key]))
    stale = repo.get_plugin_setting(plugin_id, "api_key")
    assert stale.setting_value is None
    assert stale.value_unreadable is True


# --- backend configs -------------------------------------------------------


class FakeEngineConfig(BaseBackendConfig):
    """A real engine config class, declaring one secret field the way a plugin does."""

    server_url: str = Field(default="http://localhost:8188")
    api_key: str = Field(default="", json_schema_extra={"secret": True})


@pytest.fixture
def config_manager(db):
    """The real BackendConfigManager, with a fake engine registered."""
    return BackendConfigManager(
        backend_repository=BackendRepository(),
        registered_config_types={"fake-engine": FakeEngineConfig},
    )


def _add(config_manager, api_key=PLAINTEXT_KEY):
    config_manager.add_backend(FakeEngineConfig(
        id="be-1", name="Remote", engine="fake-engine", api_key=api_key,
    ))


def test_backend_secret_field_is_absent_from_the_raw_database_file(db, config_manager):
    _add(config_manager)
    blob = db.raw_bytes()
    assert PLAINTEXT_KEY.encode() not in blob
    assert b"http://localhost:8188" in blob


def test_the_backend_dump_check_can_actually_see_a_plaintext_value(db, config_manager):
    """Control: the same value in a NON-secret field is greppable, so the
    assertion above is testing encryption and not a missing write."""
    config_manager.add_backend(FakeEngineConfig(
        id="be-2", name="Remote", engine="fake-engine", server_url=PLAINTEXT_KEY,
    ))
    assert PLAINTEXT_KEY.encode() in db.raw_bytes()


def test_backend_secret_roundtrips_through_the_manager(db, config_manager):
    _add(config_manager)
    config_manager._backends_cache = None
    loaded = config_manager.get_backend("be-1")
    assert loaded.api_key == PLAINTEXT_KEY
    assert loaded.server_url == "http://localhost:8188"


def test_backend_stored_column_holds_an_envelope(db, config_manager):
    _add(config_manager)
    with db.get_cursor() as cursor:
        cursor.execute("SELECT config FROM backends WHERE id = 'be-1'")
        stored = json.loads(cursor.fetchone()[0])
    assert stored["api_key"].startswith("enc:v1:")
    assert stored["server_url"] == "http://localhost:8188"


def test_backend_wrong_key_raises_on_read(db, config_manager):
    _add(config_manager)
    configure_secret_cipher(SecretCipher([generate_key()]))
    with pytest.raises(SecretDecryptionError):
        BackendRepository().get_by_id("be-1")


def test_backend_update_keeps_the_credential_encrypted(db, config_manager):
    _add(config_manager)
    config_manager.update_backend("be-1", FakeEngineConfig(
        id="be-1", name="Renamed", engine="fake-engine", api_key=PLAINTEXT_KEY,
    ))
    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()
    assert BackendRepository().get_by_id("be-1").config["api_key"] == PLAINTEXT_KEY


def test_startup_sweep_encrypts_a_preexisting_plaintext_backend_credential(db, config_manager):
    """A backend configured before this landed keeps working, and stops being
    greppable once the sweep has run."""
    BackendRepository().create(Backend(
        id="be-legacy", name="Legacy", engine="fake-engine", driver="fake-engine", enabled=True,
        is_default=False, config={"server_url": "http://legacy", "api_key": PLAINTEXT_KEY},
    ))
    assert PLAINTEXT_KEY.encode() in db.raw_bytes()

    assert config_manager.encrypt_stored_credentials() == 1

    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()
    assert BackendRepository().get_by_id("be-legacy").config["api_key"] == PLAINTEXT_KEY


def test_startup_sweep_is_idempotent(db, config_manager):
    _add(config_manager)
    assert config_manager.encrypt_stored_credentials() == 0
    assert BackendRepository().get_by_id("be-1").config["api_key"] == PLAINTEXT_KEY


# --- startup preflight -----------------------------------------------------


def test_preflight_reports_undecryptable_values_by_location(db, config_manager):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    _add(config_manager)

    configure_secret_cipher(SecretCipher([generate_key()]))
    reported = run_secret_preflight(repo, config_manager)

    assert sorted(reported) == ["backends:be-1/api_key", f"plugin_settings:{plugin_id}/api_key"]
    assert all(PLAINTEXT_KEY not in entry for entry in reported)


def test_preflight_promotes_a_manifest_declared_secret_stored_unflagged(db, config_manager):
    """Every save used to force is_secret=False, so a declared credential could
    be sitting in the clear with no flag for migration 111 to key off."""
    from src.features.plugins.manager import PluginManager
    from src.platform.plugins.manifest import SettingSpec

    plugin_id = _install_plugin(db)
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, is_secret) "
            "VALUES (?, 'api_key', ?, 0)",
            (plugin_id, PLAINTEXT_KEY),
        )
    assert PLAINTEXT_KEY.encode() in db.raw_bytes()

    manifest = Mock()
    manifest.settings = [SettingSpec(name="api_key", type="string", is_secret=True)]
    registry = Mock()
    registry.get_plugin.return_value = manifest
    repo = PluginRepository()
    manager = PluginManager(plugin_repository=repo, plugin_registry=registry)

    assert run_secret_preflight(repo, config_manager, plugin_manager=manager) == []

    assert PLAINTEXT_KEY.encode() not in db.raw_bytes()
    stored = repo.get_plugin_setting(plugin_id, "api_key")
    assert stored.setting_value == PLAINTEXT_KEY
    assert stored.is_secret is True


def test_preflight_is_silent_when_everything_decrypts(db, config_manager):
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    _add(config_manager)
    assert run_secret_preflight(repo, config_manager) == []


def test_preflight_does_not_overwrite_an_undecryptable_value(db, config_manager):
    """The dangerous failure: a sweep that re-encrypts what it could not read
    would destroy the credential permanently."""
    plugin_id = _install_plugin(db)
    repo = PluginRepository()
    repo.set_plugin_setting(plugin_id=plugin_id, setting_key="api_key",
                            setting_value=PLAINTEXT_KEY, is_secret=True)
    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings WHERE setting_key = 'api_key'")
        before = cursor.fetchone()[0]

    configure_secret_cipher(SecretCipher([generate_key()]))
    run_secret_preflight(repo, config_manager)

    with db.get_cursor() as cursor:
        cursor.execute("SELECT setting_value FROM plugin_settings WHERE setting_key = 'api_key'")
        assert cursor.fetchone()[0] == before
