from unittest.mock import MagicMock, Mock

import pytest
from fastapi import HTTPException

from src.features.backends.backend_registry import BackendRegistry
from src.features.settings.routes import SettingsController
from src.platform.runtime.gpu import GpuMonitor
from src.platform.security.user import AccountType, User
from src.platform.settings.records import SettingType, SettingValueType
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings

_KNOWN = {
    "content_policy_nsfw": SettingValueType.STRING,
    "content_banned_words": SettingValueType.JSON,
    "content_video_sample_frames": SettingValueType.INTEGER,
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
def controller(repository):
    return SettingsController(
        settings=Mock(spec=Settings),
        setting_repository=repository,
        gpu_monitor=Mock(spec=GpuMonitor),
        backend_registry=MagicMock(spec=BackendRegistry),
    )


@pytest.fixture
def admin():
    return User(
        id="admin1", username="a", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )


@pytest.mark.asyncio
async def test_a_valid_policy_batch_is_written(controller, repository, admin):
    response = await controller.update_settings({
        "content_policy_nsfw": "blur",
        "content_banned_words": ["nud*", "gore"],
        "content_video_sample_frames": 6,
    }, admin)

    assert response.success is True
    system_updates, _ = repository.apply_bulk_updates.call_args[0]
    assert dict(system_updates) == {
        "id-content_policy_nsfw": "blur",
        "id-content_banned_words": '["nud*", "gore"]',
        "id-content_video_sample_frames": "6",
    }


@pytest.mark.parametrize("key,value", [
    ("content_policy_nsfw", "sometimes"),
    ("content_policy_nsfw", True),
    ("content_banned_words", "nude, gore"),
    ("content_banned_words", ["ok", 5]),
    ("content_banned_words", ["*"]),
    ("content_video_sample_frames", 0),
    ("content_video_sample_frames", 9),
    ("content_video_sample_frames", True),
])
@pytest.mark.asyncio
async def test_a_rejected_value_writes_nothing(controller, repository, admin, key, value):
    with pytest.raises(HTTPException) as exc:
        await controller.update_settings({key: value}, admin)

    assert exc.value.status_code == 400
    assert key in exc.value.detail["message"]
    repository.apply_bulk_updates.assert_not_called()
