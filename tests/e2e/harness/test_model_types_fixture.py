from __future__ import annotations

import os
import sys
from pathlib import Path

_HARNESS_DIR = Path(__file__).resolve().parent
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import model_types_fixture

from src.platform.plugins.loader import EXTRA_PLUGIN_DIRS_ENV, PluginLoader


def test_only_plugin_model_type_specs_ask_for_the_fixture():
    assert model_types_fixture.wants_model_types(["tests/e2e/plugin-model-type.spec.ts"])
    assert not model_types_fixture.wants_model_types(["model-roots.spec.ts", "cloud-fake-generate.spec.ts"])


def test_the_extra_plugin_directories_of_both_fixtures_are_joined():
    merged = model_types_fixture.merge_env(
        {EXTRA_PLUGIN_DIRS_ENV: "/a"}, None, model_types_fixture.plugin_env(), {"OTHER": "1"}
    )

    assert merged == {EXTRA_PLUGIN_DIRS_ENV: os.pathsep.join(["/a", str(model_types_fixture.PLUGINS_ROOT)]), "OTHER": "1"}
    assert model_types_fixture.merge_env(None, None) is None


def test_the_fixture_plugin_declares_a_valid_model_type(monkeypatch, tmp_path):
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, str(model_types_fixture.PLUGINS_ROOT))

    (manifest,) = PluginLoader(str(tmp_path / "none"), str(tmp_path / "none")).discover_plugins()

    assert manifest.id == model_types_fixture.PLUGIN_ID
    assert manifest.validation_error in (None, "")
    assert [(t["id"], t["folder"]) for t in manifest.model_types] == [
        (model_types_fixture.MODEL_TYPE, model_types_fixture.FOLDER)
    ]
