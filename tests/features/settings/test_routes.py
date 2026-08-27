import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.features.settings.routes import SettingsController
from src.features.settings.dto import SettingUpdateRequest
from src.platform.settings.settings import Settings
from src.features.models.directory import ModelDirectories
from src.platform.runtime.gpu import GpuMonitor
from src.features.backends.backend_registry import BackendRegistry
from src.platform.settings.repository import SettingRepository
from src.platform.security.user import User, AccountType
from src.platform.settings.records import Setting, SettingType, SettingValueType
from fastapi import HTTPException


class TestSettingsController:
    """Test cases for SettingsController - Core functionality only"""

    @pytest.fixture
    def mock_settings(self):
        """Mock Settings"""
        return Mock(spec=Settings)

    @pytest.fixture
    def mock_setting_repository(self):
        """Mock SettingRepository"""
        return Mock(spec=SettingRepository)

    @pytest.fixture
    def mock_model_directories(self):
        """Mock ModelDirectories"""
        return Mock(spec=ModelDirectories)

    @pytest.fixture
    def mock_gpu_monitor(self):
        """Mock GpuMonitor"""
        return Mock(spec=GpuMonitor)

    @pytest.fixture
    def mock_backend_registry(self):
        """Mock BackendRegistry"""
        mock = MagicMock(spec=BackendRegistry)
        mock.backend_config_store = MagicMock()
        mock.get_supported_engines.return_value = ['native', 'comfyui']
        return mock

    @pytest.fixture
    def controller(self, mock_settings, mock_setting_repository, mock_model_directories, mock_gpu_monitor, mock_backend_registry):
        """Create SettingsController instance with mocked dependencies"""
        return SettingsController(
            settings=mock_settings,
            setting_repository=mock_setting_repository,
            model_directories=mock_model_directories,
            gpu_monitor=mock_gpu_monitor,
            backend_registry=mock_backend_registry
        )

    @pytest.mark.asyncio
    async def test_get_settings_requires_authentication(self, controller):
        """Test that getting settings requires authentication"""
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_settings()
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == 'settings_get_failed'
        assert 'Authentication required' in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_get_settings_regular_user(self, controller, mock_settings, mock_setting_repository):
        """Test getting settings for regular user (only USER type settings)"""
        # Create mock user
        user = User(
            id="user123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type=AccountType.USER
        )
        
        # Create mock settings
        user_setting = Mock()
        user_setting.key = "nsfw_filter"
        user_setting.type = SettingType.USER
        user_setting.get_typed_value.return_value = False
        
        system_setting = Mock()
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        
        mock_setting_repository.get_all_settings.return_value = [user_setting, system_setting]
        mock_settings.get_setting.return_value = False
        
        response = await controller.get_settings(user)
        
        assert response.success is True
        assert "nsfw_filter" in response.data
        assert "outputs_directory" not in response.data  # System settings hidden from regular users

    @pytest.mark.asyncio
    async def test_get_settings_never_leaks_system_settings_to_regular_user(
        self, controller, mock_settings, mock_setting_repository
    ):
        """Regression guard: a non-admin GET /settings must exclude EVERY SYSTEM
        setting - credentials, paths, and the registration_policy from the
        instance-claim work - while still returning USER-typed preferences.

        The per-type filter is the only thing standing between a regular user and
        sensitive/admin-shaped config; if a future setting is mis-typed USER or
        the filter is dropped, this fails.
        """
        user = User(
            id="u1", username="u", email="u@example.com",
            password_hash="h", account_type=AccountType.USER,
        )

        def _sys(key):
            s = Mock(); s.key = key; s.type = SettingType.SYSTEM; return s

        def _usr(key):
            s = Mock(); s.key = key; s.type = SettingType.USER
            s.get_typed_value.return_value = None
            return s

        system_keys = [
            "registration_policy", "hf_api_key", "civitai_api_key",
            "models_dir", "output_directory", "file_storage_directory",
            "attention_mechanism", "chat_llm_call_tracing",
        ]
        user_keys = ["nsfw_filter", "notification_prefs"]

        mock_setting_repository.get_all_settings.return_value = (
            [_sys(k) for k in system_keys] + [_usr(k) for k in user_keys]
        )
        mock_settings.get_setting.return_value = "secret-or-path"

        response = await controller.get_settings(user)

        assert response.success is True
        for key in system_keys:
            assert key not in response.data, f"SYSTEM setting '{key}' leaked to a regular user"
        for key in user_keys:
            assert key in response.data

    @pytest.mark.asyncio
    async def test_get_all_settings_detailed_hides_system_from_regular_user(
        self, controller, mock_setting_repository
    ):
        """The detailed listing endpoint applies the same SYSTEM/non-admin filter."""
        user = User(
            id="u1", username="u", email="u@example.com",
            password_hash="h", account_type=AccountType.USER,
        )

        from datetime import datetime
        now = datetime(2026, 1, 1, 0, 0, 0)

        sys_setting = Mock()
        sys_setting.id = "s1"; sys_setting.key = "registration_policy"
        sys_setting.type = SettingType.SYSTEM
        sys_setting.value_type = SettingValueType.STRING
        sys_setting.description = None
        sys_setting.get_typed_value.return_value = "closed"
        sys_setting.created_at = now
        sys_setting.updated_at = now

        usr_setting = Mock()
        usr_setting.id = "u1s"; usr_setting.key = "nsfw_filter"
        usr_setting.type = SettingType.USER
        usr_setting.value_type = SettingValueType.BOOLEAN
        usr_setting.description = None
        usr_setting.get_typed_value.return_value = False
        usr_setting.created_at = now
        usr_setting.updated_at = now

        mock_setting_repository.get_all_settings.return_value = [sys_setting, usr_setting]

        response = await controller.get_all_settings_detailed(user=user)

        keys = {row["key"] for row in response.data}
        assert "registration_policy" not in keys
        assert "nsfw_filter" in keys

    @pytest.mark.asyncio
    async def test_get_settings_admin_user(self, controller, mock_settings, mock_setting_repository):
        """Test getting settings for admin user (all settings)"""
        # Create mock admin user
        admin = User(
            id="admin123",
            username="admin",
            email="admin@example.com",
            password_hash="hash",
            account_type=AccountType.ADMIN
        )
        
        # Create mock settings
        user_setting = Mock()
        user_setting.key = "nsfw_filter"
        user_setting.type = SettingType.USER
        user_setting.get_typed_value.return_value = False
        
        system_setting = Mock()
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        
        mock_setting_repository.get_all_settings.return_value = [user_setting, system_setting]
        mock_settings.get_setting.side_effect = lambda key, user_id=None: {
            "nsfw_filter": True,
            "outputs_directory": "/outputs"
        }.get(key)
        
        response = await controller.get_settings(admin)
        
        assert response.success is True
        assert "nsfw_filter" in response.data
        assert "outputs_directory" in response.data  # Admin can see system settings


    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_controller_instantiation(self, controller):
        """Test that controller can be instantiated and has required methods"""
        assert controller is not None
        assert hasattr(controller, 'get_settings')
        assert hasattr(controller, 'update_settings')

    @pytest.mark.asyncio
    async def test_update_setting_system_requires_admin(self, controller, mock_setting_repository):
        """Test updating SYSTEM setting requires admin privileges"""
        # Create mock regular user
        user = User(
            id="user123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type=AccountType.USER
        )
        
        # Create mock system setting
        system_setting = Mock()
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        
        mock_setting_repository.get_setting_by_key.return_value = system_setting
        
        update_data = SettingUpdateRequest(value="/new/outputs")
        
        with pytest.raises(HTTPException) as exc_info:
            await controller.update_setting_by_key("outputs_directory", update_data, user)
        
        assert exc_info.value.status_code == 400  # Wrapped in general error
        assert exc_info.value.detail['error'] == 'setting_update_failed'
        assert 'Admin privileges required' in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_update_setting_system_with_admin(self, controller, mock_setting_repository, mock_settings):
        """Test admin can update SYSTEM settings"""
        # Create mock admin user
        admin = User(
            id="admin123",
            username="admin",
            email="admin@example.com",
            password_hash="hash",
            account_type=AccountType.ADMIN
        )
        
        # Create mock system setting
        system_setting = Mock()
        system_setting.id = "setting123"
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        
        mock_setting_repository.get_setting_by_key.return_value = system_setting
        mock_settings.set_setting.return_value = True
        
        update_data = SettingUpdateRequest(value="/new/outputs", description="New output directory")
        response = await controller.update_setting_by_key("outputs_directory", update_data, admin)
        
        assert response.success is True
        assert "updated successfully" in response.message
        mock_settings.set_setting.assert_called_once_with("outputs_directory", "/new/outputs")
        mock_setting_repository.update_setting.assert_called_once_with("setting123", description="New output directory")

    @pytest.mark.asyncio
    async def test_get_setting_by_key_system_requires_admin(self, controller, mock_setting_repository):
        """Test getting SYSTEM setting by key requires admin"""
        # Create mock regular user
        user = User(
            id="user123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type=AccountType.USER
        )
        
        # Create mock system setting
        system_setting = Mock()
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        
        mock_setting_repository.get_setting_by_key.return_value = system_setting
        
        with pytest.raises(HTTPException) as exc_info:
            await controller.get_setting_by_key("outputs_directory", user)
        
        assert exc_info.value.status_code == 400  # Wrapped in general error
        assert exc_info.value.detail['error'] == 'setting_get_failed'
        assert 'Admin privileges required' in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_update_settings_bulk_requires_authentication(self, controller):
        """Test bulk update requires authentication"""
        settings = {"some_setting": "value"}
        
        with pytest.raises(HTTPException) as exc_info:
            await controller.update_settings(settings)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == 'settings_update_failed'
        assert 'Authentication required' in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_update_settings_bulk_rejects_whole_batch_on_permission_error(
        self, controller, mock_setting_repository
    ):
        """A batch mixing a forbidden SYSTEM setting with an allowed USER setting is
        rejected WHOLE - nothing is written (the transactional all-or-nothing rule).

        Regression: this endpoint used to apply key-by-key, so the user setting
        was committed even though the batch also asked for an unauthorized system
        change - a partial write.
        """
        user = User(
            id="user123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type=AccountType.USER
        )

        system_setting = Mock()
        system_setting.id = "sys-1"
        system_setting.key = "outputs_directory"
        system_setting.type = SettingType.SYSTEM
        system_setting.value_type = SettingValueType.STRING

        user_setting = Mock()
        user_setting.id = "usr-1"
        user_setting.key = "nsfw_filter"
        user_setting.type = SettingType.USER
        user_setting.value_type = SettingValueType.BOOLEAN

        mock_setting_repository.get_setting_by_key.side_effect = lambda key: {
            "outputs_directory": system_setting,
            "nsfw_filter": user_setting
        }.get(key)

        settings = {"outputs_directory": "/new/path", "nsfw_filter": True}

        with pytest.raises(HTTPException) as exc_info:
            await controller.update_settings(settings, user)

        assert exc_info.value.status_code == 400
        # No writes at all: the transactional apply is never reached.
        mock_setting_repository.apply_bulk_updates.assert_not_called()
        assert 'Admin privileges required' in exc_info.value.detail['message']
        assert 'No settings were updated' in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_update_settings_bulk_unknown_key_rejects_whole_batch(
        self, controller, mock_setting_repository
    ):
        """One unknown key rejects the whole batch - a valid sibling is not written."""
        admin = User(
            id="admin1", username="a", email="a@example.com",
            password_hash="h", account_type=AccountType.ADMIN,
        )

        known = Mock()
        known.id = "sys-1"
        known.key = "models_dir"
        known.type = SettingType.SYSTEM
        known.value_type = SettingValueType.STRING

        mock_setting_repository.get_setting_by_key.side_effect = lambda key: {
            "models_dir": known
        }.get(key)  # "bogus" -> None

        with pytest.raises(HTTPException) as exc_info:
            await controller.update_settings({"models_dir": "/m", "bogus": "x"}, admin)

        assert exc_info.value.status_code == 400
        mock_setting_repository.apply_bulk_updates.assert_not_called()
        assert "not found" in exc_info.value.detail['message']

    @pytest.mark.asyncio
    async def test_update_settings_bulk_admin_applies_in_one_transaction(
        self, controller, mock_setting_repository
    ):
        """A fully-valid admin batch is applied via a single apply_bulk_updates call,
        with the system and user writes routed to the right buckets."""
        admin = User(
            id="admin1", username="a", email="a@example.com",
            password_hash="h", account_type=AccountType.ADMIN,
        )

        system_setting = Mock()
        system_setting.id = "sys-1"
        system_setting.key = "models_dir"
        system_setting.type = SettingType.SYSTEM
        system_setting.value_type = SettingValueType.STRING

        user_setting = Mock()
        user_setting.id = "usr-1"
        user_setting.key = "nsfw_filter"
        user_setting.type = SettingType.USER
        user_setting.value_type = SettingValueType.BOOLEAN

        mock_setting_repository.get_setting_by_key.side_effect = lambda key: {
            "models_dir": system_setting,
            "nsfw_filter": user_setting
        }.get(key)

        response = await controller.update_settings(
            {"models_dir": "/m", "nsfw_filter": True}, admin
        )

        assert response.success is True
        mock_setting_repository.apply_bulk_updates.assert_called_once()
        system_updates, user_updates = mock_setting_repository.apply_bulk_updates.call_args[0]
        assert system_updates == [("sys-1", "/m")]
        assert user_updates == [("admin1", "usr-1", "true")]

    @pytest.mark.asyncio
    async def test_update_settings_bulk_surfaces_transaction_failure(
        self, controller, mock_setting_repository
    ):
        """If the transaction raises, the endpoint returns an error (the repository
        guarantees the batch rolled back)."""
        admin = User(
            id="admin1", username="a", email="a@example.com",
            password_hash="h", account_type=AccountType.ADMIN,
        )
        setting = Mock()
        setting.id = "sys-1"
        setting.key = "models_dir"
        setting.type = SettingType.SYSTEM
        setting.value_type = SettingValueType.STRING
        mock_setting_repository.get_setting_by_key.return_value = setting
        mock_setting_repository.apply_bulk_updates.side_effect = Exception("db down")

        with pytest.raises(HTTPException) as exc_info:
            await controller.update_settings({"models_dir": "/m"}, admin)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail['error'] == 'settings_update_failed'

class TestSettingsSecretMasking:
    """`auth_secret_key` and friends must never leave the settings API in the clear.

    Core settings have no `is_secret` column - unlike a plugin setting, whose
    manifest declares it - so the key name is the only signal. Every read path
    masks; every write path treats the mask as "unchanged" so a settings form
    saved without touching the field cannot overwrite the credential with three
    asterisks (which, for `auth_secret_key`, invalidates every session at once).
    """

    @pytest.fixture
    def controller(self):
        return SettingsController(
            settings=Mock(spec=Settings),
            setting_repository=Mock(spec=SettingRepository),
            model_directories=Mock(spec=ModelDirectories),
            gpu_monitor=Mock(spec=GpuMonitor),
            backend_registry=MagicMock(spec=BackendRegistry),
        )

    @staticmethod
    def _admin():
        return User(
            id="admin1", username="a", email="a@example.com",
            password_hash="h", account_type=AccountType.ADMIN,
        )

    @staticmethod
    def _setting(key, value_type=SettingValueType.STRING, typed_value="stored"):
        setting = Mock()
        setting.id = f"id-{key}"
        setting.key = key
        setting.type = SettingType.SYSTEM
        setting.value_type = value_type
        setting.description = None
        setting.created_at = datetime(2026, 8, 13, 12, 0, 0)
        setting.updated_at = datetime(2026, 8, 13, 12, 0, 0)
        setting.get_typed_value.return_value = typed_value
        return setting

    @pytest.mark.asyncio
    async def test_get_settings_masks_the_auth_secret_key(self, controller):
        controller.setting_repository.get_all_settings.return_value = [
            self._setting("auth_secret_key"),
            self._setting("models_dir"),
        ]
        controller.settings.get_setting.side_effect = (
            lambda key, *a, **kw: "jwt-signing-key-do-not-leak"
            if key == "auth_secret_key" else "/data/models"
        )

        response = await controller.get_settings(self._admin())

        assert response.success is True
        assert response.data["auth_secret_key"] == "***"
        assert "jwt-signing-key-do-not-leak" not in repr(response.data)
        # A non-credential setting is untouched - masking must not eat the config.
        assert response.data["models_dir"] == "/data/models"

    @pytest.mark.asyncio
    async def test_get_settings_masks_provider_api_keys(self, controller):
        controller.setting_repository.get_all_settings.return_value = [
            self._setting("civitai_api_key"),
            self._setting("hf_api_key"),
        ]
        controller.settings.get_setting.return_value = "sk-live-do-not-leak"

        response = await controller.get_settings(self._admin())

        assert "sk-live-do-not-leak" not in repr(response.data)
        assert response.data == {"civitai_api_key": "***", "hf_api_key": "***"}

    @pytest.mark.asyncio
    async def test_an_unset_credential_reads_back_as_unset_not_as_configured(
        self, controller
    ):
        """The admin UI's only "is this configured?" signal is the value itself."""
        controller.setting_repository.get_all_settings.return_value = [
            self._setting("auth_secret_key"),
        ]
        controller.settings.get_setting.return_value = ""

        response = await controller.get_settings(self._admin())

        assert response.data["auth_secret_key"] == ""

    @pytest.mark.asyncio
    async def test_detailed_listing_masks_credentials(self, controller):
        controller.setting_repository.get_all_settings.return_value = [
            self._setting("auth_secret_key", typed_value="jwt-key-do-not-leak"),
            self._setting("models_dir", typed_value="/data/models"),
        ]

        response = await controller.get_all_settings_detailed(None, self._admin())

        assert "jwt-key-do-not-leak" not in repr(response.data)
        by_key = {row["key"]: row["value"] for row in response.data}
        assert by_key["auth_secret_key"] == "***"
        assert by_key["models_dir"] == "/data/models"

    @pytest.mark.asyncio
    async def test_get_setting_by_key_masks_credentials(self, controller):
        controller.setting_repository.get_setting_by_key.return_value = self._setting(
            "auth_secret_key"
        )
        controller.settings.get_setting.return_value = "jwt-key-do-not-leak"

        response = await controller.get_setting_by_key("auth_secret_key", self._admin())

        assert response.data["value"] == "***"
        assert "jwt-key-do-not-leak" not in repr(response.data)

    @pytest.mark.asyncio
    async def test_saving_the_mask_back_does_not_overwrite_the_credential(
        self, controller
    ):
        """A settings form round-trips what it was given. Writing the mask through
        would replace the JWT secret with '***' and log everyone out."""
        controller.setting_repository.get_setting_by_key.side_effect = (
            lambda key: self._setting(key)
        )

        response = await controller.update_settings(
            {"auth_secret_key": "***", "models_dir": "/data/models"}, self._admin()
        )

        assert response.success is True
        system_updates, _user_updates = (
            controller.setting_repository.apply_bulk_updates.call_args[0]
        )
        written_keys = {setting_id for setting_id, _value in system_updates}
        assert "id-auth_secret_key" not in written_keys
        assert system_updates == [("id-models_dir", "/data/models")]

    @pytest.mark.asyncio
    async def test_a_real_new_credential_is_still_written(self, controller):
        """The guard must skip the mask, not every write to a credential."""
        controller.setting_repository.get_setting_by_key.side_effect = (
            lambda key: self._setting(key)
        )

        await controller.update_settings(
            {"auth_secret_key": "a-genuinely-new-key"}, self._admin()
        )

        system_updates, _ = controller.setting_repository.apply_bulk_updates.call_args[0]
        assert system_updates == [("id-auth_secret_key", "a-genuinely-new-key")]

    @pytest.mark.asyncio
    async def test_single_key_update_ignores_the_mask(self, controller):
        controller.setting_repository.get_setting_by_key.return_value = self._setting(
            "auth_secret_key"
        )

        response = await controller.update_setting_by_key(
            "auth_secret_key", SettingUpdateRequest(value="***"), self._admin()
        )

        assert response.success is True
        controller.settings.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_key_update_writes_a_real_value(self, controller):
        controller.setting_repository.get_setting_by_key.return_value = self._setting(
            "auth_secret_key"
        )
        controller.settings.set_setting.return_value = True

        await controller.update_setting_by_key(
            "auth_secret_key", SettingUpdateRequest(value="a-new-key"), self._admin()
        )

        controller.settings.set_setting.assert_called_once_with(
            "auth_secret_key", "a-new-key"
        )

    @pytest.mark.asyncio
    async def test_the_mask_is_not_special_for_ordinary_settings(self, controller):
        """'***' is only a sentinel in a credential field; elsewhere it is a value."""
        controller.setting_repository.get_setting_by_key.side_effect = (
            lambda key: self._setting(key)
        )

        await controller.update_settings({"models_dir": "***"}, self._admin())

        system_updates, _ = controller.setting_repository.apply_bulk_updates.call_args[0]
        assert system_updates == [("id-models_dir", "***")]
