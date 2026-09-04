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


class TestLoraChainThroughARuntimeSwitch:
    """`ComfySwitchNode`'s `on_true`/`on_false` are `passthrough`-tagged
    (see `node_catalog.NodeEntry.branch`) - the model-chain walk must follow
    whichever side the switch's own `switch` literal selects, not stop dead
    at the first node without an input literally named `model` (the
    maintainer's real case: a switch toggling a Lightning LoRA on/off)."""

    def test_switch_true_follows_on_true_into_the_lora(self):
        workflow = parse_api_workflow(_load("switch_lora_api.json"))
        analysis = suggest_fields(workflow)

        assert analysis.model_chain is not None
        chain = analysis.lora_chain
        assert chain is not None
        assert chain.lora_node_ids == ["20"]
        assert chain.source_node_id == "4"
        assert chain.target_node_id == "3"

        node = chain.nodes[0]
        assert node.class_type == "LoraLoaderModelOnly"
        assert node.model_source == ("4", 0)
        # The consumer is the switch's own on_true input, not the sampler -
        # splicing must rewire the switch, not jump straight past it.
        assert node.model_consumer == ("30", "on_true")

        by_role = {c.role: c for c in analysis.candidates}
        assert by_role["option"].node_id == "30"
        assert by_role["option"].input_name == "switch"
        assert by_role["option"].suggested_field_type == "checkbox"

    def test_switch_false_follows_on_false_and_finds_no_lora(self):
        """Confirms the walk actually reads the switch's own value: flipping
        the same fixture's literal to false must lose the LoRA entirely (it
        walks into the raw checkpoint loader instead) while the model chain
        itself is still detected."""
        workflow = parse_api_workflow(_load("switch_lora_api.json"))
        workflow.node("30").inputs["switch"] = False

        analysis = suggest_fields(workflow)

        assert analysis.model_chain is not None
        assert analysis.lora_chain is None

    def test_bite_check_switch_walk_breaks_without_the_branch_rule(self):
        """Confirms the assertions above can fail: without branch/passthrough
        resolution, the old hardcoded `.connection_source("model")` lookup
        finds nothing on the switch node (it has no input literally named
        `model`) and the walk stops there."""
        workflow = parse_api_workflow(_load("switch_lora_api.json"))
        switch_node = workflow.node("30")
        assert switch_node.connection_source("model") is None


class TestCustomSamplingGraphViaNodeCatalog:
    """A SamplerCustomAdvanced-based Flux graph (RandomNoise + KSamplerSelect
    + BasicScheduler + BasicGuider, FluxGuidance, ModelSamplingFlux, one
    LoraLoaderModelOnly) - the class of workflow the node catalog exists to
    recognize without naming these classes directly in suggest.py."""

    def test_sampler_and_sampling_cluster_are_found(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)

        assert analysis.sampler_node_id == "40"
        assert set(analysis.sampling_cluster_node_ids) == {"40", "30", "31", "32", "22"}

    def test_seed_steps_scheduler_denoise_sampler_come_from_the_cluster(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}

        assert by_role["seed"].node_id == "30" and by_role["seed"].input_name == "noise_seed"
        assert by_role["sampler"].node_id == "31" and by_role["sampler"].input_name == "sampler_name"
        assert by_role["scheduler"].node_id == "32" and by_role["scheduler"].input_name == "scheduler"
        assert by_role["steps"].node_id == "32" and by_role["steps"].current_value == 20
        assert by_role["denoise"].node_id == "32" and by_role["denoise"].current_value == 1.0
        for role in ("steps", "scheduler", "denoise", "sampler"):
            assert by_role[role].section == "Sampling"

    def test_guidance_and_shift_come_from_modifier_nodes_anywhere_in_the_graph(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}

        assert by_role["guidance"].node_id == "21" and by_role["guidance"].current_value == 3.5
        assert by_role["guidance"].section == "Sampling"
        assert by_role["shift"].node_id == "11" and by_role["shift"].input_name == "max_shift"
        assert by_role["shift"].section == "Sampling"

    def test_prompt_positive_resolves_through_flux_guidance_to_the_text_node(self):
        """CLIPTextEncode(20) -> FluxGuidance(21) -> BasicGuider(22): the
        prompt walk must follow FluxGuidance's own `prompt_positive`-kind
        link rather than stopping at it (it has no `text`/`prompt` literal
        of its own)."""
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        positive = next(c for c in analysis.candidates if c.role == "prompt_positive")
        assert positive.node_id == "20"
        assert positive.input_name == "text"
        assert positive.current_value == "a cat astronaut"

    def test_resolution_and_batch_come_from_the_sd3_latent(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}

        assert by_role["resolution_width"].node_id == "5" and by_role["resolution_width"].current_value == 1024
        assert by_role["resolution_height"].node_id == "5" and by_role["resolution_height"].current_value == 1024
        assert by_role["batch_size"].node_id == "5"

    def test_three_loaders_and_one_lora_slot_are_found(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: [] for c in analysis.candidates}
        for c in analysis.candidates:
            by_role.setdefault(c.role, []).append(c)

        assert by_role["diffusion_model"][0].node_id == "1"
        assert {c.node_id for c in by_role["clip"]} == {"2"}
        assert {c.suggested_field_name for c in by_role["clip"]} == {"clip", "clip_2"}
        assert by_role["vae"][0].node_id == "3"

        lora_candidates = by_role["lora_slot"]
        assert {c.node_id for c in lora_candidates} == {"10"}
        assert all(c.suggested_field_name == "loras" for c in lora_candidates)

        assert analysis.lora_chain is not None
        assert analysis.lora_chain.lora_node_ids == ["10"]
        assert analysis.lora_chain.source_node_id == "1"

    def test_bite_check_sampling_cluster_walk_breaks_without_the_sampling_link(self):
        """Confirms the assertions above can fail: severing
        SamplerCustomAdvanced's own `guider` connection drops BasicGuider(22)
        out of the sampling cluster entirely. The LoRA-chain walk still finds
        a `model_chain` link (BasicScheduler(32) carries one too), but now
        starts from THAT node instead - proving the start node really comes
        from cluster membership, not a hardcoded "the guider" assumption."""
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        workflow.node("40").inputs["guider"] = "not-a-connection"
        analysis = suggest_fields(workflow)
        assert "22" not in analysis.sampling_cluster_node_ids
        assert set(analysis.sampling_cluster_node_ids) == {"40", "30", "31", "32"}
        assert analysis.lora_chain is not None
        assert analysis.lora_chain.target_node_id == "32"


class TestModelChainDetection:
    """`AnalyzeResult.model_chain` (`suggest.ModelChainInfo`) - the sampling
    cluster's own model-chain boundary, populated independent of whether a
    LoRA chain exists at all (unlike `lora_chain`, which is `None` for a
    workflow with no LoRA node)."""

    def test_no_lora_chain_still_finds_the_model_chain(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is None
        assert analysis.model_chain is not None
        assert (
            analysis.model_chain.source_node_id,
            analysis.model_chain.source_output_index,
            analysis.model_chain.target_node_id,
            analysis.model_chain.target_input,
        ) == ("4", 0, "3", "model")
        assert analysis.to_dict()["model_chain"] == {
            "source_node_id": "4",
            "source_output_index": 0,
            "target_node_id": "3",
            "target_input": "model",
        }

    def test_model_chain_tracks_the_cluster_node_that_actually_carries_it(self):
        """Whatever `_model_chain_start` picks as the cluster's own
        model-chain consumer (BasicGuider(22), not BasicScheduler(32), for
        this fixture's default cluster) is `model_chain`'s target - the
        immediate connection, one hop back (ModelSamplingFlux(11)), not the
        chain walk's ultimate loader (UNETLoader(1))."""
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is not None
        assert analysis.model_chain is not None
        assert analysis.model_chain.target_node_id == "22"
        assert analysis.model_chain.source_node_id == "11"
        assert analysis.model_chain.source_output_index == 0

    def test_model_chain_is_none_when_nothing_in_the_cluster_consumes_a_model(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        workflow.node("3").inputs["model"] = "not-a-connection"
        analysis = suggest_fields(workflow)
        assert analysis.model_chain is None
        assert analysis.to_dict()["model_chain"] is None


class TestLtx25RealExportFindsItsSamplerViaCategory:
    """This fixture has NO connections at all (every input, including a
    sampler's own noise/seed, is a positional `widget_N` literal with no
    `object_info` to resolve it) - the sampler is still found by category,
    even though nothing further can be recovered from it."""

    def test_sampler_is_found_by_category_not_by_conditioning_links(self):
        workflow = parse_api_workflow(_load("ltx25_img2img_real_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.sampler_node_id == "5516:4829"
        assert workflow.node(analysis.sampler_node_id).class_type == "SamplerCustomAdvanced"

    def test_mode_is_img2img_from_the_load_image_node(self):
        workflow = parse_api_workflow(_load("ltx25_img2img_real_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.mode == "img2img"
        image = next(c for c in analysis.candidates if c.role == "image")
        assert image.node_id == "2004"


class TestModelFileComboEnrichment:
    """`_enrich_with_object_info` must never turn a model-name combo into a
    `select` field baking the live server's whole model directory into the
    preset as static options - see `suggest._model_file_type_for_combo`/
    `_looks_like_model_file_combo`."""

    def _workflow(self):
        raw = {
            "1": {
                "class_type": "MyCustomLoraApplyNode",
                "inputs": {"lora_name": "style_a.safetensors"},
                "_meta": {"title": "Custom LoRA Apply"},
            },
            "2": {
                "class_type": "MyCustomSamplerPickerNode",
                "inputs": {"sampler_name": "euler"},
                "_meta": {"title": "Custom Sampler Picker"},
            },
            "3": {
                "class_type": "MyCustomCheckpointPickerNode",
                "inputs": {"weights": "some_checkpoint.safetensors"},
                "_meta": {"title": "Custom Weights Picker"},
            },
        }
        return parse_api_workflow(raw)

    def test_lora_name_combo_by_name_becomes_a_model_field_with_no_options(self):
        workflow = self._workflow()
        object_info = {
            "MyCustomLoraApplyNode": {
                "input": {
                    "required": {
                        "lora_name": [["style_a.safetensors", "style_b.safetensors", "style_c.safetensors"]]
                    }
                }
            },
        }
        analysis = suggest_fields(workflow, object_info=object_info)
        candidate = next(c for c in analysis.candidates if c.node_id == "1" and c.input_name == "lora_name")

        assert candidate.suggested_field_type == "model"
        assert candidate.suggested_config == {"model_type": "lora", "allow_info_modal": True}
        assert "options" not in candidate.suggested_config
        assert candidate.suggested_transform == "strip_model_prefix"
        assert candidate.suggested_folder == "loras"

    def test_unrelated_combo_still_becomes_a_plain_select(self):
        """Confirms the assertion above is really keyed on the model-file
        detection, not "every combo becomes a model field": a combo with
        nothing model-shaped about it (name or values) must keep behaving
        exactly as before."""
        workflow = self._workflow()
        object_info = {
            "MyCustomSamplerPickerNode": {
                "input": {"required": {"sampler_name": [["euler", "dpmpp_2m"]]}}
            },
        }
        analysis = suggest_fields(workflow, object_info=object_info)
        candidate = next(c for c in analysis.candidates if c.node_id == "2" and c.input_name == "sampler_name")

        assert candidate.suggested_field_type == "select"
        assert candidate.suggested_config == {"options": ["euler", "dpmpp_2m"]}
        assert candidate.suggested_folder is None

    def test_a_combo_mostly_full_of_model_filenames_is_still_caught_by_name_alone(self):
        """The input name here (`weights`) isn't in the by-name mapping, but
        the combo's own values are almost all model filenames - the
        values-based fallback must still catch it and skip `options`."""
        workflow = self._workflow()
        object_info = {
            "MyCustomCheckpointPickerNode": {
                "input": {
                    "required": {
                        "weights": [
                            [
                                "some_checkpoint.safetensors",
                                "another_one.ckpt",
                                "a_third.safetensors",
                                "not_a_model_file",
                            ]
                        ]
                    }
                }
            },
        }
        analysis = suggest_fields(workflow, object_info=object_info)
        candidate = next(c for c in analysis.candidates if c.node_id == "3" and c.input_name == "weights")

        assert candidate.suggested_field_type == "model"
        assert "options" not in candidate.suggested_config
        assert candidate.suggested_folder is None  # unknown folder - no comfyui_model requirement guessed


class TestOffChainLoraNode:
    """A `lora`-category node the detected chain doesn't include - see
    `suggest._off_chain_lora_candidates`."""

    def _workflow(self):
        raw = {
            "4": {
                "inputs": {"ckpt_name": "sdxlBase_v10.safetensors"},
                "class_type": "CheckpointLoaderSimple",
                "_meta": {"title": "Load Checkpoint"},
            },
            "5": {
                "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
                "class_type": "EmptyLatentImage",
                "_meta": {"title": "Empty Latent Image"},
            },
            "6": {
                "inputs": {"text": "a cat", "clip": ["4", 1]},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Positive)"},
            },
            "7": {
                "inputs": {"text": "blurry", "clip": ["4", 1]},
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "CLIP Text Encode (Negative)"},
            },
            "3": {
                "inputs": {
                    "seed": 1, "steps": 20, "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple",
                    "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
                "class_type": "KSampler",
                "_meta": {"title": "KSampler"},
            },
            # Not on the model backbone at all - feeds nothing, just sits in
            # the graph (e.g. a second sampler's own chain in the real
            # workflow this mirrors).
            "50": {
                "inputs": {"lora_name": "unrelated.safetensors", "strength_model": 0.5, "model": ["4", 0]},
                "class_type": "LoraLoaderModelOnly",
                "_meta": {"title": "Detail LoRA"},
            },
        }
        return parse_api_workflow(raw)

    def test_off_chain_lora_becomes_a_model_field_not_a_lora_picker_slot(self):
        workflow = self._workflow()
        analysis = suggest_fields(workflow)

        assert analysis.lora_chain is None  # node 50 never reaches the sampler's model input

        candidate = next(c for c in analysis.candidates if c.node_id == "50" and c.input_name == "lora_name")
        assert candidate.role == "lora_file"
        assert candidate.suggested_field_type == "model"
        assert candidate.suggested_config == {"model_type": "lora", "allow_info_modal": True}
        assert candidate.suggested_transform == "strip_model_prefix"
        assert candidate.suggested_folder == "loras"
        assert candidate.obvious is True
        assert candidate.section == "Models"

    def test_off_chain_lora_strength_inputs_become_ordinary_sliders(self):
        workflow = self._workflow()
        analysis = suggest_fields(workflow)

        strength = next(c for c in analysis.candidates if c.node_id == "50" and c.input_name == "strength_model")
        assert strength.role == "lora_strength_model"
        assert strength.suggested_field_type == "slider"
        assert strength.section == "Sampling"

    def test_bite_check_off_chain_lora_is_invisible_without_the_dedicated_scan(self):
        """Confirms the assertions above depend on the off-chain scan: node
        50's class is only ever category "lora" via the catalog, which the
        generic loader/image_input/modifier sweep in suggest_fields
        deliberately skips (lora nodes are handled separately)."""
        from backend.preset_import.node_catalog import get_catalog

        entry = get_catalog().get("LoraLoaderModelOnly")
        assert entry.category not in ("loader", "image_input", "modifier")
