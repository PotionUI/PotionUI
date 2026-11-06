import logging
from types import SimpleNamespace

import pytest

from src.platform.plugins.hooks import BlockedByPlugin, run_veto_hook


class _Plugins:
    def __init__(self, data):
        self.data = data
        self.seen = []

    def execute_hook(self, hook, initial_data):
        self.seen.append((hook, dict(initial_data)))
        merged = {**initial_data, **self.data}
        return SimpleNamespace(data=merged), []


class _CustomError(Exception):
    pass


def test_unblocked_hook_returns_the_plugin_mutated_data():
    plugins = _Plugins({"name": "changed"})

    result = run_veto_hook(plugins, "x.before", {"name": "orig", "k": 1}, "denied")

    assert result == {"name": "changed", "k": 1}
    assert plugins.seen == [("x.before", {"name": "orig", "k": 1})]


def test_blocked_hook_raises_value_error_subclass_with_the_plugin_reason():
    plugins = _Plugins({"blocked": True, "block_reason": "no way"})

    with pytest.raises(ValueError) as raised:
        run_veto_hook(plugins, "x.before", {}, "denied")

    assert isinstance(raised.value, BlockedByPlugin)
    assert str(raised.value) == "no way"
    assert raised.value.reason == "no way"


def test_blocked_hook_without_reason_uses_the_default():
    plugins = _Plugins({"blocked": True})

    with pytest.raises(BlockedByPlugin) as raised:
        run_veto_hook(plugins, "x.before", {}, "Operation blocked")

    assert str(raised.value) == "Operation blocked"


def test_error_callable_builds_the_raised_exception_from_the_reason():
    plugins = _Plugins({"blocked": True, "block_reason": "stop"})

    with pytest.raises(_CustomError) as raised:
        run_veto_hook(plugins, "x.before", {}, "denied", error=_CustomError)

    assert str(raised.value) == "stop"


def test_a_veto_is_logged_once(caplog):
    plugins = _Plugins({"blocked": True, "block_reason": "stop"})

    with caplog.at_level(logging.WARNING, logger="src.platform.plugins.hooks"):
        with pytest.raises(BlockedByPlugin):
            run_veto_hook(plugins, "x.before", {}, "denied")

    assert [r.getMessage() for r in caplog.records] == ["x.before blocked by plugin: stop"]


def test_block_details_ride_on_the_raised_exception():
    plugins = _Plugins({"blocked": True, "block_reason": "stop", "block_details": {"items": [1]}})

    with pytest.raises(_CustomError) as raised:
        run_veto_hook(plugins, "x.before", {}, "denied", error=_CustomError)

    assert raised.value.block_details == {"items": [1]}


def test_a_block_without_details_leaves_the_exception_bare():
    plugins = _Plugins({"blocked": True, "block_reason": "stop"})

    with pytest.raises(_CustomError) as raised:
        run_veto_hook(plugins, "x.before", {}, "denied", error=_CustomError)

    assert not hasattr(raised.value, "block_details")
