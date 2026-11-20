import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.preset_lint import render_check
from src.features.presets import editor_mode_lint
from src.features.presets.editor_mode_lint import (
    fixed_value_problems,
    generation_mode_name_problems,
    lint_editor_mode,
    mask_problems,
    size_problems,
    source_problems,
    task_problems,
)
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.templates import FieldTemplate, PresetTemplate
from tests.features.presets.editor_mode_trees import (
    KREA2,
    KREA2_ID,
    KREA_EDITOR,
    ROOT,
    SDXL,
    copy_preset,
    shipped_editor_issues,
    write_editor_mode,
)

NEEDS = {"edit_region": "required", "remove_object": "none"}.get


def editors_of(task):
    return ("image",)


def field(name, type_="string", **configuration):
    return FieldTemplate(type=type_, name=name, configuration=configuration or None)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "presets"
    krea = copy_preset(KREA2, root)
    sdxl = copy_preset(SDXL, root)
    return SimpleNamespace(root=root, krea=krea, sdxl=sdxl, tmp=tmp_path)


def lint(root, plugin_manifests=None):
    issues = PresetLinter([str(root)], plugin_manifests=plugin_manifests or []).lint()
    return [issue for issue in issues if "editor_mode" in issue.message or "editor:" in issue.message]


def messages(issues, level=None):
    return [issue.message for issue in issues if level is None or issue.level == level]


def krea_template(root):
    loader = PresetTemplateLoader([str(root)])
    loader.load_presets()
    return loader.get(KREA2_ID)


def with_spec(tree, **changes):
    write_editor_mode(tree.krea, "edit", {**KREA_EDITOR, **changes})


def test_a_valid_editor_mode_lints_clean(tree):
    write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    issues = lint(tree.root)
    assert messages(issues, "error") == []
    assert messages(issues, "warning") == []


def test_the_shipped_editor_modes_lint_clean(shipped_editor_issues):
    editor = [issue for issue in shipped_editor_issues if "editor_mode" in issue.message]
    assert [issue for issue in editor if issue.level != "info"] == []


def test_an_icon_outside_the_icon_set_is_a_warning(tree):
    with_spec(tree, icon="no_such_icon_name")
    assert any("no_such_icon_name" in message for message in messages(lint(tree.root), "warning"))


def test_an_icon_in_the_icon_set_is_not_a_warning(tree):
    with_spec(tree, icon="brush")
    assert messages(lint(tree.root), "warning") == []


def test_a_load_error_of_an_editor_mode_is_a_lint_error(tree):
    with_spec(tree, **{"from": "no_such_mode", "fields": {}})
    errors = messages(lint(tree.root), "error")
    assert any("no_such_mode" in message for message in errors)


def test_a_mask_region_without_a_mask_input_is_an_error(tree):
    with_spec(tree, region="mask", fixed={}, fields={})
    assert any("region: mask needs inputs.mask" in message for message in messages(lint(tree.root), "error"))


def test_a_source_that_is_not_a_field_is_an_error(tree):
    with_spec(tree, inputs={"source": "no_such_field"}, fixed={}, fields={})
    assert any("not a field of the mode" in message for message in messages(lint(tree.root), "error"))


def test_source_problems_cover_missing_wrong_type_and_index_on_a_plain_field():
    fields = {"picture": field("picture", "image"), "pictures": field("pictures", "media"), "cfg": field("cfg", "slider")}
    assert source_problems("picture", fields, ("image",)) == []
    assert source_problems({"field": "pictures", "index": 0}, fields, ("image",)) == []
    assert "not a field" in source_problems("nope", fields, ("image",))[0]
    assert "slider field, not a image field" in source_problems("cfg", fields, ("image",))[0]
    assert "needs a media field" in source_problems({"field": "picture", "index": 0}, fields, ("image",))[0]
    assert "video field" in source_problems("picture", fields, ("video",))[0]


def test_mask_problems_cover_the_name_and_allow_inpaint():
    fields = {"picture": field("picture", "image", allow_inpaint=True), "plain": field("plain", "image")}
    assert mask_problems({"source": "picture", "mask": "picture_inpaint_mask"}, fields) == []
    assert mask_problems({"source": "picture"}, fields) == []
    assert "must be 'picture_inpaint_mask'" in mask_problems({"source": "picture", "mask": "other"}, fields)[0]
    assert "allow_inpaint" in mask_problems({"source": "plain", "mask": "plain_inpaint_mask"}, fields)[0]


def test_size_problems_cover_a_missing_and_a_wrong_typed_field():
    fields = {"resolution": field("resolution", "resolution"), "cfg": field("cfg", "slider")}
    assert size_problems({}, fields) == []
    assert size_problems({"field": "resolution"}, fields) == []
    assert "not a field" in size_problems({"field": "nope"}, fields)[0]
    assert "not a resolution field" in size_problems({"field": "cfg"}, fields)[0]


def test_a_size_field_that_is_not_a_resolution_field_is_an_error_end_to_end(tree):
    with_spec(tree, size={"field": "cfg"}, fixed={}, fields={})
    assert any("not a resolution field" in message for message in messages(lint(tree.root), "error"))


def test_fixed_value_problems_cover_select_checkbox_and_range():
    select = field("mode", "select", options=[{"label": "A", "value": "a"}, {"label": "B", "value": "b"}])
    checkbox = field("flag", "checkbox")
    slider = field("cfg", "slider", min=1, max=8)
    assert fixed_value_problems("mode", select, "a") == []
    assert "not an option" in fixed_value_problems("mode", select, "z")[0]
    assert fixed_value_problems("flag", checkbox, True) == []
    assert "true or false" in fixed_value_problems("flag", checkbox, "yes")[0]
    assert fixed_value_problems("cfg", slider, 4) == []
    assert "outside its range" in fixed_value_problems("cfg", slider, 99)[0]
    assert "outside its range" in fixed_value_problems("cfg", slider, 0)[0]
    assert "must be a number" in fixed_value_problems("cfg", slider, "x")[0]
    assert fixed_value_problems("note", field("note", "string"), 5) == []


@pytest.mark.parametrize(
    "fixed, fragment",
    [
        ({"fit_mode": "sideways"}, "not an option of 'fit_mode'"),
        ({"match_source": "yes"}, "must be true or false"),
        ({"cfg": 99}, "outside its range"),
    ],
)
def test_an_invalid_fixed_value_is_an_error_end_to_end(tree, fixed, fragment):
    with_spec(tree, fixed=fixed, fields={})
    assert any(fragment in message for message in messages(lint(tree.root), "error"))


def test_task_problems_cover_unknown_plugin_and_foreign_tasks():
    assert task_problems("edit_region", NEEDS, None) == ([], [])
    assert task_problems("nonsense", NEEDS, None)[0] == ["unknown task 'nonsense'"]
    errors, warnings = task_problems("other-plugin.relight", NEEDS, None)
    assert errors == [] and "not installed or enabled" in warnings[0]
    assert "cannot be offered" in task_problems("other-plugin.relight", lambda task: "required", "mine")[0][0]
    assert task_problems("mine.relight", lambda task: "required", "mine") == ([], [])


def test_an_unknown_task_is_an_error_and_a_missing_plugin_task_a_warning_end_to_end(tree):
    with_spec(tree, task="nonsense_task", fixed={}, fields={})
    issues = lint(tree.root)
    assert any("unknown task 'nonsense_task'" in message for message in messages(issues, "error"))
    with_spec_again = {**KREA_EDITOR, "task": "ghost.relight", "fixed": {}, "fields": {}}
    (tree.krea / "editor_modes" / "edit" / "editor_mode.yml").write_text(
        yaml.safe_dump(with_spec_again, sort_keys=False), encoding="utf-8"
    )
    issues = lint(tree.root)
    assert any("not installed or enabled" in message for message in messages(issues, "warning"))
    assert messages(issues, "error") == []


def test_a_task_that_needs_a_prompt_on_a_promptless_from_mode_is_a_warning(tree):
    write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    template = krea_template(tree.root)
    promptless = replace(template, vars={**(template.vars or {}), "promptless_modes": ["edit"]})
    issues = lint_editor_mode(promptless, "edit", template.editor_modes["edit"], NEEDS, editors_of, None)
    assert any(level == "warning" and "requires a prompt" in message for level, message in issues)
    clean = lint_editor_mode(template, "edit", template.editor_modes["edit"], NEEDS, editors_of, None)
    assert not any("requires a prompt" in message for _, message in clean)


def test_a_media_field_that_settings_will_show_is_reported_as_info(tree):
    spec = {**KREA_EDITOR, "fields": {"hide": ["ref_position_a", "ref_boost_a"]}}
    write_editor_mode(tree.krea, "edit", spec)
    infos = messages(lint(tree.root), "info")
    assert any("will show the image field 'source_image_b'" in message for message in infos)
    write_editor_mode(tree.krea, "other", KREA_EDITOR)
    assert not any("'source_image_b'" in message and "other" in message for message in messages(lint(tree.root), "info"))


def test_a_generation_mode_name_with_a_colon_is_an_error():
    template = PresetTemplate(id="x", name="x", version="1", path="/x", modes={"a:b": object(), "ok": object()})
    assert generation_mode_name_problems(template) == [("error", "modes/a:b: a mode name must not contain ':'")]


def test_a_leftover_editor_yml_beside_an_editor_mode_warns(tree):
    write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    (tree.krea / "modes" / "edit" / "editor.yml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")

    warnings = messages(lint(tree.root), "warning")

    assert any("modes/edit/editor.yml: no longer read" in message for message in warnings)
    assert not any("modes/inpaint" in message for message in warnings)


def test_the_leftover_warning_can_be_switched_off_by_its_gate(tree, monkeypatch):
    write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    (tree.krea / "modes" / "edit" / "editor.yml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")
    monkeypatch.setattr(editor_mode_lint, "LEFTOVER_EDITOR_YML_WARNS", False)

    assert not any("no longer read" in message for message in messages(lint(tree.root)))


def test_a_declaration_without_an_editor_mode_never_warns(tree):
    (tree.krea / "modes" / "edit" / "editor.yml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")

    assert not any("no longer read" in message for message in messages(lint(tree.root)))


def test_the_render_check_renders_editor_modes_and_reports_a_broken_expression(tree):
    pipeline = (tree.krea / "modes" / "edit" / "pipeline.yml").read_text(encoding="utf-8")
    assert "{{ form.cfg | default(1.0) }}" in pipeline
    broken = pipeline.replace("{{ form.cfg | default(1.0) }}", "{{ form.not_a_field }}")
    write_editor_mode(tree.krea, "edit", KREA_EDITOR, {"pipeline.yml": broken})
    issues = render_check([str(tree.krea)], presets_root=tree.root)
    assert any("editor:edit" in issue.message and "not_a_field" in issue.message for issue in issues)
    assert any("editor_modes/edit" in issue.preset_path for issue in issues)


def test_the_render_check_is_quiet_for_a_working_editor_mode(tree):
    write_editor_mode(tree.krea, "edit", KREA_EDITOR)
    assert render_check([str(tree.krea)], presets_root=tree.root) == []


def manifest(tmp, files, plugin_id="p"):
    plugin_dir = tmp / "plugins" / plugin_id
    for relative, content in files.items():
        path = plugin_dir / "contributed" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    return SimpleNamespace(
        id=plugin_id,
        plugin_dir=plugin_dir,
        preset_modes=[{"target": KREA2_ID, "modes_root": "contributed"}],
        preset_extensions=[],
        presets=[],
        editor_tasks=[],
    )


def test_a_contributed_editor_mode_is_linted_under_its_plugin(tree):
    good = {**KREA_EDITOR, "fixed": {"fit_mode": "sideways"}, "fields": {}}
    plugin = manifest(tree.tmp, {"editor_modes/relight/editor_mode.yml": good})
    issues = lint(tree.root, [plugin])
    hit = [issue for issue in issues if "not an option of 'fit_mode'" in issue.message]
    assert hit and hit[0].preset_path == f"plugin 'p' preset_modes -> '{KREA2_ID}'"


def test_a_broken_contributed_editor_mode_is_a_lint_error(tree):
    bad = {**KREA_EDITOR, "from": "nowhere", "fields": {}}
    plugin = manifest(tree.tmp, {"editor_modes/relight/editor_mode.yml": bad})
    errors = [issue for issue in lint(tree.root, [plugin]) if issue.level == "error"]
    assert any("nowhere" in issue.message and issue.preset_path.startswith("plugin 'p'") for issue in errors)
