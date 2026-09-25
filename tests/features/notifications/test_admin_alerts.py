from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.notifications.admin_alerts import (
    CATEGORIES_SETTING,
    ENABLED_SETTING,
    NOTIFICATION_TYPE,
    GenerationFailureAdminAlerts,
)
from src.features.notifications.types import notification_type_registry
from src.platform.plugins.hooks import HookChain
from src.platform.security.user import AccountType

PAYLOAD = {
    "generation_id": "gen_1",
    "user_id": "user_1",
    "preset_id": "sdxl/base",
    "error_code": "cuda_oom",
    "category": "cuda_oom",
    "message": "Ran out of GPU memory (VRAM) during generation.",
    "failed_pipe": "generator",
}


def _settings(enabled, categories=None):
    values = {ENABLED_SETTING: enabled, CATEGORIES_SETTING: categories if categories is not None else []}
    settings = Mock()
    settings.get_setting = Mock(side_effect=lambda key, default=None, **_: values.get(key, default))
    return settings


def _users():
    users = Mock()
    users.get_all = Mock(return_value=[
        SimpleNamespace(id="admin_1", account_type=AccountType.ADMIN),
        SimpleNamespace(id="user_1", account_type=AccountType.USER),
        SimpleNamespace(id="admin_2", account_type=AccountType.ADMIN),
    ])
    return users


def _run(settings, payload=None):
    notify = Mock()
    chain = HookChain()
    chain.register("generation.failed", "core.admin_failure_alerts", GenerationFailureAdminAlerts(settings, _users(), notify))
    chain.execute("generation.failed", initial_data=dict(payload or PAYLOAD))
    return notify


def test_disabled_setting_sends_nothing():
    assert _run(_settings(False)).call_count == 0


def test_enabled_notifies_each_admin_once_and_no_one_else():
    notify = _run(_settings(True))

    assert sorted(c.kwargs["user_id"] for c in notify.call_args_list) == ["admin_1", "admin_2"]


def test_notification_carries_the_safe_message_and_a_link_to_the_failure():
    notify = _run(_settings(True))

    kwargs = notify.call_args_list[0].kwargs
    assert kwargs["level"] == "error"
    assert kwargs["type"] == NOTIFICATION_TYPE
    assert kwargs["message"] == "Generation failed: Ran out of GPU memory (VRAM) during generation."
    assert kwargs["metadata"]["link"] == "/admin?tab=generations&id=gen_1"
    assert kwargs["metadata"]["generation_id"] == "gen_1"
    assert kwargs["metadata"]["error_code"] == "cuda_oom"


@pytest.mark.parametrize("categories", [["cuda_oom"], ["disk_full", "cuda_oom"], '["cuda_oom"]'])
def test_matching_category_filter_notifies(categories):
    assert _run(_settings(True, categories)).call_count == 2


@pytest.mark.parametrize("categories", [["disk_full"], '["disk_full"]'])
def test_non_matching_category_filter_sends_nothing(categories):
    assert _run(_settings(True, categories)).call_count == 0


def test_a_failing_notify_does_not_break_the_hook_chain():
    notify = Mock(side_effect=RuntimeError("db down"))
    chain = HookChain()
    chain.register("generation.failed", "core.admin_failure_alerts", GenerationFailureAdminAlerts(_settings(True), _users(), notify))

    context, results = chain.execute("generation.failed", initial_data=dict(PAYLOAD))

    assert context.data["generation_id"] == "gen_1"


def test_admin_alert_type_is_registered_admin_only():
    spec = notification_type_registry.get(NOTIFICATION_TYPE)
    assert spec is not None and spec.admin_only
