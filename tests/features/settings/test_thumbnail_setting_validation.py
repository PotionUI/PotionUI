"""The thumbnail settings are range-checked before they are written.

An out-of-range frame rate or an empty size list would otherwise be stored
and then silently corrected on every read, so the admin would see a value
that is not the one actually in force.
"""

from unittest.mock import MagicMock, Mock

import pytest
from fastapi import HTTPException

from src.features.backends.backend_registry import BackendRegistry
from src.features.models.directory import ModelDirectories
from src.features.settings.dto import SettingUpdateRequest
from src.features.settings.routes import SettingsController
from src.platform.runtime.gpu import GpuMonitor
from src.platform.security.user import AccountType, User
from src.platform.settings.records import SettingType, SettingValueType
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

_KNOWN = {
    "thumbnail_sizes": SettingValueType.JSON,
    "thumbnail_video_fps": SettingValueType.INTEGER,
    "thumbnail_video_seconds": SettingValueType.INTEGER,
    "thumbnail_video_quality": SettingValueType.INTEGER,
    "thumbnail_image_quality": SettingValueType.INTEGER,
    "models_dir": SettingValueType.STRING,
}


@pytest.fixture
def repository():
    repo = Mock(spec=SettingRepository)

    def lookup(key):
        if key not in _KNOWN:
            return None
        setting = Mock()
        setting.id = f"id-{key}"
        setting.key = key
        setting.type = SettingType.SYSTEM
        setting.value_type = _KNOWN[key]
        return setting

    repo.get_setting_by_key.side_effect = lookup
    return repo


@pytest.fixture
def settings():
    return Mock(spec=Settings)


@pytest.fixture
def controller(repository, settings):
    backend_registry = MagicMock(spec=BackendRegistry)
    return SettingsController(
        settings=settings,
        setting_repository=repository,
        model_directories=Mock(spec=ModelDirectories),
        gpu_monitor=Mock(spec=GpuMonitor),
        backend_registry=backend_registry,
    )


@pytest.fixture
def admin():
    return User(
        id="admin1", username="a", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )


class TestBatchUpdateValidation:

    @pytest.mark.asyncio
    async def test_a_valid_batch_round_trips_a_list_and_four_integers(
        self, controller, repository, admin
    ):
        response = await controller.update_settings({
            "thumbnail_sizes": ["small", "large"],
            "thumbnail_video_fps": 24,
            "thumbnail_video_seconds": 2,
            "thumbnail_video_quality": 40,
            "thumbnail_image_quality": 75,
        }, admin)

        assert response.success is True
        system_updates, _ = repository.apply_bulk_updates.call_args[0]
        assert dict(system_updates) == {
            "id-thumbnail_sizes": '["small", "large"]',
            "id-thumbnail_video_fps": "24",
            "id-thumbnail_video_seconds": "2",
            "id-thumbnail_video_quality": "40",
            "id-thumbnail_image_quality": "75",
        }

    @pytest.mark.parametrize("key,value", [
        ("thumbnail_sizes", []),
        ("thumbnail_sizes", ["enormous"]),
        ("thumbnail_sizes", "not a list"),
        ("thumbnail_video_fps", 0),
        ("thumbnail_video_fps", 61),
        ("thumbnail_video_seconds", 0),
        ("thumbnail_video_seconds", 11),
        ("thumbnail_video_quality", 0),
        ("thumbnail_video_quality", 101),
        ("thumbnail_image_quality", 0),
        ("thumbnail_image_quality", 101),
    ])
    @pytest.mark.asyncio
    async def test_a_rejected_value_writes_nothing(
        self, controller, repository, admin, key, value
    ):
        with pytest.raises(HTTPException) as exc:
            await controller.update_settings({key: value, "models_dir": "/m"}, admin)

        # The controller's outer handler re-wraps the rejection, so the
        # reason arrives nested in the message - the shape every rejected
        # batch already has.
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "settings_update_failed"
        assert key in exc.value.detail["message"]
        assert "No settings were updated" in exc.value.detail["message"]
        repository.apply_bulk_updates.assert_not_called()


class TestSingleKeyUpdateValidation:

    @pytest.mark.asyncio
    async def test_a_valid_single_write_is_applied(self, controller, settings, admin):
        settings.set_setting.return_value = True

        response = await controller.update_setting_by_key(
            "thumbnail_video_fps", SettingUpdateRequest(value=30), admin
        )

        assert response.success is True
        settings.set_setting.assert_called_once_with("thumbnail_video_fps", 30)

    @pytest.mark.asyncio
    async def test_an_out_of_range_single_write_is_refused(self, controller, settings, admin):
        with pytest.raises(HTTPException) as exc:
            await controller.update_setting_by_key(
                "thumbnail_video_fps", SettingUpdateRequest(value=120), admin
            )

        assert exc.value.status_code == 400
        assert "thumbnail_video_fps" in exc.value.detail["message"]
        settings.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_empty_size_list_is_refused(self, controller, settings, admin):
        with pytest.raises(HTTPException):
            await controller.update_setting_by_key(
                "thumbnail_sizes", SettingUpdateRequest(value=[]), admin
            )

        settings.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unrelated_setting_is_untouched_by_this_check(
        self, controller, settings, admin
    ):
        settings.set_setting.return_value = True

        response = await controller.update_setting_by_key(
            "models_dir", SettingUpdateRequest(value="/models"), admin
        )

        assert response.success is True
