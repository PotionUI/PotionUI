"""The settings controller runs the retention validator, so a rejected window
never reaches the settings table."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.features.settings.routes import SettingsController
from src.platform.security.user import AccountType, User
from src.platform.settings.records import SettingType, SettingValueType


def _controller():
    setting_repository = Mock()
    setting_repository.get_setting_by_key.return_value = Mock(
        id="s1", type=SettingType.SYSTEM, value_type=SettingValueType.INTEGER
    )
    controller = SettingsController(
        settings=Mock(),
        setting_repository=setting_repository,
        model_directories=Mock(),
        gpu_monitor=Mock(),
        backend_registry=Mock(),
    )
    return controller, setting_repository


def _admin():
    return User(
        id="admin", username="admin", email="a@example.com",
        password_hash="h", account_type=AccountType.ADMIN,
    )


class TestRetentionValidationHook:

    async def test_a_window_past_the_ceiling_is_rejected_and_nothing_is_written(self):
        controller, setting_repository = _controller()

        with pytest.raises(HTTPException) as raised:
            await controller.update_settings({"tmp_retention_days": 99999}, _admin())

        assert raised.value.status_code == 400
        assert "tmp_retention_days" in raised.value.detail["message"]
        setting_repository.apply_bulk_updates.assert_not_called()

    async def test_a_negative_window_is_rejected(self):
        controller, _ = _controller()

        with pytest.raises(HTTPException):
            await controller.update_settings({"llm_trace_retention_days": -1}, _admin())

    async def test_a_window_inside_the_range_passes_the_hook(self):
        controller, setting_repository = _controller()

        await controller.update_settings({"run_report_retention_days": 14}, _admin())

        setting_repository.apply_bulk_updates.assert_called_once_with([("s1", "14")], [])
