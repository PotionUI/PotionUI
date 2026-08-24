"""StorageSettingsManager: reads storage_backend/s3_* settings, encrypts the
S3 secret key at rest, and builds the driver the settings describe."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

import tests.conftest as ct
from src.platform.filesystem.s3_driver import S3FileStorageDriver
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.filesystem.storage_settings import StorageSettingsManager
from src.platform.security.secrets import get_secret_cipher
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import SettingsManager

_MIGRATIONS = Path("src/platform/database/migrations")


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def storage_settings():
    test_database = ct.TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.settings.repository.db", test_database):
        _load("001_baseline", f"m001_{id(test_database)}").up()
        settings_manager = SettingsManager(SettingRepository())
        yield StorageSettingsManager(settings_manager)
    test_database.close()


class TestBackendSelection:
    def test_defaults_to_local(self, storage_settings):
        assert storage_settings.get_backend() == "local"

    def test_build_driver_defaults_to_local(self, storage_settings, tmp_path):
        driver = storage_settings.build_driver(str(tmp_path))
        assert isinstance(driver, LocalFileStorageDriver)


class TestConfiguredDirectoryGuard:
    """Regression for the 2026-08-15 incident: `file_storage_directory` got
    overwritten with a throwaway tmp_path by a test-isolation bug, and every
    existing generation/upload 404'd until someone noticed. The driver still
    builds (it must - it's not this layer's job to second-guess an admin's
    real directory change), but a missing configured directory next to an
    existing default one must be loud, not silent."""

    def test_warns_when_configured_directory_is_missing_but_default_exists(
        self, storage_settings, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "storage").mkdir()
        missing = tmp_path / "run-42" / "storage"

        with caplog.at_level("WARNING"):
            driver = storage_settings.build_driver(str(missing))

        assert isinstance(driver, LocalFileStorageDriver)
        assert any(
            "file_storage_directory" in r.message and str(missing) in r.message
            for r in caplog.records
        )

    def test_no_warning_when_configured_directory_already_exists(
        self, storage_settings, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "storage").mkdir()

        with caplog.at_level("WARNING"):
            storage_settings.build_driver(str(tmp_path))  # tmp_path itself exists

        assert not any("file_storage_directory" in r.message for r in caplog.records)

    def test_no_warning_when_default_directory_also_missing(
        self, storage_settings, tmp_path, monkeypatch, caplog
    ):
        # Fresh install: neither directory exists yet - nothing stale to warn about.
        monkeypatch.chdir(tmp_path)
        missing = tmp_path / "run-42" / "storage"

        with caplog.at_level("WARNING"):
            storage_settings.build_driver(str(missing))

        assert not any("file_storage_directory" in r.message for r in caplog.records)

    def test_never_rewrites_the_setting(self, storage_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "storage").mkdir()
        missing = tmp_path / "run-42" / "storage"

        storage_settings.build_driver(str(missing))

        # Unset (falls back to the migration default) - the guard only logs.
        assert storage_settings.settings.get_setting("file_storage_directory", "storage") == "storage"

    def test_invalid_stored_value_falls_back_to_local(self, storage_settings):
        storage_settings.settings.set_setting("storage_backend", "not-a-real-backend")
        assert storage_settings.get_backend() == "local"


class TestS3Config:
    def test_incomplete_s3_config_falls_back_to_local_driver(self, storage_settings, tmp_path, caplog):
        storage_settings.settings.set_setting("storage_backend", "s3")
        # bucket/access_key_id/secret intentionally left blank
        driver = storage_settings.build_driver(str(tmp_path))
        assert isinstance(driver, LocalFileStorageDriver)

    def test_complete_s3_config_builds_s3_driver(self, storage_settings, tmp_path):
        storage_settings.settings.set_setting("storage_backend", "s3")
        storage_settings.settings.set_setting("s3_bucket", "my-bucket")
        storage_settings.settings.set_setting("s3_access_key_id", "AKIDEXAMPLE")
        storage_settings.set_s3_secret_key("super-secret")
        storage_settings.settings.set_setting("s3_region", "eu-central-1")

        driver = storage_settings.build_driver(str(tmp_path))
        assert isinstance(driver, S3FileStorageDriver)
        assert driver.bucket == "my-bucket"
        assert driver.region == "eu-central-1"
        assert driver.secret_access_key == "super-secret"

    def test_secret_key_is_stored_encrypted_at_rest(self, storage_settings):
        storage_settings.set_s3_secret_key("super-secret")

        raw = storage_settings.settings.setting_repository.get_setting_by_key(
            "s3_secret_key"
        ).value
        assert raw != "super-secret"
        assert get_secret_cipher().is_encrypted(raw)

    def test_secret_key_round_trips_through_get_s3_config(self, storage_settings):
        storage_settings.set_s3_secret_key("super-secret")
        assert storage_settings.get_s3_config().secret_key == "super-secret"

    def test_empty_endpoint_url_is_normalized_to_none(self, storage_settings):
        assert storage_settings.get_s3_config().endpoint_url is None
