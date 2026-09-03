"""UI-format (`Workflow -> Export`/Save) ComfyUI workflow import: converting
to the Export (API) shape (backend/preset_import/convert.py), the
object_info-aware analyze/import routes (backend/api.py), and the
suggestion/emission enrichment that follows from having a live server's
/object_info in hand.

Fixtures live in tests/fixtures/*.json:
- ui_sdxl_basic.json / object_info_sdxl.json - a plain workflow that must
  convert to exactly sdxl_basic_api.json modulo `_meta`.
- ui_group_custom_node.json - adds a "Sampling" ComfyUI group and a
  non-core FaceDetailer node (also declared in object_info_sdxl.json).
- ui_converted_reroute.json - KSampler's `seed` widget converted to a
  socket, fed through a Reroute from a PrimitiveNode.
- ui_subgraph.json - the newest export shape (`definitions.subgraphs`): a
  single Flux-style subgraph instance, flattened to match flux_subgraph_api.json.
- ui_two_subgraph_instances.json - two instances of one "Prompt Encoder"
  subgraph (boundary input+output crossing, a promoted widget overriding
  the inner node's own default, per instance).
- ui_nested_subgraph.json - a subgraph instance whose own body contains an
  instance of another subgraph (`<outer>:<inner>:<node>` id chaining).
- ui_subgraph_cycle.json - a subgraph containing an instance of itself.
- ui_unknown_node_named_widgets.json - ui_sdxl_basic.json plus one node
  (`LTXFloatToInt`) absent from object_info_sdxl.json, with its widget
  named directly in `inputs[]` (a recent-frontend export).
- ui_unknown_node_positional_widgets.json - same extra node, but with no
  widget metadata in `inputs[]` at all (an older export).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from backend import api
from backend.preset_import.convert import (
    _widget_input_order,
    convert_graph,
    extract_node_groups,
    graph_to_prompt,
)
from backend.preset_import.emit import emit_preset
from backend.preset_import.parser import WorkflowFormatError, is_ui_format, parse_api_workflow, parse_workflow
from backend.preset_import.schema import ImportForm
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles, raw_form_dict_for_roles

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


@pytest.fixture()
def object_info() -> dict:
    return _load("object_info_sdxl.json")


class TestGraphToPrompt:
    def test_sdxl_basic_matches_the_api_fixture_modulo_meta(self, object_info):
        ui = _load("ui_sdxl_basic.json")
        api_fixture = _load("sdxl_basic_api.json")

        converted = graph_to_prompt(ui, object_info)

        assert set(converted) == set(api_fixture)
        for node_id, expected in api_fixture.items():
            actual = dict(converted[node_id])
            actual.pop("_meta", None)
            expected = dict(expected)
            expected.pop("_meta", None)
            assert actual == expected

    def test_title_falls_back_to_class_type_when_untitled(self, object_info):
        ui = _load("ui_sdxl_basic.json")
        del ui["nodes"][1]["title"]  # CheckpointLoaderSimple (id 4)
        converted = graph_to_prompt(ui, object_info)
        assert converted["4"]["_meta"]["title"] == "CheckpointLoaderSimple"

    def test_muted_node_is_dropped(self, object_info):
        ui = _load("ui_sdxl_basic.json")
        for node in ui["nodes"]:
            if node["id"] == 9:  # SaveImage
                node["mode"] = 2
        converted = graph_to_prompt(ui, object_info)
        assert "9" not in converted

    def test_bypassed_node_is_dropped_same_as_muted(self, object_info):
        ui = _load("ui_sdxl_basic.json")
        for node in ui["nodes"]:
            if node["id"] == 9:
                node["mode"] = 4
        converted = graph_to_prompt(ui, object_info)
        assert "9" not in converted

    def test_reroute_and_primitive_resolve_to_a_literal_seed(self, object_info):
        converted = graph_to_prompt(_load("ui_converted_reroute.json"), object_info)
        # The converted "seed" input is a socket in the UI graph (fed through
        # a Reroute from a PrimitiveNode), but the API shape it must become
        # carries the PrimitiveNode's own literal value, not a connection.
        assert converted["3"]["inputs"]["seed"] == 4242424242
        assert converted["3"]["inputs"]["model"] == ["4", 0]
        # Neither passthrough node is itself emitted.
        assert not any(n["class_type"] == "Reroute" for n in converted.values())
        assert not any(n["class_type"] == "PrimitiveNode" for n in converted.values())

    def test_group_and_custom_node_convert_correctly(self, object_info):
        converted = graph_to_prompt(_load("ui_group_custom_node.json"), object_info)
        assert converted["20"]["class_type"] == "FaceDetailer"
        assert converted["20"]["inputs"]["guide_size"] == 384.0
        assert converted["20"]["inputs"]["enabled"] is True
        assert converted["20"]["inputs"]["image"] == ["8", 0]

    def test_bite_check_widget_order_breaks_when_object_info_order_is_shuffled(self, object_info):
        """Confirms the widget-mapping assertions above can actually fail:
        object_info's declared input order drives which widgets_values slot
        is which name, so reordering EmptyLatentImage's declared inputs
        (moving batch_size ahead of width/height) scrambles the mapping."""
        shuffled = json.loads(json.dumps(object_info))
        required = shuffled["EmptyLatentImage"]["input"]["required"]
        shuffled["EmptyLatentImage"]["input"]["required"] = {
            "batch_size": required["batch_size"],
            "width": required["width"],
            "height": required["height"],
        }
        ui = _load("ui_sdxl_basic.json")
        converted = graph_to_prompt(ui, shuffled)
        # Node 5's widgets_values is [832, 1216, 1] (width, height, batch_size
        # in the *unshuffled* order); with the object_info order shuffled,
        # 832 now lands on batch_size instead of width.
        assert converted["5"]["inputs"]["batch_size"] == 832
        assert converted["5"]["inputs"]["width"] == 1216

    def test_bite_check_control_after_generate_skip_matters(self, object_info):
        """Confirms the skip in graph_to_prompt is actually doing something:
        without `control_after_generate: true` declared, the paired
        "randomize" string is treated as steps' own value instead of being
        skipped."""
        no_control = json.loads(json.dumps(object_info))
        del no_control["KSampler"]["input"]["required"]["seed"][1]["control_after_generate"]
        ui = _load("ui_sdxl_basic.json")
        converted = graph_to_prompt(ui, no_control)
        assert converted["3"]["inputs"]["seed"] == 619589674328597
        assert converted["3"]["inputs"]["steps"] == "randomize"  # wrong: proves the skip matters


class TestSubgraphFlattening:
    def test_flux_subgraph_matches_the_flux_fixture_modulo_meta(self, object_info):
        converted = graph_to_prompt(_load("ui_subgraph.json"), object_info)
        expected = _load("flux_subgraph_api.json")

        assert set(converted) == set(expected)
        for node_id, exp in expected.items():
            actual = dict(converted[node_id])
            actual.pop("_meta", None)
            exp = dict(exp)
            exp.pop("_meta", None)
            assert actual == exp

    def test_flattened_ids_are_instance_colon_inner(self, object_info):
        converted = graph_to_prompt(_load("ui_subgraph.json"), object_info)
        assert converted["92:40"]["class_type"] == "KSampler"
        assert converted["92:40"]["inputs"]["model"] == ["92:11", 0]

    def test_two_instances_of_one_subgraph_stay_independent(self, object_info):
        converted = graph_to_prompt(_load("ui_two_subgraph_instances.json"), object_info)

        # Boundary input crossing: each instance's own "clip" socket resolves
        # straight through to the checkpoint loader outside the subgraph.
        assert converted["200:100"]["inputs"]["clip"] == ["4", 1]
        assert converted["201:100"]["inputs"]["clip"] == ["4", 1]
        # Promoted widgets: each instance's own widgets_values overrode the
        # inner CLIPTextEncode's shared default ("placeholder default text"),
        # independently per instance.
        assert converted["200:100"]["inputs"]["text"] == "masterpiece, best quality"
        assert converted["201:100"]["inputs"]["text"] == "worst quality, low quality"
        # Boundary output crossing: KSampler's positive/negative pull from
        # each instance's own inner node, not from the instance id itself.
        assert converted["3"]["inputs"]["positive"] == ["200:100", 0]
        assert converted["3"]["inputs"]["negative"] == ["201:100", 0]
        assert not any(node_id in ("200", "201") for node_id in converted)

    def test_bite_check_link_rewiring_breaks_if_boundary_input_ignored(self, object_info):
        """Confirms the boundary-crossing assertions above can fail: if the
        instance's own input link were ignored (e.g. resolved as if slot 0
        had no link at all), the inner CLIPTextEncode would have no `clip`
        input, not a wrong-but-present one - so this checks resolution
        really depends on the instance's own `inputs[0].link`."""
        ui = _load("ui_two_subgraph_instances.json")
        for node in ui["nodes"]:
            if node["id"] == 200:
                node["inputs"][0]["link"] = None  # sever the boundary crossing
        converted = graph_to_prompt(ui, object_info)
        assert "clip" not in converted["200:100"]["inputs"]

    def test_nested_subgraph_chains_ids_and_resolves_across_two_levels(self, object_info):
        converted = graph_to_prompt(_load("ui_nested_subgraph.json"), object_info)
        assert converted["500:2:100"]["class_type"] == "CLIPTextEncode"
        assert converted["500:2:100"]["inputs"]["text"] == "a nested prompt"
        assert converted["500:2:100"]["inputs"]["clip"] == ["500:1", 1]
        assert converted["500:4"]["inputs"]["positive"] == ["500:2:100", 0]
        assert converted["500:4"]["inputs"]["negative"] == ["500:2:100", 0]
        assert not any(node_id in ("500:2",) for node_id in converted)

    def test_self_referencing_subgraph_raises(self, object_info):
        with pytest.raises(WorkflowFormatError, match="instance of itself"):
            graph_to_prompt(_load("ui_subgraph_cycle.json"), object_info)


class TestUnknownNodeDegradesInsteadOfFailing:
    """A node class absent from /object_info (an uninstalled custom node
    pack, or a workflow built against a different server) must never fail
    the whole import - see convert.py's module docstring: the whole point
    of importing is to see what's missing."""

    def test_named_widgets_from_the_export_are_used_directly(self, object_info):
        result = convert_graph(_load("ui_unknown_node_named_widgets.json"), object_info)
        assert result.prompt["99"]["class_type"] == "LTXFloatToInt"
        assert result.prompt["99"]["inputs"] == {"value": 3.5}
        assert result.unknown_nodes == [
            {"node_id": "99", "class_type": "LTXFloatToInt", "title": "Float To Int"}
        ]
        # The rest of the graph converts normally around it.
        assert result.prompt["3"]["class_type"] == "KSampler"

    def test_no_widget_names_at_all_falls_back_to_positional(self, object_info):
        result = convert_graph(_load("ui_unknown_node_positional_widgets.json"), object_info)
        assert result.prompt["99"]["inputs"] == {"widget_0": 3.5}
        assert result.unknown_nodes[0]["class_type"] == "LTXFloatToInt"

    def test_bite_check_named_widgets_are_not_accidentally_positional(self, object_info):
        """Confirms the first assertion can fail: without reading the
        export's own widget names, the unknown class's single FLOAT widget
        would come out under a synthetic name, not its real one."""
        result = convert_graph(_load("ui_unknown_node_named_widgets.json"), object_info)
        assert "widget_0" not in result.prompt["99"]["inputs"]

    def test_a_known_class_is_never_listed_as_unknown(self, object_info):
        result = convert_graph(_load("ui_sdxl_basic.json"), object_info)
        assert result.unknown_nodes == []

    @pytest.mark.asyncio
    async def test_import_succeeds_with_a_comfyui_node_requirement_and_a_warning(
        self, monkeypatch, tmp_path, object_info
    ):
        async def _fake_fetch(base_url):
            return object_info

        monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch)
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)

        ui = _load("ui_unknown_node_named_widgets.json")
        analyze_body = api.AnalyzeWorkflowRequest(workflow=ui)
        analysis = await api.analyze_workflow(analyze_body, current_user=None)
        assert analysis["unknown_nodes"] == [
            {"node_id": "99", "class_type": "LTXFloatToInt", "title": "Float To Int"}
        ]
        assert any("LTXFloatToInt" in w for w in analysis["warnings"])

        form = raw_form_dict_for_roles(analysis, {"checkpoint"})
        import_body = api.ImportWorkflowRequest(
            workflow=ui, form=form, model_family="UnknownNodeTest", variant="v1",
            display_name="Unknown Node Test",
        )
        result = await api.import_workflow(import_body, current_user=None)

        assert any("LTXFloatToInt" in w for w in result["lint"]["warnings"])
        preset_yml = yaml.safe_load((Path(result["path"]) / "preset.yml").read_text())
        assert {"type": "comfyui_node", "class_type": "LTXFloatToInt"} in preset_yml["requirements"]


class TestComboWidgetTypeShapes:
    """A COMBO widget's declared type isn't always the classic inline options
    list - a dynamically-populated or bundled dropdown can report its type as
    the bare string `"COMBO"` or as a dict (`{"type": "COMBO", "options":
    [...]}`). Either shape must still be recognized as a widget: excluding it
    doesn't just lose that one field, it shifts every widget declared after
    it onto the wrong `widgets_values` slot (see convert.py's module
    docstring and the real-export regression in TestRealKrea2Export below)."""

    def test_inline_options_list_is_recognized(self):
        class_info = {"input": {"required": {"model": [["a", "b"], {}]}}}
        assert _widget_input_order(class_info) == [("model", {})]

    def test_bare_combo_string_type_is_recognized(self):
        class_info = {"input": {"required": {"model": ["COMBO", {"options": ["a", "b"]}]}}}
        assert _widget_input_order(class_info) == [("model", {"options": ["a", "b"]})]

    def test_dict_shaped_combo_type_is_recognized(self):
        class_info = {"input": {"required": {"model": [{"type": "COMBO", "options": ["a", "b"]}, {}]}}}
        assert _widget_input_order(class_info) == [("model", {})]

    def test_a_real_socket_type_is_still_excluded(self):
        """Confirms the widened recognition isn't just "accept everything":
        a genuine link-only type (never a widget) must stay excluded."""
        class_info = {"input": {"required": {"model": ["MODEL", {}]}}}
        assert _widget_input_order(class_info) == []

    def test_dict_shaped_type_does_not_crash_on_the_scalar_membership_check(self):
        """Bite check: before recognizing a dict as a widget type outright,
        `type_spec in _WIDGET_SCALAR_TYPES` would run on it - a dict is
        unhashable, so this would raise TypeError rather than exclude it."""
        class_info = {"input": {"required": {"model": [{"type": "COMBO"}, {}]}}}
        _widget_input_order(class_info)  # must not raise


class TestRealKrea2Export:
    """The exact shape a maintainer's real UI-format import produced: a
    single all-in-one `Krea2ImageNode` (a real, bundled ComfyUI node - not a
    custom/unknown class) whose `model` COMBO is declared with type `"COMBO"`
    (not the classic inline options list). The old converter silently
    excluded `model` from the widget-name order, which didn't just drop
    `model` - it shifted `seed` onto `model`'s own raw value and dropped
    every widgets_values slot after that without a trace, collapsing an
    8-field node down to two, one of them wrong."""

    @pytest.fixture()
    def object_info(self) -> dict:
        return _load("object_info_krea2_real.json")

    @pytest.fixture()
    def ui(self) -> dict:
        return _load("ui_krea2_real.json")

    def test_node_count_matches_the_export(self, ui, object_info):
        # Both export nodes are live (mode 0, no Note/MarkdownNote, no
        # subgraph instances) - none should vanish during conversion.
        converted = graph_to_prompt(ui, object_info)
        assert len(converted) == len(ui["nodes"]) == 2

    def test_every_export_link_resolves_to_an_input_reference(self, ui, object_info):
        converted = graph_to_prompt(ui, object_info)
        for link_id, origin_id, origin_slot, target_id, target_slot, *_rest in ui["links"]:
            target_node = next(n for n in ui["nodes"] if n["id"] == target_id)
            input_name = target_node["inputs"][target_slot]["name"]
            assert converted[str(target_id)]["inputs"][input_name] == [str(origin_id), origin_slot]

    def test_combo_widget_is_no_longer_silently_dropped(self, ui, object_info):
        converted = graph_to_prompt(ui, object_info)
        inputs = converted["1"]["inputs"]
        assert inputs["prompt"].startswith("high fashion editorial")
        assert inputs["model"] == "Krea 2 Medium"

    def test_no_class_is_reported_unknown(self, ui, object_info):
        """Krea2ImageNode and SaveImage are both real, recognized classes -
        this isn't a missing/uninstalled custom node problem."""
        result = convert_graph(ui, object_info)
        assert result.unknown_nodes == []

    def test_bite_check_an_unrecognized_type_shape_still_drops_the_field(self, ui):
        """Confirms the fixture actually exercises a real risk, not a
        non-issue: a `model` type this converter genuinely can't identify as
        a widget (an arbitrary custom socket-shaped string) is still
        excluded, same as before the fix - proving `object_info_krea2_real
        .json`'s bare `"COMBO"` string is what needed the widened
        recognition, not something the converter always handled."""
        object_info = json.loads(json.dumps(_load("object_info_krea2_real.json")))
        object_info["Krea2ImageNode"]["input"]["required"]["model"] = ["KREA2_MODEL_SOCKET", {}]
        converted = graph_to_prompt(ui, object_info)
        assert "model" not in converted["1"]["inputs"]

    def test_trailing_widgets_values_are_no_longer_silently_lost(self, ui, object_info):
        """Before the fix, `model` being dropped from the widget-name order
        shifted `seed` onto `model`'s own raw value and then simply stopped -
        every widgets_values slot past that (quality, negative_prompt,
        denoise, the real seed, the randomize marker) never appeared in the
        emitted prompt at all. They still can't be positioned correctly
        (`/object_info` has no way to describe the sub-fields a real
        `model` choice bundles - see the module docstring), but they're no
        longer thrown away: every one of them shows up under a positional
        `widget_N` name, including the real seed value."""
        converted = graph_to_prompt(ui, object_info)
        inputs = converted["1"]["inputs"]
        raw_values = ui["nodes"][0]["widgets_values"]
        assert inputs["widget_4"] == raw_values[4] == "medium"
        assert inputs["widget_5"] == raw_values[5] == ""
        assert inputs["widget_6"] == raw_values[6] == 0.35
        assert inputs["widget_7"] == raw_values[7] == 1981045336  # the real seed
        assert inputs["widget_8"] == raw_values[8] == "randomize"

    def test_candidates_are_no_longer_empty(self, ui, object_info):
        """Item #3 of the bug report: import.json's `choices` came out empty
        because convert.py's own output was already collapsed to two
        (mis-valued) fields by the time suggest_fields ever saw it - fixing
        the conversion is what gives the wizard something to offer, even
        though this all-in-one node has no KSampler for suggest_fields'
        structural seed/sampler detection to key off (see the module
        docstring of suggest.py) - every field but `prompt` surfaces through
        its generic "everything else literal" fallback, at role "literal"
        rather than "seed". `prompt` is still caught, by the name-based
        fallback (`_fallback_prompt_roles`) that fires when structural
        prompt detection finds nothing at all - prompts must never be a
        choosable form field regardless of whether a sampler exists."""
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        analysis = suggest_fields(workflow, object_info=object_info)
        assert analysis.sampler_node_id is None
        by_input = {c.input_name: c for c in analysis.candidates if c.node_id == "1"}
        assert {"prompt", "model", "seed"} <= set(by_input)
        assert by_input["prompt"].role == "prompt_positive"
        assert all(c.role == "literal" for name, c in by_input.items() if name != "prompt")


class TestExtractNodeGroups:
    def test_nodes_inside_the_bounding_box_are_mapped(self):
        mapping = extract_node_groups(_load("ui_group_custom_node.json"))
        assert mapping.get("3") == "Sampling"

    def test_nodes_outside_any_group_are_absent(self):
        mapping = extract_node_groups(_load("ui_group_custom_node.json"))
        assert "20" not in mapping

    def test_no_groups_means_empty_mapping(self):
        assert extract_node_groups(_load("ui_sdxl_basic.json")) == {}


class TestParseWorkflow:
    def test_ui_format_without_object_info_raises_the_export_api_message(self):
        with pytest.raises(WorkflowFormatError, match="Export \\(API\\)"):
            parse_workflow(_load("ui_sdxl_basic.json"), object_info=None)

    def test_ui_format_with_object_info_converts_and_parses(self, object_info):
        workflow = parse_workflow(_load("ui_sdxl_basic.json"), object_info=object_info)
        assert workflow.node("3").class_type == "KSampler"
        assert workflow.node("3").connection_source("model") == ("4", 0)

    def test_api_format_is_unaffected_by_object_info(self):
        api_data = _load("sdxl_basic_api.json")
        assert parse_workflow(api_data) == parse_api_workflow(api_data)
        assert parse_workflow(api_data, object_info={"irrelevant": True}) == parse_api_workflow(api_data)

    def test_is_ui_format_detects_both_shapes(self):
        assert is_ui_format(_load("ui_sdxl_basic.json")) is True
        assert is_ui_format(_load("sdxl_basic_api.json")) is False


class TestSuggestEnrichmentFromObjectInfo:
    def test_combo_becomes_select_with_real_options(self, object_info):
        workflow = parse_workflow(_load("ui_sdxl_basic.json"), object_info=object_info)
        analysis = suggest_fields(workflow, object_info=object_info)
        by_role = {c.role: c for c in analysis.candidates}
        assert by_role["sampler"].suggested_config["options"] == [
            "euler", "euler_ancestral", "heun", "dpm_2", "dpm_2_ancestral", "lms", "dpmpp_2m",
        ]

    def test_numeric_bounds_come_from_the_server_not_the_generic_guess(self, object_info):
        workflow = parse_workflow(_load("ui_sdxl_basic.json"), object_info=object_info)
        analysis = suggest_fields(workflow, object_info=object_info)
        by_role = {c.role: c for c in analysis.candidates}
        # object_info's KSampler.steps is min=1/max=10000, not this importer's
        # generic 1-150 slider guess.
        assert by_role["steps"].suggested_config == {"min": 1, "max": 10000, "step": 1}

    def test_boolean_literal_becomes_checkbox(self, object_info):
        ui = _load("ui_group_custom_node.json")
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        analysis = suggest_fields(workflow, object_info=object_info)
        enabled = next(c for c in analysis.candidates if c.node_id == "20" and c.input_name == "enabled")
        assert enabled.suggested_field_type == "checkbox"

    def test_node_groups_set_suggested_tab(self, object_info):
        ui = _load("ui_group_custom_node.json")
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        node_groups = extract_node_groups(ui)
        analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
        by_role = {c.role: c for c in analysis.candidates}
        assert by_role["steps"].suggested_tab == "Sampling"
        literal = next(c for c in analysis.candidates if c.node_id == "20" and c.input_name == "guide_size")
        assert literal.suggested_tab is None  # FaceDetailer sits outside the group's bounding box

    def test_bite_check_no_object_info_means_no_enrichment(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}
        assert "options" not in by_role["sampler"].suggested_config
        assert by_role["sampler"].suggested_config["file"]["path"].endswith("samplers/all.yml")


class TestEmitFromUiFormat:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_ui_json_written_alongside_the_converted_workflow(self, object_info, dest_root):
        ui = _load("ui_sdxl_basic.json")
        workflow = parse_workflow(ui, object_info=object_info)
        analysis = suggest_fields(workflow, object_info=object_info)
        form = form_from_roles(analysis, {"checkpoint"})

        result = emit_preset(
            workflow, form, [], model_family="UiFormatTest", variant="v1",
            display_name="UI Format Test", dest_root=dest_root,
            object_info=object_info, ui_workflow=ui,
        )

        workflows_dir = result.preset_dir / "modes" / result.mode / "files" / "workflows"
        assert (workflows_dir / "txt2img.json").exists()
        ui_json_path = workflows_dir / "txt2img.ui.json"
        assert ui_json_path.exists()
        assert json.loads(ui_json_path.read_text()) == ui
        assert "UI-format export" in (result.preset_dir / "description.md").read_text()

    def test_a_dedicated_tab_gets_its_own_file(self, object_info, dest_root):
        """The form designer's tabs are admin-authored (not auto-grouped by
        the emitter any more - see `defaults.build_default_form` for the
        "one tab per ComfyUI group" *suggestion* this replaces): a form with
        a "Sampling" tab writes exactly one `sampling.yml` referenced from
        form.yml, alongside the Generation tab's own file."""
        ui = _load("ui_group_custom_node.json")
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        node_groups = extract_node_groups(ui)
        analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
        generation_form = form_from_roles(analysis, {"checkpoint"})
        sampling_form = form_from_roles(
            analysis, {"steps", "cfg", "sampler", "scheduler", "denoise"},
            tab_id="sampling", tab_label="Sampling",
        )
        form = ImportForm(tabs=[generation_form.tabs[0], sampling_form.tabs[0]])

        result = emit_preset(
            workflow, form, [], model_family="UiGroupTabsTest", variant="v1",
            display_name="UI Group Tabs Test", dest_root=dest_root,
            object_info=object_info, ui_workflow=ui,
        )

        tabs_dir = result.preset_dir / "modes" / result.mode / "tabs"
        assert (tabs_dir / "sampling.yml").exists()
        sampling_fields = yaml.safe_load((tabs_dir / "sampling.yml").read_text())["fields"]
        assert {f["name"] for f in sampling_fields} == {"steps", "cfg", "sampler_name", "scheduler", "denoise"}
        form_yml = yaml.safe_load((result.preset_dir / "modes" / result.mode / "form.yml").read_text())
        tab_labels = [t["label"] for t in form_yml["fields"][0]["children"]]
        assert "Sampling" in tab_labels

    def test_default_form_puts_everything_in_one_generation_tab_when_no_groups(self, object_info, dest_root):
        """`defaults.build_default_form` falls back to one "Generation" tab
        for a workflow with no ComfyUI groups drawn (see the module
        docstring of `defaults.py`) - no per-role tab split any more."""
        from backend.preset_import.defaults import build_default_form, build_default_history

        ui = _load("ui_sdxl_basic.json")  # no groups
        workflow = parse_workflow(ui, object_info=object_info)
        analysis = suggest_fields(workflow, object_info=object_info)
        form = build_default_form(analysis)
        history = build_default_history(form)
        assert [t.id for t in form.tabs] == ["generation"]

        result = emit_preset(
            workflow, form, history, model_family="UiNoGroupsTest", variant="v1",
            display_name="UI No Groups Test", dest_root=dest_root,
            object_info=object_info, ui_workflow=ui,
        )

        tabs_dir = result.preset_dir / "modes" / result.mode / "tabs"
        assert (tabs_dir / "generation.yml").exists()
        assert len(list(tabs_dir.glob("*.yml"))) == 1

    def test_emitted_ui_format_preset_renders_and_lints_clean(self):
        """End-to-end proof that a UI-format import produces a working
        preset: the emitted workflow renders through scripts/preset_render.py
        with the same field values as the equivalent API-format import."""
        object_info_data = _load("object_info_sdxl.json")
        ui = _load("ui_sdxl_basic.json")
        workflow = parse_workflow(ui, object_info=object_info_data)
        analysis = suggest_fields(workflow, object_info=object_info_data)
        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"})

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"UiFormatImporterTest{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            result = emit_preset(
                workflow, form, [], model_family=marker, variant="v1",
                display_name="UI Format Importer E2E Test", dest_root=local_root,
                object_info=object_info_data, ui_workflow=ui,
            )

            lint_proc = subprocess.run(
                [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            lint_output = lint_proc.stdout + lint_proc.stderr
            error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
            assert not error_lines, lint_output

            fixture_form = {
                "seed": 7, "quantity": 1,
                "checkpoint": "models/checkpoints/sdxlBase_v10.safetensors",
                "steps": 27, "cfg": 4.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            }
            form_file = local_root / f"{marker}_form.yml"
            form_file.write_text(yaml.safe_dump(fixture_form))
            try:
                render_proc = subprocess.run(
                    [
                        sys.executable, "scripts/preset_render.py",
                        result.preset_id, "txt2img", "--form", str(form_file), "--json",
                    ],
                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
                )
                assert render_proc.returncode == 0, render_proc.stdout + render_proc.stderr
                json_text = render_proc.stdout[render_proc.stdout.index("{"):]
                record = json.loads(json_text)
                assert "error" not in record
                comfyui_pipe_render = next(p for p in record["pipes"] if p["name"] == "comfyui")
                field_mappings = comfyui_pipe_render["config"]["field_mappings"]["value"]
                resolved = {m[1]: m[0] for m in field_mappings}
                assert resolved["4.inputs.ckpt_name"] == "sdxlBase_v10.safetensors"
                assert resolved["3.inputs.steps"] == 27
                assert resolved["3.inputs.sampler_name"] == "euler"
            finally:
                form_file.unlink(missing_ok=True)
        finally:
            shutil.rmtree(preset_family_dir, ignore_errors=True)


class TestApiRoutesAcceptUiFormat:
    """The /presets/import/analyze and /presets/import routes, called
    directly as coroutines (current_user is only a Depends placeholder the
    handlers never read - see test_preset_import_families.py for the same
    pattern). No real network: `_fetch_object_info` is monkeypatched."""

    @pytest.mark.asyncio
    async def test_analyze_ui_format_with_no_reachable_backend_returns_400(self, monkeypatch):
        async def _boom(base_url):
            raise TimeoutError("no route to host")

        monkeypatch.setattr(api, "_fetch_object_info", _boom)
        body = api.AnalyzeWorkflowRequest(workflow=_load("ui_sdxl_basic.json"))

        with pytest.raises(Exception) as exc_info:
            await api.analyze_workflow(body, current_user=None)
        assert "A reachable ComfyUI backend" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_analyze_ui_format_with_object_info_reports_format_and_usage(self, monkeypatch, object_info):
        async def _fake_fetch(base_url):
            return object_info

        monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch)
        body = api.AnalyzeWorkflowRequest(workflow=_load("ui_group_custom_node.json"))

        result = await api.analyze_workflow(body, current_user=None)

        assert result["format"] == "ui"
        assert result["object_info_used"] is True
        by_role = {c["role"]: c for c in result["candidates"]}
        assert by_role["steps"]["suggested_tab"] == "Sampling"
        assert by_role["sampler"]["suggested_config"]["options"]

    @pytest.mark.asyncio
    async def test_analyze_api_format_reports_format_without_object_info(self):
        body = api.AnalyzeWorkflowRequest(workflow=_load("sdxl_basic_api.json"))
        result = await api.analyze_workflow(body, current_user=None)
        assert result["format"] == "api"
        assert result["object_info_used"] is False

    @pytest.mark.asyncio
    async def test_import_ui_format_writes_ui_json_under_the_local_root(self, monkeypatch, tmp_path, object_info):
        async def _fake_fetch(base_url):
            return object_info

        monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch)
        monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", tmp_path)

        analyze_body = api.AnalyzeWorkflowRequest(workflow=_load("ui_sdxl_basic.json"))
        analysis = await api.analyze_workflow(analyze_body, current_user=None)
        form = raw_form_dict_for_roles(analysis, {"checkpoint"})

        import_body = api.ImportWorkflowRequest(
            workflow=_load("ui_sdxl_basic.json"), form=form,
            model_family="RouteUiTest", variant="v1", display_name="Route UI Test",
        )
        result = await api.import_workflow(import_body, current_user=None)

        preset_dir = Path(result["path"])
        assert (preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.ui.json").exists()


class TestEmittedTabsAreAllReferenced:
    """Regression for the emitted `children:` Jinja path picking PyYAML's
    default single-quote style: `tests/features/presets/test_references_tab_layout.py::
    test_no_tab_body_file_is_orphaned` finds which `tabs/*.yml` a form.yml
    composes by regexing for a DOUBLE-quoted `children: "..."` value (the
    hand-authored convention every other preset uses), so a single-quoted
    emission made every tab file this importer writes look orphaned - it
    still lints and renders clean, since the YAML parses to the same string
    either way; only the guard's own text-based scan cared about the quote
    character."""

    @staticmethod
    def _assert_all_tabs_referenced(preset_dir: Path) -> None:
        composed = set()
        for form in preset_dir.glob("modes/*/form.yml"):
            for ref in re.findall(r'children:\s*"([^"]+)"', form.read_text()):
                composed.add(os.path.normpath(ref.replace("{{ paths.preset }}", str(preset_dir))))
        tab_files = list(preset_dir.glob("modes/*/tabs/*.yml"))
        assert tab_files, "no tab bodies were emitted - fixture no longer exercises the bug"
        orphans = [f for f in tab_files if os.path.normpath(str(f)) not in composed]
        assert orphans == [], f"tab bodies no form.yml composes: {orphans}"

    def test_group_tabs_all_referenced_and_preset_lints_clean(self, object_info, tmp_path):
        ui = _load("ui_group_custom_node.json")
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        node_groups = extract_node_groups(ui)
        analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"})

        result = emit_preset(
            workflow, form, [], model_family="TabReferenceTest", variant="v1",
            display_name="Tab Reference Test", dest_root=tmp_path / "presets",
            object_info=object_info, ui_workflow=ui,
        )

        self._assert_all_tabs_referenced(result.preset_dir)

        lint_proc = subprocess.run(
            [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        lint_output = lint_proc.stdout + lint_proc.stderr
        error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
        assert not error_lines or all("unknown type 'comfyui_" in line for line in error_lines), lint_output

    def test_real_ltx25_img2img_export_tabs_all_referenced_lints_and_renders(self):
        """The exact shape a maintainer's real UI-format import produced:
        five nested `definitions.subgraphs` instances, two ComfyUI groups,
        and a `LoadImage` whose widget name comes from `object_info` alone
        (no `inputs[]` widget markers recorded in this export). Placed under
        `content/presets/local` (cleaned up after) because `preset_render.py`
        resolves a preset by id through the normal loader, same as
        `test_emitted_ui_format_preset_renders_and_lints_clean` above."""
        ui = _load("ui_ltx25_img2img_real.json")
        object_info_data = _load("object_info_ltx25_img2img_real.json")
        workflow = parse_workflow(ui, object_info=object_info_data)
        analysis = suggest_fields(workflow, object_info=object_info_data)
        form = form_from_roles(analysis, {"image"})
        assert form.tabs[0].items, "the real export's LoadImage no longer resolves to an image candidate"

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"LtxRealImportTest{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            result = emit_preset(
                workflow, form, [], model_family=marker, variant="v1",
                display_name="LTX Real Import Test", dest_root=local_root,
                object_info=object_info_data, ui_workflow=ui,
            )

            self._assert_all_tabs_referenced(result.preset_dir)

            lint_proc = subprocess.run(
                [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            lint_output = lint_proc.stdout + lint_proc.stderr
            error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
            assert not error_lines or all("unknown type 'comfyui_" in line for line in error_lines), lint_output

            render_proc = subprocess.run(
                [sys.executable, "scripts/preset_render.py", result.preset_id, result.mode, "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            assert render_proc.returncode == 0, render_proc.stdout + render_proc.stderr
            json_text = render_proc.stdout[render_proc.stdout.index("{"):]
            record = json.loads(json_text)
            assert "error" not in record, record
        finally:
            shutil.rmtree(preset_family_dir, ignore_errors=True)
