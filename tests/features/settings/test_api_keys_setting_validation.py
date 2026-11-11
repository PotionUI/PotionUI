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
    "api_keys_enabled": SettingValueType.BOOLEAN,
    "api_key_scopes_allowed": SettingValueType.JSON,
    "api_key_max_lifetime_days": SettingValueType.INTEGER,
    "mcp_tools_ceiling_policy": SettingValueType.STRING,
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
async def test_a_valid_batch_is_written(controller, repository, admin):
    response = await controller.update_settings({
        "api_keys_enabled": False,
        "api_key_scopes_allowed": ["read", "admin"],
        "api_key_max_lifetime_days": 90,
        "mcp_tools_ceiling_policy": "read_only_on",
    }, admin)

    assert response.success is True
    system_updates, _ = repository.apply_bulk_updates.call_args[0]
    assert dict(system_updates)["id-api_key_scopes_allowed"] == '["read", "admin"]'
    assert dict(system_updates)["id-api_key_max_lifetime_days"] == "90"


@pytest.mark.parametrize("key,value", [
    ("api_keys_enabled", "maybe"),
    ("api_key_scopes_allowed", ["read", "root"]),
    ("api_key_scopes_allowed", "read"),
    ("api_key_max_lifetime_days", -1),
    ("api_key_max_lifetime_days", 5000),
    ("mcp_tools_ceiling_policy", "some_on"),
])
@pytest.mark.asyncio
async def test_a_rejected_value_writes_nothing(controller, repository, admin, key, value):
    with pytest.raises(HTTPException) as exc:
        await controller.update_settings({key: value}, admin)

    assert exc.value.status_code == 400
    assert key in exc.value.detail["message"]
    repository.apply_bulk_updates.assert_not_called()
