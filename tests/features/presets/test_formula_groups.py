from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.presets.form_serializer import PresetFormSerializer
from src.features.presets.formula_groups import FormulaGroup, resolve_formula_groups
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.operations import query
from src.features.presets.templates import (
    FieldTemplate,
    FormTemplate,
    ModeTemplate,
    PresetTemplate,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"

CATALOG = """formulas:
  groups:
    speed: { label: "Speed and sampling" }
    size: { label: "Size", description: "Output dimensions" }
    cache: { label: "Step cache" }
    models: { label: "Models", preselect: false }
"""

SHARED_ADVANCED = """fields:
  - type: "section"
    label: "Sampling"
    children:
      - name: "steps"
        type: "slider"
        formula: "speed"
        reactions:
          - when: { field: "speed_profile", equals: "fast" }
            then: { set_value: 4 }
      - type: "row"
        children:
          - name: "sampler"
            type: "select"
            formula: "speed"
          - name: "manual_sigmas"
            type: "textbox"
  - type: "section"
    label: "Step cache"
    formula: "cache"
    children:
      - type: "row"
        children:
          - name: "cache_threshold"
            type: "slider"
          - name: "cache_warmup"
            type: "slider"
          - name: "cache_debug"
            type: "checkbox"
            formula: false
"""


def _form(name: str, body: str) -> str:
    return f'name: "{name}"\nfields:\n' + body


GENERATION_BODY = """  - name: "speed_profile"
    type: "select"
    formula: "speed"
  - type: "section"
    label: "Image"
    children:
      - name: "seed"
        type: "seed"
      - name: "resolution"
        type: "resolution"
        formula: "size"
  - type: "section"
    label: "Models"
    formula: "models"
    children:
      - name: "checkpoint"
        type: "model"
      - name: "vae"
        type: "model"
  - type: "tab"
    label: "Advanced"
    children: "{{ paths.preset }}/shared/advanced.yml"
"""


def _write_preset(root: Path, catalog: str = CATALOG, modes=None, variants=None) -> Path:
    modes = modes or {"txt2img": GENERATION_BODY, "img2img": GENERATION_BODY}
    preset_dir = root / "Foo"
    (preset_dir / "shared").mkdir(parents=True)
    (preset_dir / "shared" / "advanced.yml").write_text(SHARED_ADVANCED)
    modes_yaml = "\n".join(f"  - {m}" for m in modes)
    (preset_dir / "preset.yml").write_text(
        "schema: 1\n"
        'id: "01FORMULAGROUPSAAAAAAAAAAAA"\n'
        'name: "Foo"\n'
        'version: "1.0.0"\n'
        'category: "image"\n'
        'engine: "native"\n'
        f"{catalog}"
        f"modes:\n{modes_yaml}\n"
    )
    for mode, body in modes.items():
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        (mode_dir / "form.yml").write_text(_form("custom", body))
    for (mode, variant), body in (variants or {}).items():
        variant_dir = preset_dir / "modes" / mode / "variants" / variant
        variant_dir.mkdir(parents=True)
        (variant_dir / "form.yml").write_text(_form(variant, body))
    return preset_dir


def _load(root: Path) -> PresetTemplate:
    loader = PresetTemplateLoader([str(root)])
    loader.load_presets()
    assert loader.load_errors == {}
    assert len(loader.presets) == 1
    return loader.presets[0]


def _by_id(groups):
    return {group.id: group for group in groups}


class TestResolveFormulaGroups:
    def test_groups_come_back_in_catalog_order_with_their_catalog_details(self, tmp_path):
        preset = _load(_write_preset(tmp_path))

        groups = resolve_formula_groups(preset, "txt2img", None)

        assert [g.id for g in groups] == ["speed", "size", "cache", "models"]
        assert groups[1] == FormulaGroup(
            id="size", label="Size", description="Output dimensions", preselect=True, fields=["resolution"],
        )
        assert _by_id(groups)["models"].preselect is False

    def test_a_section_formula_reaches_every_field_inside_it(self, tmp_path):
        groups = _by_id(resolve_formula_groups(_load(_write_preset(tmp_path)), "txt2img"))

        assert groups["models"].fields == ["checkpoint", "vae"]
        assert groups["cache"].fields[:2] == ["cache_threshold", "cache_warmup"]

    def test_formula_false_keeps_a_field_out_of_its_sections_group(self, tmp_path):
        groups = _by_id(resolve_formula_groups(_load(_write_preset(tmp_path)), "txt2img"))

        assert groups["cache"].fields == ["cache_threshold", "cache_warmup"]

    def test_a_field_without_a_formula_in_an_unannotated_section_is_in_no_group(self, tmp_path):
        groups = resolve_formula_groups(_load(_write_preset(tmp_path)), "txt2img")
        grouped = {name for group in groups for name in group.fields}

        assert "manual_sigmas" not in grouped
        assert "seed" not in grouped
        assert _by_id(groups)["speed"].fields == ["speed_profile", "steps", "sampler"]

    def test_a_field_formula_overrides_the_group_of_its_section(self, tmp_path):
        body = GENERATION_BODY.replace(
            '      - name: "vae"\n        type: "model"\n',
            '      - name: "vae"\n        type: "model"\n        formula: "size"\n',
        )
        groups = _by_id(resolve_formula_groups(_load(_write_preset(tmp_path, modes={"txt2img": body})), "txt2img"))

        assert groups["models"].fields == ["checkpoint"]
        assert groups["size"].fields == ["resolution", "vae"]

    def test_a_shared_tab_offers_its_groups_in_every_mode_that_includes_it(self, tmp_path):
        preset = _load(_write_preset(tmp_path))

        for mode in ("txt2img", "img2img"):
            groups = _by_id(resolve_formula_groups(preset, mode))
            assert groups["speed"].fields == ["speed_profile", "steps", "sampler"]
            assert groups["cache"].fields == ["cache_threshold", "cache_warmup"]

    def test_each_form_variant_resolves_its_own_groups(self, tmp_path):
        lean = '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
        preset = _load(_write_preset(tmp_path, variants={("txt2img", "lean"): lean}))

        assert [g.id for g in resolve_formula_groups(preset, "txt2img", "lean")] == ["size"]
        assert [g.id for g in resolve_formula_groups(preset, "txt2img", "custom")] == ["speed", "size", "cache", "models"]

    def test_an_unknown_form_variant_has_no_groups(self, tmp_path):
        assert resolve_formula_groups(_load(_write_preset(tmp_path)), "txt2img", "missing") == []

    def test_a_preset_without_a_catalog_has_no_groups(self, tmp_path):
        assert resolve_formula_groups(_load(_write_preset(tmp_path, catalog="")), "txt2img") == []

    def test_a_mode_with_no_annotated_fields_has_no_groups(self, tmp_path):
        body = '  - name: "seed"\n    type: "seed"\n  - name: "steps"\n    type: "slider"\n'
        preset = _load(_write_preset(tmp_path, modes={"txt2img": body}))

        assert resolve_formula_groups(preset, "txt2img") == []

    def test_an_unknown_mode_has_no_groups(self, tmp_path):
        assert resolve_formula_groups(_load(_write_preset(tmp_path)), "nope") == []

    def test_a_group_id_missing_from_the_catalog_is_left_out(self, tmp_path):
        body = '  - name: "steps"\n    type: "slider"\n    formula: "turbo"\n'
        preset = _load(_write_preset(tmp_path, modes={"txt2img": body}))

        assert resolve_formula_groups(preset, "txt2img") == []

    def test_an_input_field_is_never_offered_even_inside_an_annotated_section(self, tmp_path):
        body = (
            '  - type: "section"\n    formula: "size"\n    children:\n'
            '      - name: "init_image"\n        type: "image"\n'
            '      - name: "resolution"\n        type: "resolution"\n'
        )
        preset = _load(_write_preset(tmp_path, modes={"txt2img": body}))

        assert resolve_formula_groups(preset, "txt2img")[0].fields == ["resolution"]

    def test_a_gate_is_offered_as_a_field_together_with_its_children(self, tmp_path):
        body = (
            '  - name: "detailer_enabled"\n    type: "gate"\n    formula: "cache"\n    children:\n'
            '      - name: "detailer_strength"\n        type: "slider"\n'
        )
        preset = _load(_write_preset(tmp_path, modes={"txt2img": body}))

        assert resolve_formula_groups(preset, "txt2img")[0].fields == ["detailer_enabled", "detailer_strength"]

    def test_a_mode_contributed_by_a_plugin_uses_the_presets_catalog(self):
        form = FormTemplate(name="custom", fields=[
            FieldTemplate(type="section", label="Speed", formula="speed", children=[
                FieldTemplate(type="slider", name="steps"),
            ]),
        ])
        preset = PresetTemplate(
            id="p", name="P", version="1.0.0", path="/p",
            modes={"extra": ModeTemplate(forms=[form], pipes=[], source_plugin="some-plugin")},
            formulas={"groups": {"speed": {"label": "Speed", "description": None, "preselect": True}}},
        )

        assert resolve_formula_groups(preset, "extra") == [
            FormulaGroup(id="speed", label="Speed", description=None, preselect=True, fields=["steps"]),
        ]


class TestFormulaKeyInTheFieldSchema:
    def _render(self, fields):
        loader = Mock(spec=PresetTemplateLoader)
        loader.shared_path = Path("/presets/_shared")
        form = FormTemplate(name="custom", fields=fields)
        return PresetFormSerializer(loader).process_form_fields(form, "p")

    def test_a_field_carries_its_formula_and_an_opt_out_reaches_the_frontend(self):
        row = FieldTemplate(type="row", name="r", formula="speed", children=[
            FieldTemplate(type="slider", name="steps", formula="speed"),
            FieldTemplate(type="slider", name="debug", formula=False),
            FieldTemplate(type="slider", name="cfg"),
        ])

        rendered = self._render([row])["properties"]["r"]
        children = {child["name"]: child for child in rendered["children"]}

        assert rendered["formula"] == "speed"
        assert children["steps"]["formula"] == "speed"
        assert children["debug"]["formula"] is False
        assert "formula" not in children["cfg"]


class TestFormEndpointFormulasBlock:
    def _collaborators(self, preset):
        collaborators = Mock()
        collaborators.file_repo.find_preset_by_id.return_value = preset
        collaborators.db_repo.get_preset_form_overrides.return_value = {}
        collaborators.form_serializer.process_form_fields.return_value = {"properties": {}}
        return collaborators

    def test_the_form_response_lists_the_resolved_groups(self, tmp_path):
        preset = _load(_write_preset(tmp_path))

        data = query.get_form_schema(self._collaborators(preset), preset.id, "txt2img")

        assert data["formulas"]["groups"][0] == {
            "id": "speed", "label": "Speed and sampling", "description": None, "preselect": True,
            "fields": ["speed_profile", "steps", "sampler"],
        }
        assert [g["id"] for g in data["formulas"]["groups"]] == ["speed", "size", "cache", "models"]
        assert data["formulas"]["groups"][3]["preselect"] is False

    def test_the_form_response_follows_the_requested_variant(self, tmp_path):
        lean = '  - name: "resolution"\n    type: "resolution"\n    formula: "size"\n'
        preset = _load(_write_preset(tmp_path, variants={("txt2img", "lean"): lean}))

        data = query.get_form_schema(self._collaborators(preset), preset.id, "txt2img", "lean")

        assert [g["id"] for g in data["formulas"]["groups"]] == ["size"]

    def test_the_form_response_has_no_formulas_block_without_groups(self, tmp_path):
        preset = _load(_write_preset(tmp_path, catalog=""))

        data = query.get_form_schema(self._collaborators(preset), preset.id, "txt2img")

        assert "formulas" not in data


class TestShippedDeclarations:
    @pytest.fixture(scope="class")
    def h3(self):
        return _load(MARKETPLACE / "MiniMax-H3")

    def test_minimax_h3_video_offers_the_declared_groups(self, h3):
        groups = _by_id(resolve_formula_groups(h3, "video"))

        assert list(groups) == ["speed", "size", "loras", "cache", "sparse", "enhance", "models"]
        assert groups["speed"].fields == ["speed_profile", "steps", "sampler", "scheduler"]
        assert groups["size"].fields == ["resolution"]
        assert groups["models"].preselect is False
        assert groups["models"].fields == ["model", "text_encoder", "video_vae", "audio_vae", "upscale_model"]
        grouped = {name for group in groups.values() for name in group.fields}
        assert not grouped & {"seed", "quantity", "manual_sigmas", "keyframe_pixel_budget", "preview"}

    def test_minimax_h3_refs_gets_the_shared_advanced_tab_groups(self, h3):
        groups = _by_id(resolve_formula_groups(h3, "refs"))

        assert groups["speed"].fields == ["speed_profile", "steps", "sampler", "scheduler"]
        assert "sparse" in groups and "cache" in groups
        assert "references" not in {name for group in groups.values() for name in group.fields}

    def test_z_image_keeps_the_speed_profile_with_the_fields_it_pins(self):
        groups = _by_id(resolve_formula_groups(_load(MARKETPLACE / "ZImage"), "txt2img"))

        assert {"speed_profile", "steps", "sampler", "schedule", "cfg"} <= set(groups["speed"].fields)
        assert groups["models"].fields == ["diffusion_model", "text_encoder", "vae"]
        assert groups["models"].preselect is False


class TestPrintFormulas:
    def test_prints_every_mode_with_its_groups(self, tmp_path, monkeypatch, capsys):
        import scripts.preset_render as preset_render
        from scripts.preset_lint import print_formulas

        preset = _load(_write_preset(tmp_path))
        monkeypatch.setattr(preset_render, "load_all_presets", lambda presets_root=None: ([preset], {}))

        assert print_formulas("Foo") == 0
        out = capsys.readouterr().out
        assert "txt2img / custom" in out and "img2img / custom" in out
        assert "speed_profile, steps, sampler" in out
        assert "models: Models (never preselected)" in out

    def test_an_unknown_preset_exits_with_an_error(self, monkeypatch, capsys):
        import scripts.preset_render as preset_render
        from scripts.preset_lint import print_formulas

        monkeypatch.setattr(preset_render, "load_all_presets", lambda presets_root=None: ([], {}))

        assert print_formulas("missing") == 1
