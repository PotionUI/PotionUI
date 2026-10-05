from pathlib import Path
from unittest.mock import Mock

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.negative_prompt import (
    negative_applies_when,
    negative_prompt_applies,
    negative_prompt_declarations,
)
from src.features.presets.processor import PresetProcessor
from src.features.presets.schema import validate_form_file, validate_manifest
from src.platform.templating.processor import TemplateProcessor

PRESET_ID = "01NEGAAAAAAAAAAAAAAAAAAAAAA"

CFG_OR_NAG = """negative_prompt:
  applies_when:
    logic: OR
    conditions:
      - { field: cfg, greater_than: 1 }
      - logic: AND
        conditions:
          - { field: nag_enabled, equals: true }
          - { field: nag_scale, greater_than: 1 }
"""

FIELDS = """fields:
  - { name: cfg, type: slider, default: 1.0 }
  - { name: nag_enabled, type: checkbox, default: false }
  - { name: nag_scale, type: slider, default: 1.0 }
"""

ENCODER = """pipeline:
  - name: prompt_encoder
    configuration:
      guidance_scale: "{{ form.cfg }}"
      negative_applied: "{{ generation.negative_applied }}"
"""


def _write(tmp_path: Path, *, preset_yaml: str = "", forms: dict = None, pipeline: str = ENCODER) -> Path:
    forms = forms if forms is not None else {"txt2img": FIELDS}
    preset_dir = tmp_path / "presets/native/Neg/std"
    preset_dir.mkdir(parents=True, exist_ok=True)
    modes_yaml = "\n".join(f"  - {m}" for m in forms)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{PRESET_ID}"
name: "Neg"
version: "1.0.0"
category: "image"
engine: "native"
{preset_yaml}
modes:
{modes_yaml}
"""
    )
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    for mode, form_yaml in forms.items():
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        (mode_dir / "pipeline.yml").write_text(pipeline)
        (mode_dir / "form.yml").write_text(form_yaml)
    return preset_dir


def _load(tmp_path: Path):
    loader = PresetTemplateLoader([str(tmp_path)])
    loader.load_presets()
    assert loader.load_errors == {}
    return loader.presets[0]


def _negative_issues(tmp_path: Path):
    return [i for i in PresetLinter([str(tmp_path)]).lint() if "negative_prompt" in i.message]


class TestSchema:
    def test_manifest_accepts_reaction_grammar(self):
        manifest, errors = validate_manifest({
            "schema": 1, "id": PRESET_ID, "name": "n", "version": "1.0.0", "category": "image",
            "engine": "native", "modes": ["txt2img"],
            "negative_prompt": {"applies_when": {"field": "cfg", "greater_than": 1}},
        })
        assert errors == []
        assert manifest.negative_prompt.applies_when.operator == "greater_than"

    def test_unknown_operator_is_rejected(self):
        _, errors = validate_form_file({
            "fields": [], "negative_prompt": {"applies_when": {"field": "cfg", "bigger": 1}},
        })
        assert errors

    def test_unknown_key_is_rejected(self):
        _, errors = validate_form_file({
            "fields": [], "negative_prompt": {"applies_when": {"field": "cfg", "equals": 1}, "when": {}},
        })
        assert errors


class TestResolution:
    def test_mode_form_wins_over_preset_level(self, tmp_path):
        _write(
            tmp_path,
            preset_yaml="negative_prompt:\n  applies_when: { field: cfg, greater_than: 1 }\n",
            forms={
                "txt2img": FIELDS,
                "video": "negative_prompt:\n  applies_when: { field: nag_scale, greater_than: 1 }\n" + FIELDS,
            },
        )
        template = _load(tmp_path)
        assert negative_applies_when(template, "txt2img")["field"] == "cfg"
        assert negative_applies_when(template, "video")["field"] == "nag_scale"
        assert negative_prompt_applies(template, "video", None, {"cfg": 5, "nag_scale": 1}) is False
        assert negative_prompt_applies(template, "txt2img", None, {"cfg": 5, "nag_scale": 1}) is True

    def test_no_declaration_resolves_to_none(self, tmp_path):
        _write(tmp_path)
        template = _load(tmp_path)
        assert negative_applies_when(template, "txt2img") is None
        assert negative_prompt_applies(template, "txt2img", None, {"cfg": 1}) is None
        assert negative_prompt_declarations(template) is None

    def test_or_of_cfg_and_gated_nag(self, tmp_path):
        _write(tmp_path, forms={"txt2img": CFG_OR_NAG + FIELDS})
        template = _load(tmp_path)

        def applies(**values):
            return negative_prompt_applies(template, "txt2img", None, values)

        assert applies(cfg=1, nag_enabled=False, nag_scale=1.5) is False
        assert applies(cfg=4, nag_enabled=False, nag_scale=1) is True
        assert applies(cfg=1, nag_enabled=True, nag_scale=1.5) is True
        assert applies(cfg=1, nag_enabled=True, nag_scale=1) is False
        assert applies(cfg=1, nag_enabled=True) is False

    def test_declarations_served_per_mode_and_variant(self, tmp_path):
        _write(
            tmp_path,
            preset_yaml="negative_prompt:\n  applies_when: { field: cfg, greater_than: 1 }\n",
            forms={"txt2img": FIELDS, "video": CFG_OR_NAG + FIELDS},
        )
        template = _load(tmp_path)
        info = FilePresetRepository(Mock()).preset_to_info(template).dict()
        declared = info["negative_prompt"]
        assert declared["applies_when"]["field"] == "cfg"
        assert declared["modes"]["txt2img"]["default"]["field"] == "cfg"
        assert declared["modes"]["video"]["default"]["logic"] == "OR"
        assert [w["logic"] for w in declared["modes"]["video"]["variants"].values()] == ["OR"]


class TestNeverApplies:
    def test_false_never_applies_and_needs_no_fields(self, tmp_path):
        _write(tmp_path, preset_yaml="negative_prompt:\n  applies_when: false\n")
        template = _load(tmp_path)
        assert negative_prompt_applies(template, "txt2img", None, {"cfg": 7}) is False
        assert negative_prompt_declarations(template)["modes"]["txt2img"]["default"] is False
        assert _negative_issues(tmp_path) == []

    def test_mode_false_overrides_preset_condition(self, tmp_path):
        _write(
            tmp_path,
            preset_yaml="negative_prompt:\n  applies_when: { field: cfg, greater_than: 1 }\n",
            forms={"txt2img": FIELDS, "upscale": "negative_prompt:\n  applies_when: false\n" + FIELDS},
        )
        template = _load(tmp_path)
        assert negative_prompt_applies(template, "upscale", None, {"cfg": 7}) is False
        assert negative_prompt_applies(template, "txt2img", None, {"cfg": 7}) is True

    def test_string_false_is_rejected(self):
        _, errors = validate_form_file({"fields": [], "negative_prompt": {"applies_when": "false"}})
        assert errors


class TestProcessorContext:
    def test_generation_negative_applied_reaches_prompt_encoder(self, tmp_path):
        _write(tmp_path, forms={"txt2img": CFG_OR_NAG + FIELDS})
        template = _load(tmp_path)
        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()),
            settings=Mock(),
            preset_template_loader=Mock(),
        )

        def encoder_config(**form):
            pipes = processor.process(template, {"mode": "txt2img", "form_data": form})
            return next(p for p in pipes if p["name"] == "prompt_encoder")["config"]

        assert encoder_config(cfg=1.0, nag_enabled=False, nag_scale=1.0)["negative_applied"] is False
        assert encoder_config(cfg=4.0, nag_enabled=False, nag_scale=1.0)["negative_applied"] is True
        assert encoder_config(cfg=1.0, nag_enabled=True, nag_scale=1.5)["negative_applied"] is True

    def test_undeclared_preset_renders_none(self, tmp_path):
        _write(tmp_path)
        template = _load(tmp_path)
        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()),
            settings=Mock(),
            preset_template_loader=Mock(),
        )
        pipes = processor.process(template, {"mode": "txt2img", "form_data": {"cfg": 1.0}})
        assert pipes[0]["config"]["negative_applied"] is None


class TestLint:
    def test_known_fields_are_clean(self, tmp_path):
        _write(tmp_path, forms={"txt2img": CFG_OR_NAG + FIELDS})
        assert _negative_issues(tmp_path) == []

    def test_unknown_field_is_an_error(self, tmp_path):
        form = "negative_prompt:\n  applies_when: { field: guidance_scale, greater_than: 1 }\n" + FIELDS
        _write(tmp_path, forms={"txt2img": form})
        issues = _negative_issues(tmp_path)
        assert [i.level for i in issues] == ["error"]
        assert "'guidance_scale'" in issues[0].message

    def test_nested_unknown_field_is_an_error(self, tmp_path):
        form = CFG_OR_NAG.replace("nag_enabled", "nag_on") + FIELDS
        _write(tmp_path, forms={"txt2img": form})
        issues = _negative_issues(tmp_path)
        assert [i.level for i in issues] == ["error"]
        assert "'nag_on'" in issues[0].message

    def test_preset_level_declaration_is_checked_against_each_mode(self, tmp_path):
        _write(
            tmp_path,
            preset_yaml="negative_prompt:\n  applies_when: { field: cfg, greater_than: 1 }\n",
            forms={"txt2img": FIELDS, "upscale": "fields:\n  - { name: scale, type: slider, default: 2 }\n"},
        )
        issues = _negative_issues(tmp_path)
        assert [i.level for i in issues] == ["error"]
        assert "upscale" in issues[0].message

    def test_fields_from_external_tabs_count(self, tmp_path):
        preset_dir = _write(
            tmp_path,
            forms={"txt2img": CFG_OR_NAG + 'fields:\n  - type: tab\n    label: A\n    children: "{{ paths.preset }}/modes/txt2img/tabs/a.yml"\n'},
        )
        tabs = preset_dir / "modes" / "txt2img" / "tabs"
        tabs.mkdir()
        (tabs / "a.yml").write_text(FIELDS)
        assert [i for i in _negative_issues(tmp_path) if i.level == "error"] == []

    def test_unwired_prompt_encoder_warns(self, tmp_path):
        _write(
            tmp_path,
            forms={"txt2img": CFG_OR_NAG + FIELDS},
            pipeline='pipeline:\n  - name: prompt_encoder\n    configuration:\n      guidance_scale: "{{ form.cfg }}"\n',
        )
        issues = _negative_issues(tmp_path)
        assert [i.level for i in issues] == ["warning"]
        assert "generation.negative_applied" in issues[0].message
