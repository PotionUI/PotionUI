from types import SimpleNamespace

from src.features.presets.linter import PresetLinter
from src.pipelines.contracts import PipeConfigSpec
from tests.features.presets.test_preset_extensions import PRESET_ID, _plugin, _write_preset


class _Generator:
    @classmethod
    def configuration(cls):
        return [PipeConfigSpec("steps", int, 8), PipeConfigSpec("strength", float, 0.5)]

    @classmethod
    def inputs(cls):
        return None

    @classmethod
    def outputs(cls):
        return None


def _catalog(known=("loader", "generator", "gallery", "refiner")):
    return SimpleNamespace(
        get_pipe_source=lambda name: "core" if name in known else None,
        get_pipe=lambda name: _Generator if name in known else None,
    )


def _extension_issues(tmp_path, manifests):
    root = tmp_path / "presets"
    _write_preset(root)
    issues = PresetLinter([str(root)], plugin_manifests=manifests, pipe_catalog=_catalog()).lint()
    return [i for i in issues if "preset_extensions" in i.preset_path]


def test_clean_extension_has_no_issues(tmp_path):
    ext = {"pipes": [
        {"add": {"name": "refiner", "id": "refiner", "configuration": {"steps": 2}}, "after": "generator"},
        {"patch": "generator", "configuration_add": {"strength": 0.5}},
    ]}
    assert _extension_issues(tmp_path, [_plugin(tmp_path, "ext-a", ext)]) == []


def test_rejected_extension_is_a_lint_error_naming_the_plugin(tmp_path):
    ext = {"fields": [{"into": {"tab": "nope"}, "fields": [{"name": "x", "type": "checkbox"}]}]}
    issues = _extension_issues(tmp_path, [_plugin(tmp_path, "ext-bad", ext)])
    assert [i.level for i in issues] == ["error", "error"]
    assert all("plugin 'ext-bad'" in i.preset_path and PRESET_ID in i.preset_path for i in issues)
    assert "no tab with anchor 'nope'" in issues[0].message


def test_added_pipe_missing_from_catalog_is_an_error(tmp_path):
    ext = {"pipes": [{"add": {"name": "no_such_pipe", "id": "extra"}}]}
    issues = _extension_issues(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    assert [i.level for i in issues] == ["error"]
    assert "'no_such_pipe', which is not in the pipe catalog" in issues[0].message


def test_undeclared_configuration_keys_are_warnings(tmp_path):
    ext = {"pipes": [
        {"add": {"name": "refiner", "id": "refiner", "configuration": {"bogus_add": 1}}},
        {"patch": "generator", "configuration_add": {"bogus_patch": 1}},
    ]}
    issues = _extension_issues(tmp_path, [_plugin(tmp_path, "ext-a", ext)])
    assert [i.level for i in issues] == ["warning", "warning"]
    assert "'refiner' (refiner)" in issues[0].message and "['bogus_add']" in issues[0].message
    assert "'generator' (generator)" in issues[1].message and "['bogus_patch']" in issues[1].message


def test_no_extensions_means_no_extra_load(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    _write_preset(root)
    calls = []
    monkeypatch.setattr("src.features.presets.loader.PresetTemplateLoader.load_presets", lambda self: calls.append(1))
    linter = PresetLinter([str(root)], plugin_manifests=[SimpleNamespace(id="p", plugin_dir=tmp_path)], pipe_catalog=_catalog())
    assert linter._lint_preset_extensions() == []
    assert calls == []
