from pathlib import Path
from types import SimpleNamespace

import yaml

from src.features.presets.linter import PresetLinter
from src.features.presets.schema import validate_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"

CATALOG = """formulas:
  groups:
    speed: { label: "Speed" }
    size: { label: "Size" }
"""


def _write_preset(tmp_path, form_body, catalog=CATALOG, extra_yaml="", tabs=None, modes=("txt2img",)):
    preset_dir = tmp_path / "Foo"
    preset_dir.mkdir(parents=True, exist_ok=True)
    modes_yaml = "\n".join(f"  - {m}" for m in modes)
    (preset_dir / "preset.yml").write_text(
        "schema: 1\n"
        'id: "01FORMULALINTAAAAAAAAAAAAAA"\n'
        'name: "Foo"\n'
        'version: "1.0.0"\n'
        'category: "image"\n'
        'engine: "native"\n'
        f"{catalog}{extra_yaml}"
        f"modes:\n{modes_yaml}\n"
    )
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    for rel, body in (tabs or {}).items():
        (preset_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (preset_dir / rel).write_text("fields:\n" + body)
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        (mode_dir / "form.yml").write_text('name: "custom"\nfields:\n' + form_body)
    return preset_dir


def _formula_issues(tmp_path, level=None):
    issues = PresetLinter([str(tmp_path)]).lint()
    return [
        i for i in issues
        if "formula" in i.message and (level is None or i.level == level)
    ]


BOTH_USED = (
    '  - name: "steps"\n    type: "slider"\n    formula: "speed"\n'
    '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
)


def test_a_clean_declaration_has_no_formula_issues(tmp_path):
    _write_preset(tmp_path, BOTH_USED)

    assert _formula_issues(tmp_path) == []


class TestUnknownGroupId:
    def test_a_field_naming_an_undeclared_group_is_an_error(self, tmp_path):
        _write_preset(tmp_path, BOTH_USED + '  - name: "cfg"\n    type: "slider"\n    formula: "turbo"\n')

        errors = _formula_issues(tmp_path, "error")
        assert len(errors) == 1
        assert "field 'cfg' has formula 'turbo'" in errors[0].message

    def test_a_section_naming_an_undeclared_group_is_an_error(self, tmp_path):
        body = BOTH_USED + (
            '  - type: "section"\n    label: "Cache"\n    formula: "cache"\n    children:\n'
            '      - name: "cache_threshold"\n        type: "slider"\n'
        )
        _write_preset(tmp_path, body)

        errors = _formula_issues(tmp_path, "error")
        assert len(errors) == 1
        assert "section 'Cache' has formula 'cache'" in errors[0].message

    def test_an_annotation_without_any_catalog_is_an_error(self, tmp_path):
        _write_preset(tmp_path, BOTH_USED, catalog="")

        assert len(_formula_issues(tmp_path, "error")) == 2

    def test_an_annotation_in_a_shared_tab_fragment_is_checked(self, tmp_path):
        body = BOTH_USED + '  - type: "tab"\n    label: "More"\n    children: "{{ paths.preset }}/tabs/more.yml"\n'
        _write_preset(tmp_path, body, tabs={"tabs/more.yml": '  - name: "cfg"\n    type: "slider"\n    formula: "nope"\n'})

        errors = _formula_issues(tmp_path, "error")
        assert [e.message.split(": ", 1)[1] for e in errors] == [
            "field 'cfg' has formula 'nope', which is not declared in preset.yml formulas.groups"
        ]


class TestCatalogEntries:
    def test_a_group_without_a_label_is_an_error(self, tmp_path):
        catalog = 'formulas:\n  groups:\n    speed: { label: "Speed" }\n    size: { description: "Output" }\n'
        _write_preset(tmp_path, BOTH_USED, catalog=catalog)

        errors = _formula_issues(tmp_path, "error")
        assert [e.message for e in errors] == ["formulas.groups.size: a group needs a label"]

    def test_a_group_no_field_uses_is_a_warning(self, tmp_path):
        _write_preset(tmp_path, '  - name: "steps"\n    type: "slider"\n    formula: "speed"\n')

        warnings = _formula_issues(tmp_path, "warning")
        assert [w.message for w in warnings] == ["formulas.groups.size: no field in any mode is in this group"]

    def test_a_group_used_by_only_one_mode_is_not_unused(self, tmp_path):
        _write_preset(tmp_path, BOTH_USED, modes=("txt2img", "img2img"))
        (tmp_path / "Foo" / "modes" / "img2img" / "form.yml").write_text(
            'name: "custom"\nfields:\n  - name: "steps"\n    type: "slider"\n'
        )

        assert _formula_issues(tmp_path) == []


class TestInputsCannotBeInAFormula:
    def test_an_input_inside_an_annotated_section_is_an_error(self, tmp_path):
        body = BOTH_USED + (
            '  - type: "section"\n    formula: "size"\n    children:\n'
            '      - name: "init_image"\n        type: "image"\n'
            '      - name: "denoise"\n        type: "slider"\n'
        )
        _write_preset(tmp_path, body)

        errors = _formula_issues(tmp_path, "error")
        assert len(errors) == 1
        assert "'init_image' (image) is an input" in errors[0].message

    def test_opting_the_input_out_clears_the_error(self, tmp_path):
        body = BOTH_USED + (
            '  - type: "section"\n    formula: "size"\n    children:\n'
            '      - name: "init_image"\n        type: "image"\n        formula: false\n'
            '      - name: "denoise"\n        type: "slider"\n'
        )
        _write_preset(tmp_path, body)

        assert _formula_issues(tmp_path) == []

    def test_the_seed_is_an_input(self, tmp_path):
        _write_preset(tmp_path, BOTH_USED + '  - name: "seed"\n    type: "seed"\n    formula: "speed"\n')

        assert "'seed' (seed) is an input" in _formula_issues(tmp_path, "error")[0].message


class TestOneFieldOneGroup:
    def test_the_same_name_in_two_groups_is_an_error(self, tmp_path):
        body = BOTH_USED + (
            '  - type: "tab"\n    label: "Again"\n    children:\n'
            '      - name: "steps"\n        type: "slider"\n        formula: "size"\n'
        )
        _write_preset(tmp_path, body)

        errors = _formula_issues(tmp_path, "error")
        assert [e.message.split(": ", 1)[1] for e in errors] == [
            "field 'steps' is in more than one formula group (size, speed)"
        ]

    def test_the_same_name_twice_in_one_group_is_clean(self, tmp_path):
        body = BOTH_USED + (
            '  - type: "tab"\n    label: "Again"\n    children:\n'
            '      - name: "steps"\n        type: "slider"\n        formula: "speed"\n'
        )
        _write_preset(tmp_path, body)

        assert _formula_issues(tmp_path) == []


PINNED_STEPS = (
    '  - name: "steps"\n    type: "slider"\n    formula: "speed"\n'
    '    reactions:\n'
    '      - when: {when}\n'
    '        then: {then}\n'
    '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
)


def _pinned(when='{ field: "speed_profile", equals: "fast" }', then="{ set_value: 4 }", profile_formula=None):
    profile = '  - name: "speed_profile"\n    type: "select"\n'
    if profile_formula:
        profile += f'    formula: "{profile_formula}"\n'
    return profile + PINNED_STEPS.replace("{when}", when).replace("{then}", then)


class TestReactionLinkedFieldsStayTogether:
    def test_a_controller_in_no_group_is_an_error(self, tmp_path):
        _write_preset(tmp_path, _pinned())

        errors = _formula_issues(tmp_path, "error")
        assert [e.message.split(": ", 1)[1] for e in errors] == [
            "'steps' is pinned by 'speed_profile', which is in no formula group; put 'speed_profile' in group 'speed'"
        ]

    def test_a_controller_in_another_group_is_an_error_naming_both(self, tmp_path):
        _write_preset(tmp_path, _pinned(profile_formula="size"))

        errors = _formula_issues(tmp_path, "error")
        assert len(errors) == 1
        assert "'steps' (group 'speed') is pinned by 'speed_profile' (group 'size')" in errors[0].message

    def test_a_controller_in_the_same_group_is_clean(self, tmp_path):
        _write_preset(tmp_path, _pinned(profile_formula="speed"))

        assert _formula_issues(tmp_path) == []

    def test_a_controller_inside_a_logic_group_is_found(self, tmp_path):
        when = (
            '{ logic: "AND", conditions: [ { field: "resolution", equals: "1024x1024" },'
            ' { field: "speed_profile", equals: "fast" } ] }'
        )
        _write_preset(tmp_path, _pinned(when=when, profile_formula="speed"))

        errors = _formula_issues(tmp_path, "error")
        assert len(errors) == 1
        assert "pinned by 'resolution' (group 'size')" in errors[0].message

    def test_a_disabled_state_link_counts(self, tmp_path):
        _write_preset(tmp_path, _pinned(then="{ set_disabled: true }"))

        assert len(_formula_issues(tmp_path, "error")) == 1

    def test_a_visibility_only_reaction_is_exempt(self, tmp_path):
        _write_preset(tmp_path, _pinned(then="{ set_visibility: false }"))

        assert _formula_issues(tmp_path) == []

    def test_a_model_filter_reaction_is_exempt(self, tmp_path):
        _write_preset(tmp_path, _pinned(then='{ set_filter_tags: ["turbo"] }'))

        assert _formula_issues(tmp_path) == []


class TestAllAdvancedGroup:
    def test_a_group_whose_fields_are_all_advanced_gets_an_info(self, tmp_path):
        body = (
            '  - name: "steps"\n    type: "slider"\n    formula: "speed"\n    audience: "advanced"\n'
            '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
        )
        _write_preset(tmp_path, body)

        infos = _formula_issues(tmp_path, "info")
        assert len(infos) == 1
        assert "every field in formula group 'speed' is audience: advanced" in infos[0].message
        assert _formula_issues(tmp_path, "warning") == []

    def test_one_simple_field_in_the_group_clears_the_warning(self, tmp_path):
        body = (
            '  - name: "steps"\n    type: "slider"\n    formula: "speed"\n    audience: "advanced"\n'
            '  - name: "cfg"\n    type: "slider"\n    formula: "speed"\n'
            '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
        )
        _write_preset(tmp_path, body)

        assert _formula_issues(tmp_path) == []


class TestUndeclaredPresetHint:
    LORAS = '  - name: "loras"\n    type: "lora_picker"\n'

    def test_a_preset_with_loras_and_no_groups_gets_an_info(self, tmp_path):
        _write_preset(tmp_path, self.LORAS, catalog="")

        infos = _formula_issues(tmp_path, "info")
        assert len(infos) == 1
        assert "declares no formulas.groups" in infos[0].message

    def test_a_preset_with_speed_profiles_and_no_groups_gets_an_info(self, tmp_path):
        profiles = "speed_profiles:\n  fast:\n    steps: 4\n"
        _write_preset(tmp_path, '  - name: "steps"\n    type: "slider"\n', catalog="", extra_yaml=profiles)

        assert len(_formula_issues(tmp_path, "info")) == 1

    def test_an_empty_catalog_still_gets_the_info(self, tmp_path):
        _write_preset(tmp_path, self.LORAS, catalog="formulas:\n  groups: {}\n")

        assert len(_formula_issues(tmp_path, "info")) == 1

    def test_a_declared_catalog_clears_the_info(self, tmp_path):
        catalog = 'formulas:\n  groups:\n    loras: { label: "LoRAs" }\n'
        _write_preset(tmp_path, '  - name: "loras"\n    type: "lora_picker"\n    formula: "loras"\n', catalog=catalog)

        assert _formula_issues(tmp_path) == []

    def test_a_preset_without_loras_or_profiles_gets_nothing(self, tmp_path):
        _write_preset(tmp_path, '  - name: "steps"\n    type: "slider"\n', catalog="")

        assert _formula_issues(tmp_path) == []


def test_a_plugin_contributed_mode_is_checked_against_the_target_catalog(tmp_path):
    presets = tmp_path / "presets"
    _write_preset(presets, BOTH_USED)
    mode_dir = tmp_path / "plugin" / "modes" / "extra"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text(
        'name: "custom"\nfields:\n  - name: "cfg"\n    type: "slider"\n    formula: "turbo"\n'
    )
    manifest = SimpleNamespace(
        id="extra-plugin",
        plugin_dir=tmp_path / "plugin",
        preset_modes=[{"target": "01FORMULALINTAAAAAAAAAAAAAA", "modes_root": "."}],
    )

    issues = PresetLinter([str(presets)], plugin_manifests=[manifest]).lint()
    errors = [i for i in issues if "formula" in i.message and i.level == "error"]

    assert len(errors) == 1
    assert "plugin 'extra-plugin'" in errors[0].preset_path
    assert "field 'cfg' has formula 'turbo'" in errors[0].message


def test_every_shipped_preset_declares_formula_groups_that_lint_clean():
    comfyui = REPO_ROOT / "content" / "plugins" / "marketplace" / "comfyui-backend" / "presets"
    preset_files = sorted(MARKETPLACE.rglob("preset.yml")) + sorted(comfyui.rglob("preset.yml"))
    linter = PresetLinter([])
    issues = []
    for preset_file in preset_files:
        manifest, errors = validate_manifest(yaml.safe_load(preset_file.read_text(encoding="utf-8")))
        assert errors == [], preset_file
        issues.extend(linter._lint_formulas(preset_file, manifest))

    assert len(preset_files) >= 27

    assert [str(i) for i in issues if "formula" in i.message and i.level != "info"] == []
    assert [str(i) for i in issues if "declares no formulas.groups" in i.message] == []
