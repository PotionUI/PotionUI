from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import HTTPException

from src.features.api_keys.records import UserApiAccess
from src.features.backends.backend_registry import BackendRegistry
from src.features.settings.dto import SettingUpdateRequest
from src.features.settings.legacy_mcp_switch import LegacyMcpUserSwitch
from src.features.settings.routes import SettingsController
from src.platform.runtime.gpu import GpuMonitor
from src.platform.security.principal import KeyGrant
from src.platform.security.user import AccountType, User
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings


class MemoryAccess:
    def __init__(self):
        self.rows = {}
        self.saved_by = {}

    def get(self, user_id):
        return self.rows.get(user_id) or UserApiAccess(user_id=user_id)

    def save(self, access, updated_at, updated_by):
        self.rows[access.user_id] = access
        self.saved_by[access.user_id] = updated_by


@pytest.fixture
def access():
    return MemoryAccess()


@pytest.fixture
def repository():
    repo = Mock(spec=SettingRepository)
    repo.get_setting_by_key.return_value = None
    repo.get_all_settings.return_value = []
    repo.get_user_settings.return_value = []
    return repo


@pytest.fixture
def controller(repository, access):
    return SettingsController(
        settings=Mock(spec=Settings),
        setting_repository=repository,
        gpu_monitor=Mock(spec=GpuMonitor),
        backend_registry=MagicMock(spec=BackendRegistry),
        legacy_mcp=LegacyMcpUserSwitch(access),
    )


def person(user_id, account_type):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=account_type)


@pytest.fixture
def admin():
    return person("admin1", AccountType.ADMIN)


@pytest.fixture
def member():
    return person("user1", AccountType.USER)


def payload(response):
    return response.model_dump() if hasattr(response, "model_dump") else response.dict()


@pytest.mark.asyncio
async def test_get_settings_lists_the_old_key_as_true_by_default(controller, member):
    response = await controller.get_settings(member)

    assert payload(response)["data"]["mcp_user_enabled"] is True


@pytest.mark.asyncio
async def test_get_settings_reads_the_new_value(controller, access, member):
    access.rows[member.id] = UserApiAccess(user_id=member.id, mcp_enabled=False)

    response = await controller.get_settings(member)

    assert payload(response)["data"]["mcp_user_enabled"] is False


@pytest.mark.asyncio
async def test_get_by_key_reads_the_new_value_in_the_v010_shape(controller, access, member):
    access.rows[member.id] = UserApiAccess(user_id=member.id, mcp_enabled=False)

    data = payload(await controller.get_setting_by_key("mcp_user_enabled", member))["data"]

    assert data["key"] == "mcp_user_enabled"
    assert data["value"] is False
    assert data["value_type"] == "boolean"
    assert data["type"] == "USER"
    assert "MCP" in data["description"]


@pytest.mark.asyncio
async def test_detailed_listing_includes_the_old_key_for_a_user(controller, member):
    data = payload(await controller.get_all_settings_detailed(None, member))["data"]

    entry = next(item for item in data if item["key"] == "mcp_user_enabled")
    assert entry["value"] is True
    assert entry["type"] == "USER"


@pytest.mark.asyncio
async def test_detailed_listing_filtered_to_system_leaves_the_old_key_out(controller, member):
    data = payload(await controller.get_all_settings_detailed("SYSTEM", member))["data"]

    assert all(item["key"] != "mcp_user_enabled" for item in data)


@pytest.mark.asyncio
async def test_user_overrides_list_the_old_key_only_when_it_is_off(controller, access, member):
    assert payload(await controller.get_user_settings(member))["data"] == []

    access.rows[member.id] = UserApiAccess(user_id=member.id, mcp_enabled=False)
    data = payload(await controller.get_user_settings(member))["data"]

    assert [(item["setting_key"], item["value"]) for item in data] == [("mcp_user_enabled", False)]


@pytest.mark.asyncio
async def test_an_admin_write_by_key_lands_on_the_new_value_and_keeps_the_key_switch(controller, access, admin):
    access.rows[admin.id] = UserApiAccess(user_id=admin.id, keys_enabled=False, mcp_enabled=True)

    response = await controller.update_setting_by_key("mcp_user_enabled", SettingUpdateRequest(value=False), admin)

    assert payload(response)["success"] is True
    assert access.rows[admin.id] == UserApiAccess(user_id=admin.id, keys_enabled=False, mcp_enabled=False)
    assert access.saved_by[admin.id] == admin.id


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [("true", True), ("false", False), (1, True), (0, False), (True, True)])
async def test_an_admin_write_accepts_the_v010_value_forms(controller, access, admin, raw, expected):
    await controller.update_setting_by_key("mcp_user_enabled", SettingUpdateRequest(value=raw), admin)

    assert access.get(admin.id).mcp_enabled is expected


@pytest.mark.asyncio
async def test_an_unreadable_value_is_rejected_and_writes_nothing(controller, access, admin):
    with pytest.raises(HTTPException) as refused:
        await controller.update_setting_by_key("mcp_user_enabled", SettingUpdateRequest(value="maybe"), admin)

    assert refused.value.status_code == 400
    assert access.rows == {}


@pytest.mark.asyncio
async def test_a_non_admin_cannot_write_the_old_key_by_key(controller, access, member):
    with pytest.raises(HTTPException) as refused:
        await controller.update_setting_by_key("mcp_user_enabled", SettingUpdateRequest(value=True), member)

    assert refused.value.status_code == 403
    assert access.rows == {}


@pytest.mark.asyncio
async def test_a_non_admin_cannot_write_the_old_key_in_a_batch(controller, access, member):
    with pytest.raises(HTTPException) as refused:
        await controller.update_settings({"mcp_user_enabled": True}, member)

    assert refused.value.status_code == 403
    assert access.rows == {}


@pytest.mark.asyncio
async def test_an_admin_batch_write_lands_on_the_new_value(controller, access, admin):
    response = await controller.update_settings({"mcp_user_enabled": False}, admin)

    assert payload(response)["success"] is True
    assert access.get(admin.id).mcp_enabled is False


@pytest.mark.asyncio
async def test_a_batch_with_a_bad_old_key_value_writes_nothing(controller, repository, access, admin):
    with pytest.raises(HTTPException) as refused:
        await controller.update_settings({"mcp_user_enabled": "maybe"}, admin)

    assert refused.value.status_code == 400
    assert access.rows == {}
    repository.apply_bulk_updates.assert_not_called()


@pytest.mark.asyncio
async def test_a_non_admin_cannot_delete_the_old_key_override(controller, access, member):
    access.rows[member.id] = UserApiAccess(user_id=member.id, mcp_enabled=False)

    with pytest.raises(HTTPException) as refused:
        await controller.delete_user_setting("mcp_user_enabled", member)

    assert refused.value.status_code == 403
    assert access.get(member.id).mcp_enabled is False


@pytest.mark.asyncio
async def test_an_admin_delete_resets_the_old_key_to_on(controller, access, admin):
    access.rows[admin.id] = UserApiAccess(user_id=admin.id, mcp_enabled=False)

    await controller.delete_user_setting("mcp_user_enabled", admin)

    assert access.get(admin.id).mcp_enabled is True


@pytest.mark.asyncio
async def test_an_api_key_cannot_write_the_old_key(controller, access, admin):
    grant = KeyGrant(key_id="k", user_id=admin.id, scopes=frozenset(), display_prefix="pui_", expires_at=None)
    keyed = replace(admin, key=grant)

    with pytest.raises(HTTPException) as refused:
        await controller.update_setting_by_key("mcp_user_enabled", SettingUpdateRequest(value=True), keyed)

    assert refused.value.status_code == 403
    assert access.rows == {}
