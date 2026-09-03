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

    def test_renaming_a_chain_node_drops_it_from_collection_but_the_walk_continues_through_it(self):
        """The walk backward from the sampler follows the `model` connection
        structurally regardless of class_type - only *collecting* a node as
        a LoRA still depends on its class_type starting with `LoraLoader`
        (see `suggest._detect_lora_chain`'s docstring on walking through
        pass-through patcher nodes). Node 101 is the bottom of the chain
        (closest to the checkpoint loader); un-marking it as a LoRA loader
        removes it from `lora_node_ids`, but the walk still passes through
        it structurally to find the real loader node "4"."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        workflow.node("101").class_type = "SomethingElse"
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain.lora_node_ids == ["102"]
        assert analysis.lora_chain.source_node_id == "4"

    def test_renaming_the_node_closest_to_the_sampler_still_finds_the_rest_of_the_chain(self):
        """Node 102 is what the sampler's `model` input connects to
        directly. Renaming it away from a LoRA class no longer defeats
        detection entirely (the pre-patch-node-walk behavior): it's just
        walked through structurally like any other pass-through node, and
        node 101 further back is still found and collected."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        workflow.node("102").class_type = "SomethingElse"
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is not None
        assert analysis.lora_chain.lora_node_ids == ["101"]
        assert analysis.lora_chain.source_node_id == "4"
        assert analysis.lora_chain.target_node_id == "3"

    def test_bite_check_lora_chain_none_when_the_model_link_itself_is_broken(self):
        """Confirms detection can still fail outright: breaking the
        sampler's own `model` connection (not just renaming a downstream
        node's class) means there is nothing to walk at all."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        workflow.node("3").inputs["model"] = "not-a-connection"
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is None

    def test_lora_behind_pass_through_patch_nodes_is_detected(self):
        """UNETLoader -> LoraLoaderModelOnly -> ModelSamplingAuraFlow ->
        CFGNorm -> KSampler: the maintainer's actual case (a template's own
        LoRA node sits behind pass-through patcher nodes the old sampler-
        adjacency-only walk never looked past)."""
        workflow = parse_api_workflow(_load("lora_chain_patch_node_api.json"))
        analysis = suggest_fields(workflow)
        chain = analysis.lora_chain
        assert chain is not None
        assert chain.lora_node_ids == ["20"]
        assert chain.source_node_id == "1"
        assert chain.target_node_id == "3"
        assert chain.has_clip_path is False

        node = chain.nodes[0]
        assert node.node_id == "20"
        assert node.class_type == "LoraLoaderModelOnly"
        assert node.lora_name == "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors"
        assert node.strength_model == 1.0
        assert node.strength_clip is None
        assert node.model_source == ("1", 0)
        # The consumer is the first pass-through node, NOT the sampler -
        # splicing must rewire node 21, the actual thing that read node 20's
        # MODEL output, not jump straight to the sampler.
        assert node.model_consumer == ("21", "model")

    def test_bite_check_patch_node_walk_breaks_if_it_stops_at_the_first_non_lora_node(self):
        """Confirms the assertion above can fail: with the walk stopping as
        soon as it meets a non-LoRA class (the pre-fix behavior), nothing
        behind CFGNorm/ModelSamplingAuraFlow would ever be found."""
        workflow = parse_api_workflow(_load("lora_chain_patch_node_api.json"))
        sampler = workflow.node("3")
        conn = sampler.connection_source("model")
        current = workflow.resolve(conn)
        assert not current.class_type.startswith("LoraLoader")  # CFGNorm - the old walk would stop right here

    def test_lora_chain_with_clip_path_and_a_lora_behind_a_patch_node(self):
        """LoraLoader (model+clip) -> ModelSamplingAuraFlow -> LoraLoaderModelOnly
        -> KSampler, with both CLIPTextEncode nodes reading CLIP from the
        LoraLoader directly (fan-out to positive AND negative)."""
        workflow = parse_api_workflow(_load("lora_chain_clip_patch_api.json"))
        analysis = suggest_fields(workflow)
        chain = analysis.lora_chain
        assert chain is not None
        assert chain.lora_node_ids == ["101", "102"]
        assert chain.source_node_id == "4"
        assert chain.target_node_id == "3"
        assert chain.has_clip_path is True

        by_id = {n.node_id: n for n in chain.nodes}
        lora_loader = by_id["101"]
        assert lora_loader.class_type == "LoraLoader"
        assert lora_loader.strength_clip == 0.7
        assert lora_loader.model_source == ("4", 0)
        assert lora_loader.model_consumer == ("150", "model")  # the patch node, not node 102 directly
        assert lora_loader.clip_source == ("4", 1)
        assert set(lora_loader.clip_consumers) == {("6", "clip"), ("7", "clip")}

        model_only = by_id["102"]
        assert model_only.class_type == "LoraLoaderModelOnly"
        assert model_only.strength_clip is None
        assert model_only.model_source == ("150", 0)
        assert model_only.model_consumer == ("3", "model")
        assert model_only.clip_source is None
        assert model_only.clip_consumers == []
