from pathlib import Path

import pytest
from pydantic import ValidationError

from src.features.presets.linter import PresetLinter
from src.features.presets.schema import FieldSpec
from tests.features.presets.test_linter import _write_preset

ROOT = Path(__file__).resolve().parents[3]
PIPELINE_HEAD = "pipeline:\n  - name: generator\n    configuration:\n"


def _lint(tmp_path, pipeline, form, extra_yaml="", extra_files=None):
    preset_dir = _write_preset(tmp_path, "p", "01RAAAAAAAAAAAAAAAAAAAAAAAB", ["txt2img"], extra_yaml=extra_yaml)
    mode_dir = preset_dir / "modes" / "txt2img"
    (mode_dir / "pipeline.yml").write_text(pipeline)
    (mode_dir / "form.yml").write_text(form)
    for rel, content in (extra_files or {}).items():
        target = preset_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return PresetLinter([str(tmp_path)]).lint()


def _form(*fields):
    return "name: custom\nfields:\n" + "".join(f"  - {field}\n" for field in fields)


def _pipe(**config):
    return PIPELINE_HEAD + "".join(f"      {key}: \"{value}\"\n" for key, value in config.items())


def _matching(issues, level, *parts):
    return [i for i in issues if i.level == level and all(p in i.message for p in parts)]


class TestDanglingFormRefs:
    def test_unguarded_dangling_ref_is_error(self, tmp_path):
        issues = _lint(tmp_path, _pipe(v="{{ form.ghost }}"), _form("{type: string, name: real, default: ''}"))
        assert _matching(issues, "error", "form.ghost", "no field named")

    def test_guarded_dangling_ref_is_warning(self, tmp_path):
        issues = _lint(tmp_path, _pipe(v="{{ form.ghost | default(1) }}"), _form("{type: string, name: real, default: ''}"))
        assert _matching(issues, "warning", "form.ghost", "declares no field")
        assert not _matching(issues, "error", "form.ghost")

    @pytest.mark.parametrize("ref", [
        "form.quantity",
        "form.regional_prompt",
        "form.src__origin",
        "form.src_inpaint_mask",
        "form.video_director",
        "form.real",
    ])
    def test_host_injected_and_declared_refs_are_clean(self, tmp_path, ref):
        form = _form("{type: image, name: src}", "{type: string, name: real, default: ''}")
        issues = _lint(tmp_path, _pipe(v="{{ " + ref + " }}"), form)
        assert not _matching(issues, "error", "no field named")
        assert not _matching(issues, "warning", "declares no field")

    def test_inpaint_mask_suffix_needs_a_declared_base(self, tmp_path):
        issues = _lint(tmp_path, _pipe(v="{{ form.src_inpaint_mask }}"), _form("{type: string, name: real, default: ''}"))
        assert _matching(issues, "error", "form.src_inpaint_mask")


class TestUnreadFields:
    def test_unread_field_is_warning(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: number, name: orphan, default: 1}")
        issues = _lint(tmp_path, _pipe(v="{{ form.used }}"), form)
        assert _matching(issues, "warning", "field 'orphan'", "never read")
        assert not _matching(issues, "warning", "field 'used'", "never read")

    def test_display_only_opts_out(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: number, name: orphan, default: 1, display_only: true}")
        issues = _lint(tmp_path, _pipe(v="{{ form.used }}"), form)
        assert not _matching(issues, "warning", "never read")
        assert not _matching(issues, "error", "display_only")

    def test_display_only_must_be_boolean(self):
        with pytest.raises(ValidationError, match="display_only"):
            FieldSpec(type="string", name="x", display_only="yes")
        assert FieldSpec(type="string", name="x", display_only=True).display_only is True

    def test_field_read_by_another_field_condition_is_clean(self, tmp_path):
        form = _form(
            "{type: string, name: used, default: ''}",
            "{type: checkbox, name: toggle, default: false}",
            "{type: string, name: dependent, default: '', when: \"{{ form.toggle }}\"}",
        )
        issues = _lint(tmp_path, _pipe(v="{{ form.used }} {{ form.dependent }}"), form)
        assert not _matching(issues, "warning", "field 'toggle'", "never read")

    def test_field_read_by_editor_mode_file_is_clean(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: number, name: strength_x, default: 1}")
        issues = _lint(
            tmp_path, _pipe(v="{{ form.used }}"), form,
            extra_files={"editor_modes/em/editor_mode.yml": "fixed:\n  strength_x: 0.5\n"},
        )
        assert not _matching(issues, "warning", "field 'strength_x'", "never read")

    def test_other_mode_files_do_not_count_as_consumers(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: number, name: strength_x, default: 1}")
        issues = _lint(
            tmp_path, _pipe(v="{{ form.used }}"), form,
            extra_files={"modes/other/pipeline.yml": _pipe(v="{{ form.strength_x }}")},
        )
        assert _matching(issues, "warning", "field 'strength_x'", "never read")

    def test_dynamic_bracket_prefix_counts_as_read(self, tmp_path):
        form = _form("{type: string, name: emb_1, default: ''}", "{type: string, name: emb_2, default: ''}")
        issues = _lint(tmp_path, _pipe(v="{{ form['emb_' ~ loop.index] }}"), form)
        assert not _matching(issues, "warning", "never read")

    def test_literal_bracket_access_counts_as_read(self, tmp_path):
        form = _form("{type: string, name: emb, default: ''}")
        issues = _lint(tmp_path, _pipe(v="{{ form['emb'] }}"), form)
        assert not _matching(issues, "warning", "never read")

    def test_bare_form_use_disables_the_check(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: string, name: orphan, default: ''}")
        issues = _lint(tmp_path, _pipe(v="{{ form.used }}", w="{{ form | tojson }}"), form)
        assert not _matching(issues, "warning", "never read")

    def test_host_consumed_names_are_clean(self, tmp_path):
        form = _form("{type: string, name: used, default: ''}", "{type: seed, name: seed, default: -1}")
        issues = _lint(tmp_path, _pipe(v="{{ form.used }}"), form)
        assert not _matching(issues, "warning", "field 'seed'", "never read")

    def test_alert_and_container_fields_are_not_values(self, tmp_path):
        form = _form(
            "{type: string, name: used, default: ''}",
            "{type: alert, name: note, label: Note, configuration: {message: hi}}",
        )
        issues = _lint(tmp_path, _pipe(v="{{ form.used }}"), form)
        assert not _matching(issues, "warning", "field 'note'", "never read")


def _pipe_for(loader):
    return f"pipeline:\n  - name: model_loader/{loader}\n    configuration:\n      v: \"{{{{ form.used }}}}\"\n"


USED_FORM = _form("{type: string, name: used, default: ''}")
GUIDANCE_FORM = _form(
    "{type: string, name: used, default: ''}",
    "{type: slider, name: guidance, default: 3, configuration: {min: 0, max: 10, step: 0.5}}",
)
WEIGHT_SYNTAX = (
    "prompt_syntax:\n  \"*\":\n    - token: \"(tag:1.2)\"\n      kind: weight\n"
    "      pattern: '\\(([^()]+?):(\\d+(?:[.,]\\d+)?)\\)'\n      insert: \"({}:{weight=1.2})\"\n"
)
MESH_LOADERS_WITHOUT_CAPABILITIES = {"pixal3d", "sdxl", "trellis2"}
NO_NEGATIVE = "negative_prompt:\n  applies_when: false"


class TestFamilyCapabilities:
    def test_missing_negative_declaration_is_warning(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("minimax_h3_image"), USED_FORM)
        assert _matching(issues, "warning", "takes no negative prompt", "does not declare")

    def test_negative_declared_false_is_clean(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("minimax_h3_image"), USED_FORM, extra_yaml=NO_NEGATIVE)
        assert not _matching(issues, "warning", "takes no negative prompt")

    def test_form_level_negative_declaration_is_clean(self, tmp_path):
        form = "name: custom\nnegative_prompt:\n  applies_when: false\nfields:\n  - {type: string, name: used, default: ''}\n"
        issues = _lint(tmp_path, _pipe_for("minimax_h3_image"), form)
        assert not _matching(issues, "warning", "takes no negative prompt")

    def test_family_with_negative_support_is_clean(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("qwen_image21"), USED_FORM)
        assert not _matching(issues, "warning", "takes no negative prompt")

    def test_unknown_loader_is_skipped(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("not_a_family"), USED_FORM)
        assert not _matching(issues, "warning", "takes no negative prompt")

    def test_promptless_mode_needs_no_declaration(self, tmp_path):
        form = "name: custom\nprompt: none\nfields:\n  - {type: string, name: used, default: ''}\n"
        issues = _lint(tmp_path, _pipe_for("minimax_h3_image"), form)
        assert not _matching(issues, "warning", "takes no negative prompt")

    def test_applies_when_true_on_unsupported_family_is_warning(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("minimax_h3_image"), USED_FORM, extra_yaml="negative_prompt:\n  applies_when: true")
        assert _matching(issues, "warning", "takes no negative prompt", "use `false`")

    def test_weight_token_on_non_weighting_family_is_warning(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("qwen_image21"), USED_FORM, extra_yaml=WEIGHT_SYNTAX)
        assert _matching(issues, "warning", "ignores prompt weights")

    def test_weight_token_on_weighting_family_is_clean(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("anima"), USED_FORM, extra_yaml=WEIGHT_SYNTAX)
        assert not _matching(issues, "warning", "ignores prompt weights")

    def test_mixed_variants_skip_the_rule(self, tmp_path):
        issues = _lint(tmp_path, _pipe_for("flux"), USED_FORM, extra_yaml=WEIGHT_SYNTAX)
        assert not _matching(issues, "warning", "ignores prompt weights")

    def test_guidance_field_on_guidanceless_family_is_warning(self, tmp_path):
        pipeline = _pipe_for("minimax_h3_image") + "      g: \"{{ form.guidance }}\"\n"
        issues = _lint(tmp_path, pipeline, GUIDANCE_FORM, extra_yaml=NO_NEGATIVE)
        assert _matching(issues, "warning", "has no guidance", "guidance")

    def test_guidance_field_on_cfg_family_is_clean(self, tmp_path):
        pipeline = _pipe_for("qwen_image21") + "      g: \"{{ form.guidance }}\"\n"
        issues = _lint(tmp_path, pipeline, GUIDANCE_FORM)
        assert not _matching(issues, "warning", "has no guidance")

    def test_loader_resolves_to_registry_variants(self):
        assert PresetLinter._loader_variants("flux") == ["flux1", "flux2"]
        assert PresetLinter._loader_variants("wan22") == [
            "wan22_i2v_14b", "wan_i2v_14b", "wan_t2v_14b", "wan_ti2v_5b",
        ]
        assert PresetLinter._loader_variants("not_a_family") == []

    def test_every_shipped_native_loader_resolves_to_known_variants(self):
        table = PresetLinter._family_capabilities()
        loaders = set()
        for preset_file in (ROOT / "content" / "presets").rglob("preset.yml"):
            for pipeline_file in (preset_file.parent / "modes").glob("*/pipeline.yml"):
                loader = PresetLinter._mode_loader(pipeline_file.parent)
                if loader:
                    loaders.add(loader)
        assert loaders
        unresolved = sorted(
            loader for loader in loaders
            if not [v for v in PresetLinter._loader_variants(loader) if v in table]
        )
        assert unresolved == sorted(MESH_LOADERS_WITHOUT_CAPABILITIES)
