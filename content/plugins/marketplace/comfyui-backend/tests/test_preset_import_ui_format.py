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
"""

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from backend import api
from backend.preset_import.convert import extract_node_groups, graph_to_prompt
from backend.preset_import.emit import FieldChoice, emit_preset
from backend.preset_import.parser import WorkflowFormatError, is_ui_format, parse_api_workflow, parse_workflow
from backend.preset_import.suggest import suggest_fields

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

    def test_unknown_node_class_raises(self, object_info):
        ui = _load("ui_sdxl_basic.json")
        ui["nodes"][0]["type"] = "SomeUninstalledCustomSampler"
        with pytest.raises(WorkflowFormatError, match="object_info"):
            graph_to_prompt(ui, object_info)

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
        choices = [
            FieldChoice(node_id=c.node_id, input_name=c.input_name)
            for c in analysis.candidates if c.role == "checkpoint"
        ]

        result = emit_preset(
            workflow, choices, model_family="UiFormatTest", variant="v1",
            display_name="UI Format Test", dest_root=dest_root,
            object_info=object_info, ui_workflow=ui,
        )

        workflows_dir = result.preset_dir / "modes" / result.mode / "files" / "workflows"
        assert (workflows_dir / "txt2img.json").exists()
        ui_json_path = workflows_dir / "txt2img.ui.json"
        assert ui_json_path.exists()
        assert json.loads(ui_json_path.read_text()) == ui
        assert "UI-format export" in (result.preset_dir / "description.md").read_text()

    def test_group_titles_become_one_tab_each(self, object_info, dest_root):
        ui = _load("ui_group_custom_node.json")
        converted = graph_to_prompt(ui, object_info)
        workflow = parse_api_workflow(converted)
        node_groups = extract_node_groups(ui)
        analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
        choices = [
            FieldChoice(node_id=c.node_id, input_name=c.input_name)
            for c in analysis.candidates
            if c.role in ("checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise")
        ]

        result = emit_preset(
            workflow, choices, model_family="UiGroupTabsTest", variant="v1",
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

    def test_bite_check_no_groups_falls_back_to_one_advanced_tab(self, object_info, dest_root):
        ui = _load("ui_sdxl_basic.json")  # no groups
        workflow = parse_workflow(ui, object_info=object_info)
        analysis = suggest_fields(workflow, object_info=object_info)
        choices = [
            FieldChoice(node_id=c.node_id, input_name=c.input_name)
            for c in analysis.candidates if c.role in ("checkpoint", "steps", "cfg")
        ]

        result = emit_preset(
            workflow, choices, model_family="UiNoGroupsTest", variant="v1",
            display_name="UI No Groups Test", dest_root=dest_root,
            object_info=object_info, ui_workflow=ui,
        )

        tabs_dir = result.preset_dir / "modes" / result.mode / "tabs"
        assert (tabs_dir / "advanced.yml").exists()
        assert not (tabs_dir / "sampling.yml").exists()

    def test_emitted_ui_format_preset_renders_and_lints_clean(self):
        """End-to-end proof that a UI-format import produces a working
        preset: the emitted workflow renders through scripts/preset_render.py
        with the same field values as the equivalent API-format import."""
        object_info_data = _load("object_info_sdxl.json")
        ui = _load("ui_sdxl_basic.json")
        workflow = parse_workflow(ui, object_info=object_info_data)
        analysis = suggest_fields(workflow, object_info=object_info_data)
        choices = [
            FieldChoice(node_id=c.node_id, input_name=c.input_name)
            for c in analysis.candidates
            if c.role in ("checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise")
        ]

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"UiFormatImporterTest{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            result = emit_preset(
                workflow, choices, model_family=marker, variant="v1",
                display_name="UI Format Importer E2E Test", dest_root=local_root,
                object_info=object_info_data, ui_workflow=ui,
            )

            lint_proc = subprocess.run(
                [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            lint_output = lint_proc.stdout + lint_proc.stderr
            error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
            assert not error_lines or all("unknown type 'comfyui_" in line for line in error_lines), lint_output

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
        fields = [
            api.ImportFieldChoice(node_id=c["node_id"], input_name=c["input_name"])
            for c in analysis["candidates"] if c["role"] == "checkpoint"
        ]

        import_body = api.ImportWorkflowRequest(
            workflow=_load("ui_sdxl_basic.json"), fields=fields,
            model_family="RouteUiTest", variant="v1", display_name="Route UI Test",
        )
        result = await api.import_workflow(import_body, current_user=None)

        preset_dir = Path(result["path"])
        assert (preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.ui.json").exists()
