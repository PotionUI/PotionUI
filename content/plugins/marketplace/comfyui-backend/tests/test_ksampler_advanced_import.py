"""`KSamplerAdvanced`'s four literal-only inputs (`add_noise`, `start_at_step`,
`end_at_step`, `return_with_leftover_noise`) - before this catalog entry
described them, they surfaced only as untyped generic "literal" candidates
(see `suggest.suggest_fields`'s "everything else literal" pass) and never
made it into the default form. Also covers a base+refiner two-stage
pipeline (two `KSamplerAdvanced` nodes), which needs `suggest.suggest_fields`
to catalogue a sampler-category node beyond the one `_find_sampler` picks as
the workflow's primary sampler - see the comment on that loop.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend.preset_import.defaults import build_default_form, build_default_history
from backend.preset_import.emit import emit_preset
from backend.preset_import.node_catalog import load_catalog
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


class TestKSamplerAdvancedCatalogEntry:
    def test_four_advanced_inputs_are_catalogued(self):
        entry = load_catalog().get("KSamplerAdvanced")
        assert entry is not None
        for input_name in ("add_noise", "start_at_step", "end_at_step", "return_with_leftover_noise"):
            assert input_name in entry.inputs, input_name

    def test_add_noise_and_return_with_leftover_noise_are_selects_with_the_exact_wire_strings(self):
        entry = load_catalog().get("KSamplerAdvanced")
        for input_name, label in (
            ("add_noise", "Add noise"),
            ("return_with_leftover_noise", "Return with leftover noise"),
        ):
            spec = entry.inputs[input_name]
            assert spec.field == "select"
            assert spec.role == "option"
            assert spec.config["options"] == ["enable", "disable"]
            assert spec.label == label
            assert spec.section == "Advanced Sampling"

    def test_step_range_inputs_admit_the_full_0_to_10000_range(self):
        entry = load_catalog().get("KSamplerAdvanced")
        for input_name, label in (("start_at_step", "Start at step"), ("end_at_step", "End at step")):
            spec = entry.inputs[input_name]
            assert spec.field == "integer"
            assert spec.role == "option"
            assert spec.config["min"] == 0
            assert spec.config["max"] >= 10000
            assert spec.label == label
            assert spec.section == "Advanced Sampling"

    def test_standard_ksampler_has_no_advanced_sampling_inputs(self):
        entry = load_catalog().get("KSampler")
        assert entry is not None
        assert "add_noise" not in entry.inputs
        assert "start_at_step" not in entry.inputs
        assert "end_at_step" not in entry.inputs
        assert "return_with_leftover_noise" not in entry.inputs


class TestKSamplerAdvancedSuggest:
    def _advanced_candidates(self, analysis, node_id="3"):
        return {c.input_name: c for c in analysis.candidates if c.node_id == node_id and c.section == "Advanced Sampling"}

    def test_defaults_and_field_types_are_preserved_exactly(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        analysis = suggest_fields(workflow)
        by_input = self._advanced_candidates(analysis)

        assert by_input["add_noise"].current_value == "disable"
        assert by_input["add_noise"].suggested_field_type == "select"
        assert by_input["return_with_leftover_noise"].current_value == "enable"
        assert by_input["return_with_leftover_noise"].suggested_field_type == "select"
        assert by_input["start_at_step"].current_value == 5
        assert by_input["start_at_step"].suggested_field_type == "integer"
        # The 9990 value here is deliberately close to ComfyUI's 10000
        # "run to the end" sentinel - it must survive untouched, not get
        # rounded or clamped down.
        assert by_input["end_at_step"].current_value == 9990
        assert by_input["end_at_step"].suggested_field_type == "integer"

    def test_advanced_candidates_are_obvious_and_land_in_one_section(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        analysis = suggest_fields(workflow)
        by_input = self._advanced_candidates(analysis)
        assert len(by_input) == 4
        assert all(c.obvious for c in by_input.values())

    def test_default_form_has_an_advanced_sampling_section_with_the_four_fields(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        analysis = suggest_fields(workflow)
        form = build_default_form(analysis)
        sections = {i.title: i for i in form.tabs[0].items if i.kind == "section"}
        assert "Advanced Sampling" in sections
        assert {f.field_name for f in sections["Advanced Sampling"].items} == {
            "add_noise", "start_at_step", "end_at_step", "return_with_leftover_noise",
        }

    def test_standard_ksampler_default_form_has_no_advanced_sampling_section(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = build_default_form(analysis)
        sections = {i.title: i for i in form.tabs[0].items if i.kind == "section"}
        assert "Advanced Sampling" not in sections

    def test_a_linked_end_at_step_produces_no_control(self):
        """An input the source workflow wires to another node (rather than a
        literal) must never surface as a candidate at all - `node.literals()`
        excludes it, same as any other catalogued input."""
        raw = _load("ksampler_advanced_api.json")
        raw["3"]["inputs"]["end_at_step"] = ["5", 0]  # pretend something feeds it
        workflow = parse_api_workflow(raw)
        analysis = suggest_fields(workflow)
        assert not any(c.node_id == "3" and c.input_name == "end_at_step" for c in analysis.candidates)


class TestKSamplerAdvancedTwoStage:
    def test_each_stage_gets_its_own_field_names_and_values(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_two_stage_api.json"))
        analysis = suggest_fields(workflow)
        by_key = {(c.node_id, c.input_name): c for c in analysis.candidates}

        base_end = by_key[("3", "end_at_step")]
        refiner_start = by_key[("10", "start_at_step")]
        assert base_end.suggested_field_name == "end_at_step"
        assert base_end.current_value == 20
        assert refiner_start.suggested_field_name == "start_at_step_2"
        assert refiner_start.current_value == 20

        base_add_noise = by_key[("3", "add_noise")]
        refiner_add_noise = by_key[("10", "add_noise")]
        assert base_add_noise.suggested_field_name == "add_noise"
        assert base_add_noise.current_value == "enable"
        assert refiner_add_noise.suggested_field_name == "add_noise_2"
        assert refiner_add_noise.current_value == "disable"

    def test_no_duplicate_field_names_across_both_stages(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_two_stage_api.json"))
        analysis = suggest_fields(workflow)
        advanced = [c for c in analysis.candidates if c.section == "Advanced Sampling"]
        names = [c.suggested_field_name for c in advanced]
        assert len(names) == len(set(names))
        assert len(advanced) == 8  # 4 controls x 2 stages

    def test_linked_inputs_between_the_two_stages_stay_linked(self):
        """The refiner's `latent_image` reads the base sampler's own output
        (node 3) - a connection, not a literal - and must never become a
        control."""
        workflow = parse_api_workflow(_load("ksampler_advanced_two_stage_api.json"))
        analysis = suggest_fields(workflow)
        assert not any(c.node_id == "10" and c.input_name == "latent_image" for c in analysis.candidates)

    def test_default_form_advanced_sampling_section_has_all_eight_fields(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_two_stage_api.json"))
        analysis = suggest_fields(workflow)
        form = build_default_form(analysis)
        sections = {i.title: i for i in form.tabs[0].items if i.kind == "section"}
        assert {f.field_name for f in sections["Advanced Sampling"].items} == {
            "add_noise", "start_at_step", "end_at_step", "return_with_leftover_noise",
            "add_noise_2", "start_at_step_2", "end_at_step_2", "return_with_leftover_noise_2",
        }


class TestKSamplerAdvancedEmit:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_field_mappings_round_trip_the_wire_values_exactly(self, dest_root):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "option"})

        result = emit_preset(
            workflow, form, [], model_family="KSamplerAdvancedTest", variant="v1",
            display_name="KSampler Advanced Test", dest_root=dest_root,
        )

        pipeline = yaml.safe_load((result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text())
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        mappings = comfyui_pipe["configuration"]["field_mappings"]

        add_noise = next(m for m in mappings if m[1] == "3.inputs.add_noise")
        assert "disable" in add_noise[0]  # the source workflow's own default, verbatim
        assert add_noise[2] == "str"

        return_leftover = next(m for m in mappings if m[1] == "3.inputs.return_with_leftover_noise")
        assert "enable" in return_leftover[0]
        assert return_leftover[2] == "str"

        end_at_step = next(m for m in mappings if m[1] == "3.inputs.end_at_step")
        assert "9990" in end_at_step[0]
        assert end_at_step[2] == "int"

        generation_form = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )
        add_noise_field = next(f for f in generation_form["fields"] if f.get("name") == "add_noise")
        assert add_noise_field["type"] == "select"
        assert add_noise_field["default"] == "disable"
        end_at_step_field = next(f for f in generation_form["fields"] if f.get("name") == "end_at_step")
        assert end_at_step_field["type"] == "integer"
        assert end_at_step_field["default"] == 9990

    def test_two_stage_mappings_target_the_correct_node_and_never_cross_over(self, dest_root):
        workflow = parse_api_workflow(_load("ksampler_advanced_two_stage_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "option"})

        result = emit_preset(
            workflow, form, [], model_family="KSamplerAdvancedTwoStageTest", variant="v1",
            display_name="KSampler Advanced Two Stage Test", dest_root=dest_root,
        )

        pipeline = yaml.safe_load((result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text())
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        mappings = comfyui_pipe["configuration"]["field_mappings"]

        base_end = next(m for m in mappings if m[1] == "3.inputs.end_at_step")
        refiner_start = next(m for m in mappings if m[1] == "10.inputs.start_at_step")
        assert "20" in base_end[0]
        assert "20" in refiner_start[0]
        # Distinct target node ids - retargeting one field's own mapping
        # (e.g. editing the "start_at_step_2" field) can never touch node 3.
        targets = {m[1] for m in mappings if "add_noise" in m[1] or "start_at_step" in m[1] or "end_at_step" in m[1] or "return_with_leftover_noise" in m[1]}
        assert targets == {
            "3.inputs.add_noise", "3.inputs.start_at_step", "3.inputs.end_at_step", "3.inputs.return_with_leftover_noise",
            "10.inputs.add_noise", "10.inputs.start_at_step", "10.inputs.end_at_step", "10.inputs.return_with_leftover_noise",
        }


class TestKSamplerAdvancedObjectInfoEnrichment:
    def test_offline_catalog_fallback_is_used_without_object_info(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        analysis = suggest_fields(workflow)
        add_noise = next(c for c in analysis.candidates if c.node_id == "3" and c.input_name == "add_noise")
        assert add_noise.suggested_config == {"options": ["enable", "disable"]}

    def test_live_object_info_supplies_the_real_enum_option_list(self):
        workflow = parse_api_workflow(_load("ksampler_advanced_api.json"))
        object_info = {
            "KSamplerAdvanced": {
                "input": {
                    "required": {
                        "add_noise": [["enable", "disable"]],
                        "return_with_leftover_noise": [["enable", "disable"]],
                        "start_at_step": ["INT", {"default": 0, "min": 0, "max": 10000}],
                        "end_at_step": ["INT", {"default": 10000, "min": 0, "max": 10000}],
                    }
                }
            }
        }
        analysis = suggest_fields(workflow, object_info=object_info)
        by_input = {c.input_name: c for c in analysis.candidates if c.node_id == "3"}

        assert by_input["add_noise"].suggested_config == {"options": ["enable", "disable"]}
        assert by_input["add_noise"].suggested_field_type == "select"

        # A real min/max merges into the config but must never flip an
        # "integer" field into a "slider" the way it would for "number"
        # (see suggest._enrich_with_object_info) - and the 10000 sentinel
        # is far below the unbounded-bound guard, so it must survive.
        assert by_input["end_at_step"].suggested_field_type == "integer"
        assert by_input["end_at_step"].suggested_config["max"] == 10000
