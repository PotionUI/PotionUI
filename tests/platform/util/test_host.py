import importlib
import sys

import pytest

from src.platform.util import host


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", True), ("linux", False), ("darwin", False), ("cygwin", False)],
)
def test_is_windows_is_true_only_on_the_win32_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)
    assert host.is_windows() is expected


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("src.platform.filesystem.model_roots", "is_windows"),
        ("src.features.settings.app_lifecycle", "is_windows"),
        ("src.features.models.symlink_adoption", "is_windows"),
        ("src.platform.runtime.nvml_library", "_running_on_windows"),
        ("src.platform.filesystem.storage_driver", "is_windows"),
        ("src.platform.util.process", "is_windows"),
        ("src.platform.runtime.native.optimizations.installer", "is_windows"),
        ("src.features.models.roots_routes", "is_windows"),
        ("scripts.potionui_cli", "is_windows"),
    ],
)
def test_every_platform_check_name_is_the_shared_function(module, name):
    assert getattr(importlib.import_module(module), name) is host.is_windows
