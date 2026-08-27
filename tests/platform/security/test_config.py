"""Tests for the AuthConfig class."""
import pytest
import os
import stat
from unittest.mock import Mock, patch

from src.platform.security.config import AuthConfig, SECRET_KEY_FILENAME
from src.platform.settings.settings import Settings


class TestAuthConfig:
    """Tests for AuthConfig."""

    @pytest.fixture
    def mock_settings(self):
        """Create a mock Settings."""
        return Mock(spec=Settings)

    @pytest.fixture
    def auth_config(self, mock_settings):
        """Create an AuthConfig with mock settings."""
        return AuthConfig(mock_settings)

    def test_secret_key_from_env(self, mock_settings):
        """Test that secret_key prefers environment variable."""
        with patch.dict(os.environ, {"POTIONUI_AUTH_SECRET_KEY": "env-secret-key"}):
            config = AuthConfig(mock_settings)
            assert config.secret_key == "env-secret-key"
            # Should not call settings
            mock_settings.get_setting.assert_not_called()

    def test_secret_key_from_settings(self, mock_settings):
        """Test that secret_key falls back to settings."""
        mock_settings.get_setting.return_value = "settings-secret-key"

        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            # Clear any cached key by creating a new instance
            config = AuthConfig(mock_settings)
            key = config.secret_key

            assert key == "settings-secret-key"
            mock_settings.get_setting.assert_called_with("auth_secret_key", None)

    def test_secret_key_generates_random(self, mock_settings, tmp_path):
        """Test that secret_key generates a random key when nothing is
        configured, and persists it under the file storage directory."""
        mock_settings.get_setting.return_value = None
        mock_settings.get_file_storage_directory.return_value = str(tmp_path)

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)
            key = config.secret_key

            assert key is not None
            assert len(key) > 20  # Random key should be reasonably long
            assert (tmp_path / SECRET_KEY_FILENAME).read_text().strip() == key

    def test_secret_key_caches_value(self, mock_settings):
        """Test that secret_key caches the value."""
        mock_settings.get_setting.return_value = "settings-secret-key"

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)

            # Call multiple times
            key1 = config.secret_key
            key2 = config.secret_key
            key3 = config.secret_key

            assert key1 == key2 == key3
            # Should only call settings once
            assert mock_settings.get_setting.call_count == 1

    def test_algorithm_default(self, auth_config):
        """Test algorithm default value."""
        with patch.dict(os.environ, {}, clear=True):
            assert auth_config.algorithm == "HS256"

    def test_algorithm_from_env(self, mock_settings):
        """Test algorithm from environment variable."""
        with patch.dict(os.environ, {"POTIONUI_AUTH_ALGORITHM": "HS512"}):
            config = AuthConfig(mock_settings)
            assert config.algorithm == "HS512"

    def test_access_token_expire_minutes_default(self, mock_settings):
        """Test default token expiration."""
        mock_settings.get_setting.return_value = None

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)
            assert config.access_token_expire_minutes == 1440  # 24 hours

    def test_access_token_expire_minutes_from_env(self, mock_settings):
        """Test token expiration from environment variable."""
        with patch.dict(os.environ, {"POTIONUI_AUTH_TOKEN_EXPIRE_MINUTES": "120"}):
            config = AuthConfig(mock_settings)
            assert config.access_token_expire_minutes == 120

    def test_access_token_expire_minutes_from_settings(self, mock_settings):
        """Test token expiration from settings."""
        mock_settings.get_setting.return_value = 240

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)
            assert config.access_token_expire_minutes == 240

    def test_access_token_expire_minutes_invalid_env(self, mock_settings):
        """Test handling of invalid environment variable for token expiration."""
        mock_settings.get_setting.return_value = None

        with patch.dict(os.environ, {"POTIONUI_AUTH_TOKEN_EXPIRE_MINUTES": "invalid"}):
            config = AuthConfig(mock_settings)
            # Should fall back to default
            assert config.access_token_expire_minutes == 1440

    # Remember me token expiration tests

    def test_remember_me_token_expire_days_default(self, mock_settings):
        """Test default remember me token expiration is 30 days."""
        mock_settings.get_setting.return_value = None

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)
            assert config.remember_me_token_expire_days == 30

    def test_remember_me_token_expire_days_from_env(self, mock_settings):
        """Test remember me expiration from environment variable."""
        with patch.dict(os.environ, {"POTIONUI_AUTH_REMEMBER_ME_EXPIRE_DAYS": "60"}):
            config = AuthConfig(mock_settings)
            assert config.remember_me_token_expire_days == 60

    def test_remember_me_token_expire_days_from_settings(self, mock_settings):
        """Test remember me expiration from settings."""
        mock_settings.get_setting.return_value = 14

        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(mock_settings)
            assert config.remember_me_token_expire_days == 14


class TestPersistedSecretKey:
    """Tests for durable, file-backed auth secret persistence."""

    @pytest.fixture
    def settings(self, tmp_path):
        """Settings mock with no env/DB secret and a writable storage dir."""
        m = Mock(spec=Settings)
        m.get_setting.return_value = None
        m.get_file_storage_directory.return_value = str(tmp_path)
        return m

    def test_env_takes_precedence_over_file(self, settings, tmp_path):
        """Env var wins even when a key file exists, and nothing is persisted."""
        (tmp_path / SECRET_KEY_FILENAME).write_text("file-secret")
        with patch.dict(os.environ, {"POTIONUI_AUTH_SECRET_KEY": "env-secret"}):
            config = AuthConfig(settings)
            assert config.secret_key == "env-secret"
        # The file value must be untouched.
        assert (tmp_path / SECRET_KEY_FILENAME).read_text() == "file-secret"

    def test_settings_takes_precedence_over_file(self, settings, tmp_path):
        """DB setting wins over an existing key file."""
        settings.get_setting.return_value = "db-secret"
        (tmp_path / SECRET_KEY_FILENAME).write_text("file-secret")
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            assert config.secret_key == "db-secret"

    def test_reads_existing_file(self, settings, tmp_path):
        """An existing key file is read when no env/DB secret is set."""
        path = tmp_path / SECRET_KEY_FILENAME
        path.write_text("persisted-secret\n")
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            assert config.secret_key == "persisted-secret"

    def test_generates_and_persists_with_0600(self, settings, tmp_path):
        """First boot generates a key, writes it, and locks it to 0600."""
        path = tmp_path / SECRET_KEY_FILENAME
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            key = config.secret_key
        assert path.exists()
        assert path.read_text().strip() == key
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_generated_key_is_stable_across_instances(self, settings, tmp_path):
        """A second process (new instance) reads back the same persisted key."""
        with patch.dict(os.environ, {}, clear=True):
            first = AuthConfig(settings).secret_key
            second = AuthConfig(settings).secret_key
        assert first == second

    def test_empty_file_is_regenerated(self, settings, tmp_path):
        """An empty/corrupt key file is replaced with a fresh generated key."""
        path = tmp_path / SECRET_KEY_FILENAME
        path.write_text("   ")
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            key = config.secret_key
        assert key
        assert path.read_text().strip() == key

    def test_insecure_permissions_warns_but_uses_file(self, settings, tmp_path, caplog):
        """A group/world-readable key file is used but warned about."""
        path = tmp_path / SECRET_KEY_FILENAME
        path.write_text("loose-secret")
        os.chmod(path, 0o644)
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            with caplog.at_level("WARNING"):
                assert config.secret_key == "loose-secret"
        assert any("accessible to other users" in r.message for r in caplog.records)

    def test_unwritable_dir_falls_back_to_memory(self, settings, tmp_path, caplog):
        """When the key file cannot be written, fall back to an in-memory key
        and warn, naming the path."""
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            with patch("os.replace", side_effect=OSError("read-only fs")), \
                 caplog.at_level("WARNING"):
                key = config.secret_key
        assert key and len(key) > 20
        assert not (tmp_path / SECRET_KEY_FILENAME).exists()
        assert any("temporary in-memory key" in r.message for r in caplog.records)

    def test_no_storage_dir_falls_back_to_memory(self, tmp_path, caplog):
        """No resolvable storage dir -> in-memory key with a warning."""
        m = Mock(spec=Settings)
        m.get_setting.return_value = None
        m.get_file_storage_directory.return_value = ""
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(m)
            with caplog.at_level("WARNING"):
                key = config.secret_key
        assert key and len(key) > 20
        assert any("in-memory key" in r.message for r in caplog.records)

    def test_settings_read_failure_falls_back_to_memory(self, tmp_path):
        """If resolving the storage dir raises, degrade to an in-memory key."""
        m = Mock(spec=Settings)
        m.get_setting.return_value = None
        m.get_file_storage_directory.side_effect = RuntimeError("db not ready")
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(m)
            key = config.secret_key
        assert key and len(key) > 20

    def test_secret_value_never_logged(self, settings, tmp_path, caplog):
        """The secret value must never appear in log output."""
        with patch.dict(os.environ, {}, clear=True):
            config = AuthConfig(settings)
            with caplog.at_level("DEBUG"):
                key = config.secret_key
        assert all(key not in r.getMessage() for r in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
