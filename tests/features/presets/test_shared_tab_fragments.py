from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.features.forms.binding import _expand_form_fields
from src.features.presets.form_serializer import PresetFormSerializer
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.templates import FieldTemplate
from src.platform.templating import TemplateProcessor

PRESET_ID = "01AAAAAAAAAAAAAAAAAAAAAAAAA"

TAB_FRAGMENT = "fields:\n  - name: shared_steps\n    type: number\n    default: 4\n"

FORM_WITH = (
    "fields:\n"
    "  - name: tabs\n"
    "    type: tabs\n"
    "    children:\n"
    "      - name: t\n"
    "        type: tab\n"
    "        label: T\n"
    "        children: \"{ref}\"\n"
)


def _write_preset(base: Path, ref: str) -> Path:
    preset_dir = base / "Foo" / "std"
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        "schema: 1\n"
        f'id: "{PRESET_ID}"\n'
        'name: "Test"\n'
        'version: "1.0.0"\n'
        'category: "image"\n'
        'engine: "native"\n'
        "modes:\n"
        "  - txt2img\n"
    )
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text(FORM_WITH.format(ref=ref))
    return preset_dir


def _shared_tree(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    (shared / "x").mkdir(parents=True)
    (shared / "x" / "tab.yml").write_text(TAB_FRAGMENT)
    return shared


def _tab_child_names(preset) -> list:
    form = preset.modes["txt2img"].forms[0]
    tab = form.fields[0].children[0]
    return [c.name for c in tab.children]


def test_core_preset_children_resolve_against_shared_path(tmp_path):
    shared = _shared_tree(tmp_path)
    _write_preset(tmp_path / "presets", "{{ paths._shared }}/x/tab.yml")
    loader = PresetTemplateLoader([str(tmp_path / "presets")], shared_path=shared)
    loader.load_presets()
    assert loader.load_errors == {}
    assert _tab_child_names(loader.presets[0]) == ["shared_steps"]


def test_plugin_root_preset_children_use_the_same_shared_root(tmp_path):
    shared = _shared_tree(tmp_path)
    plugin_dir = tmp_path / "plugin"
    _write_preset(plugin_dir / "presets", "{{ paths._shared }}/x/tab.yml")
    manifest = SimpleNamespace(presets=[{"path": "presets"}], plugin_dir=plugin_dir)
    registry = SimpleNamespace(get_enabled_plugins=lambda: [manifest])
    empty_core = tmp_path / "core"
    empty_core.mkdir()
    loader = PresetTemplateLoader([str(empty_core)], plugin_registry=registry, shared_path=shared)
    loader.load_presets()
    assert loader.load_errors == {}
    assert _tab_child_names(loader.presets[0]) == ["shared_steps"]


def test_preset_token_children_still_resolve_against_the_preset_dir(tmp_path):
    shared = _shared_tree(tmp_path)
    preset_dir = _write_preset(tmp_path / "presets", "{{ paths.preset }}/tabs/local.yml")
    (preset_dir / "tabs").mkdir()
    (preset_dir / "tabs" / "local.yml").write_text(TAB_FRAGMENT.replace("shared_steps", "local_steps"))
    loader = PresetTemplateLoader([str(tmp_path / "presets")], shared_path=shared)
    loader.load_presets()
    assert loader.load_errors == {}
    assert _tab_child_names(loader.presets[0]) == ["local_steps"]


def test_form_serializer_resolves_shared_children_at_serve_time(tmp_path):
    shared = _shared_tree(tmp_path)
    preset_loader = Mock()
    preset_loader.shared_path = shared
    preset_loader.load_preset_by_id.return_value = SimpleNamespace(path=str(tmp_path / "preset"))
    serializer = PresetFormSerializer(preset_loader, TemplateProcessor(settings=None))
    fields = [FieldTemplate(name="t", type="section", children="{{ paths._shared }}/x/tab.yml")]

    schema = serializer.process_form_fields(SimpleNamespace(fields=fields), PRESET_ID)

    assert "shared_steps" in str(schema["properties"]["t"])


def test_bind_form_expansion_resolves_shared_children(tmp_path, monkeypatch):
    shared = tmp_path / "content" / "presets" / "_shared"
    (shared / "x").mkdir(parents=True)
    (shared / "x" / "tab.yml").write_text(TAB_FRAGMENT)
    monkeypatch.chdir(tmp_path)
    preset_template = SimpleNamespace(path=str(tmp_path / "preset"))
    fields = [FieldTemplate(name="t", type="section", children="{{ paths._shared }}/x/tab.yml")]

    expanded = _expand_form_fields(fields, preset_template)

    assert [c.name for c in expanded[0].children] == ["shared_steps"]


def _children_errors(tmp_path: Path, shared: Path) -> list:
    issues = PresetLinter([str(tmp_path / "presets")], shared_path=shared).lint()
    return [i for i in issues if i.level == "error" and "children fragment not found" in i.message]


def test_lint_accepts_existing_shared_fragment(tmp_path):
    shared = _shared_tree(tmp_path)
    _write_preset(tmp_path / "presets", "{{ paths._shared }}/x/tab.yml")
    assert _children_errors(tmp_path, shared) == []


def test_lint_flags_missing_shared_fragment(tmp_path):
    shared = _shared_tree(tmp_path)
    _write_preset(tmp_path / "presets", "{{ paths._shared }}/x/missing.yml")
    errors = _children_errors(tmp_path, shared)
    assert len(errors) == 1
    assert "modes/txt2img/form.yml" in errors[0].message
    assert "{{ paths._shared }}/x/missing.yml" in errors[0].message


def test_lint_flags_missing_preset_fragment(tmp_path):
    shared = _shared_tree(tmp_path)
    _write_preset(tmp_path / "presets", "{{ paths.preset }}/tabs/missing.yml")
    errors = _children_errors(tmp_path, shared)
    assert len(errors) == 1
    assert "{{ paths.preset }}/tabs/missing.yml" in errors[0].message


def test_lint_accepts_existing_preset_fragment(tmp_path):
    shared = _shared_tree(tmp_path)
    preset_dir = _write_preset(tmp_path / "presets", "{{ paths.preset }}/tabs/local.yml")
    (preset_dir / "tabs").mkdir()
    (preset_dir / "tabs" / "local.yml").write_text(TAB_FRAGMENT)
    assert _children_errors(tmp_path, shared) == []
