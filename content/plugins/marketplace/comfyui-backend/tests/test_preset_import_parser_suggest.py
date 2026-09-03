"""Parser + structural-suggestion tests for the workflow importer.

Fixtures live in tests/fixtures/*.json (Export/API-format ComfyUI workflows).
"""

import json
from pathlib import Path

import pytest

from backend.preset_import.parser import WorkflowFormatError, parse_api_workflow
from backend.preset_import.suggest import suggest_fields

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


class TestParseApiWorkflow:
    def test_rejects_ui_format_with_nodes_and_links(self):
        ui_export = {"nodes": [{"id": 1, "type": "KSampler"}], "links": [[1, 1, 0, 2, 0]]}
        with pytest.raises(WorkflowFormatError, match="Export \\(API\\)"):
            parse_api_workflow(ui_export)

    def test_rejects_empty_object(self):
        with pytest.raises(WorkflowFormatError):
            parse_api_workflow({})

    def test_parses_sdxl_basic(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        assert set(workflow.nodes) == {"3", "4", "5", "6", "7", "8", "9"}
        sampler = workflow.node("3")
        assert sampler.class_type == "KSampler"
        assert sampler.connection_source("model") == ("4", 0)
        assert sampler.literals()["seed"] == 619589674328597

    def test_preserves_subgraph_node_ids(self):
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        assert "92:40" in workflow.nodes
        assert "92:11" in workflow.nodes
        sampler = workflow.node("92:40")
        assert sampler.connection_source("model") == ("92:11", 0)


class TestSuggestFields:
    def test_role_detection_via_sampler_links_sdxl(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}

        assert analysis.mode == "txt2img"
        assert analysis.sampler_node_id == "3"

        assert by_role["seed"].node_id == "3"
        assert by_role["seed"].input_name == "seed"

        assert by_role["prompt_positive"].node_id == "6"
        assert by_role["prompt_positive"].input_name == "text"
        assert by_role["prompt_negative"].node_id == "7"

        assert by_role["checkpoint"].node_id == "4"
        assert by_role["checkpoint"].input_name == "ckpt_name"
        assert by_role["checkpoint"].current_value == "sdxlBase_v10.safetensors"

        assert by_role["resolution_width"].current_value == 832
        assert by_role["resolution_height"].current_value == 1216
        assert by_role["batch_size"].node_id == "5"

        assert by_role["steps"].current_value == 27
        assert by_role["cfg"].current_value == 4.0
        assert by_role["sampler"].suggested_field_type == "select"
        assert by_role["scheduler"].suggested_field_type == "select"
        assert by_role["denoise"].current_value == 1.0

        # No LoRA nodes and no LoadImage in this fixture
        assert analysis.lora_chain is None
        assert "lora_slot" not in by_role
        assert "image" not in by_role

    def test_subgraph_ids_preserved_in_candidates(self):
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}

        assert analysis.sampler_node_id == "92:40"
        assert by_role["seed"].node_id == "92:40"
        assert by_role["diffusion_model"].node_id == "92:11"
        assert by_role["diffusion_model"].input_name == "unet_name"
        assert by_role["clip"].node_id == "92:12"
        assert by_role["vae"].node_id == "92:10"

    def test_lora_chain_and_load_image_detected(self):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: [] for c in analysis.candidates}
        for c in analysis.candidates:
            by_role.setdefault(c.role, []).append(c)

        assert analysis.mode == "img2img"
        assert analysis.lora_chain is not None
        assert analysis.lora_chain.source_node_id == "4"
        assert analysis.lora_chain.target_node_id == "3"
        assert analysis.lora_chain.lora_node_ids == ["101", "102"]

        lora_candidates = by_role["lora_slot"]
        assert {c.node_id for c in lora_candidates} == {"101", "102"}
        assert all(c.suggested_field_name == "loras" for c in lora_candidates)

        image_candidates = by_role["image"]
        assert len(image_candidates) == 1
        assert image_candidates[0].node_id == "10"
        assert image_candidates[0].suggested_field_name == "source_image"

        # No EmptyLatentImage in this fixture (pure img2img via VAEEncode)
        assert "resolution_width" not in by_role
        assert "batch_size" not in by_role

    def test_bite_check_role_detection_breaks_when_disabled(self):
        """Sanity check that the assertions above can actually fail: if the
        sampler's positive/negative connections aren't followed, no prompt
        role is found at all."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        sampler = workflow.node("3")
        # Break the very link the detector follows
        sampler.inputs["positive"] = "not-a-connection"
        analysis = suggest_fields(workflow)
        roles = {c.role for c in analysis.candidates}
        assert "prompt_positive" not in roles
        assert "prompt_negative" in roles  # negative link untouched, still detected

    def test_bite_check_lora_chain_breaks_when_class_type_changed(self):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        # Node 101 is the bottom of the chain (closest to the checkpoint
        # loader); un-marking it as a LoRA loader truncates the backward walk
        # from the sampler to just node 102, with 101 now treated as the
        # (wrong) source loader - proving the walk is actually driven by
        # class_type, not just position.
        workflow.node("101").class_type = "SomethingElse"
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain.lora_node_ids == ["102"]
        assert analysis.lora_chain.source_node_id == "101"

    def test_bite_check_lora_chain_none_when_top_of_chain_renamed(self):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        # Node 102 is what the sampler's `model` input connects to directly;
        # renaming it means the backward walk never enters the chain at all.
        workflow.node("102").class_type = "SomethingElse"
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is None
