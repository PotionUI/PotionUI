"""Tests for `backend.preset_import.node_scaffold` - the ast-based node
class extraction that backs the `comfyui_nodes.py scaffold` CLI command."""

from pathlib import Path

import pytest

from backend.preset_import import node_scaffold as ns

_COMFYUI_SRC = Path("/home/jtyszkiew/projects/ComfyUI")

_LEGACY_SOURCE = '''
import comfy.samplers

class SomeSampler:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", {}),
                "positive": ("CONDITIONING", {}),
                "negative": ("CONDITIONING", {}),
                "latent_image": ("LATENT", {}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 100}),
                "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 30.0, "step": 0.1}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS, {}),
                "scheduler": (["normal", "karras"], {}),
                "add_noise": ("BOOLEAN", {"default": True}),
                "ckpt_name": (["a.safetensors", "b.safetensors"], {}),
                "note": ("STRING", {"multiline": True}),
            }
        }
'''

_LORA_SOURCE = '''
class MyCustomLoraStack:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", {}),
                "clip": ("CLIP", {}),
                "lora_name": (["a.safetensors", "b.safetensors"], {}),
                "strength_model": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01}),
                "strength_clip": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01}),
            }
        }
'''

_LORA_MODEL_ONLY_SOURCE = '''
class MyCustomLoraModelOnly:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", {}),
                "lora_name": (["a.safetensors"], {}),
                "strength_model": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01}),
            }
        }
'''

_SCHEMA_SOURCE = '''
from comfy_api.latest import io

class SomeSchedulerNode(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="SomeSchedulerNode",
            inputs=[
                io.Model.Input("model"),
                io.Combo.Input("scheduler", options=["normal", "karras"]),
                io.Int.Input("steps", default=20, min=1, max=10000),
                io.Float.Input("denoise", default=1.0, min=0.0, max=1.0, step=0.01),
                io.Vae.Input("vae"),
            ],
        )
'''


class TestExtractLegacyInputTypes:
    def test_splits_widget_and_connected_inputs(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        assert node is not None
        assert node.style == "legacy"
        by_name = {i.name: i for i in node.inputs}

        assert by_name["model"].connected is True
        assert by_name["model"].type_name == "MODEL"
        assert by_name["positive"].connected is True
        assert by_name["seed"].connected is False
        assert by_name["seed"].type_name == "INT"

    def test_extracts_numeric_bounds(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        by_name = {i.name: i for i in node.inputs}
        assert by_name["seed"].config == {"default": 0, "min": 0, "max": 100}
        assert by_name["cfg"].config["min"] == 0.0
        assert by_name["cfg"].config["max"] == 30.0
        assert by_name["cfg"].config["step"] == 0.1

    def test_extracts_literal_combo_options(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        by_name = {i.name: i for i in node.inputs}
        assert by_name["scheduler"].type_name == "COMBO"
        assert by_name["scheduler"].config["options"] == ["normal", "karras"]

    def test_non_literal_combo_source_becomes_a_comment_not_a_crash(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        by_name = {i.name: i for i in node.inputs}
        sampler_name = by_name["sampler_name"]
        assert sampler_name.type_name == "COMBO"
        assert sampler_name.config.get("options") in (None, [])
        assert sampler_name.source_expr == "comfy.samplers.KSampler.SAMPLERS"

    def test_boolean_and_string_widgets(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        by_name = {i.name: i for i in node.inputs}
        assert by_name["add_noise"].type_name == "BOOLEAN"
        assert by_name["note"].type_name == "STRING"

    def test_infers_model_file_role_for_name_combo(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        rendered = ns.render_scaffold(node)
        assert "role: checkpoint" in rendered
        assert "folder: checkpoints" in rendered
        assert "transform: strip_model_prefix" in rendered

    def test_model_file_input_carries_the_catalogs_model_type_config(self):
        # checkpoints -> "checkpoint" per backend.comfyui_backend.FOLDER_TO_MODEL_TYPE.
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        rendered = ns.render_scaffold(node)
        assert "config: {model_type: checkpoint, allow_info_modal: true}" in rendered

    def test_model_type_missing_from_the_map_leaves_a_todo_instead_of_crashing(self, monkeypatch):
        import backend.comfyui_backend as comfyui_backend

        monkeypatch.delitem(comfyui_backend.FOLDER_TO_MODEL_TYPE, "checkpoints")
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        rendered = ns.render_scaffold(node)
        assert "config: {allow_info_modal: true}" in rendered
        assert "TODO: no model_type found for folder 'checkpoints'" in rendered

    def test_unknown_class_returns_none(self):
        assert ns.extract_from_source(_LEGACY_SOURCE, "NoSuchClass") is None


class TestExtractDefineSchema:
    def test_splits_widget_and_connected_inputs(self):
        node = ns.extract_from_source(_SCHEMA_SOURCE, "SomeSchedulerNode")
        assert node is not None
        assert node.style == "define_schema"
        by_name = {i.name: i for i in node.inputs}

        assert by_name["model"].connected is True
        assert by_name["model"].type_name == "MODEL"
        assert by_name["vae"].connected is True
        assert by_name["vae"].type_name == "VAE"
        assert by_name["steps"].connected is False
        assert by_name["steps"].type_name == "INT"

    def test_extracts_numeric_bounds_and_combo_options(self):
        node = ns.extract_from_source(_SCHEMA_SOURCE, "SomeSchedulerNode")
        by_name = {i.name: i for i in node.inputs}
        assert by_name["steps"].config == {"default": 20, "min": 1, "max": 10000}
        assert by_name["denoise"].config["step"] == 0.01
        assert by_name["scheduler"].type_name == "COMBO"
        assert by_name["scheduler"].config["options"] == ["normal", "karras"]

    def test_node_id_can_differ_from_python_class_name(self):
        # The class in the fixture is named SomeSchedulerNode and its
        # define_schema also declares node_id="SomeSchedulerNode" - looking
        # it up BY that node id (not just the python class name) must find
        # the same class, the way a class whose node_id differs from its
        # python name would only be found this way.
        node = ns.extract_from_source(_SCHEMA_SOURCE, "SomeSchedulerNode")
        assert node is not None


class TestRenderScaffold:
    def test_category_override_suppresses_todo_marker(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        rendered = ns.render_scaffold(node, category_override="sampler")
        assert "category: sampler\n" in rendered
        assert "TODO: confirm category" not in rendered

    def test_unmapped_connected_input_becomes_a_comment(self):
        node = ns.extract_from_source(_LEGACY_SOURCE, "SomeSampler")
        # "latent_image" maps to link kind "latent"; every other connected
        # input in the fixture maps too, so exercise the fallback directly.
        node.inputs.append(ns.ExtractedInput(name="mask", type_name="MASK", connected=True))
        rendered = ns.render_scaffold(node)
        assert "# connected: MASK" in rendered


class TestLoraNodeScaffold:
    """A `lora_name` combo makes a node a LoRA loader regardless of its
    class name (`MyCustomLoraStack`/`MyCustomLoraModelOnly` deliberately
    don't contain "Lora" in a way the old name-only heuristic would catch
    consistently) - category, links, and the `lora_name`/strength inputs
    should all come out matching the shipped `LoraLoader` entry's shape."""

    def test_category_is_lora_with_no_todo(self):
        node = ns.extract_from_source(_LORA_SOURCE, "MyCustomLoraStack")
        assert node.category_guess == "lora"
        rendered = ns.render_scaffold(node)
        assert "category: lora\n" in rendered
        assert "TODO: confirm category" not in rendered

    def test_links_model_and_clip_chain(self):
        node = ns.extract_from_source(_LORA_SOURCE, "MyCustomLoraStack")
        rendered = ns.render_scaffold(node)
        assert "    model: model_chain" in rendered
        assert "    clip: clip_chain" in rendered

    def test_model_only_variant_has_no_clip_chain_link(self):
        node = ns.extract_from_source(_LORA_MODEL_ONLY_SOURCE, "MyCustomLoraModelOnly")
        rendered = ns.render_scaffold(node)
        assert "    model: model_chain" in rendered
        assert "clip_chain" not in rendered

    def test_lora_name_renders_as_lora_picker_matching_lora_loader(self):
        node = ns.extract_from_source(_LORA_SOURCE, "MyCustomLoraStack")
        rendered = ns.render_scaffold(node)
        assert (
            "    lora_name:\n"
            "      role: lora_slot\n"
            "      field: lora_picker\n"
            "      name: loras\n"
            "      label: LoRAs\n"
            "      config: {model_type: lora, max_items: 6}\n"
            "      history: list\n"
            "      folder: loras\n"
        ) in rendered
        assert "field: model" not in rendered  # never the generic model-file treatment

    def test_strength_inputs_get_dedicated_roles(self):
        node = ns.extract_from_source(_LORA_SOURCE, "MyCustomLoraStack")
        rendered = ns.render_scaffold(node)
        by_name = {i.name: i for i in node.inputs}
        assert by_name["strength_model"].type_name == "FLOAT"
        assert "role: lora_strength_model" in rendered
        assert "role: lora_strength_clip" in rendered
        assert "role: option" not in rendered  # strengths never fall through to the generic guess


@pytest.mark.skipif(not _COMFYUI_SRC.is_dir(), reason="ComfyUI checkout not available")
class TestExtractFromRealComfyuiSrc:
    def test_ksampler(self):
        node = ns.extract_from_comfyui_src("KSampler", _COMFYUI_SRC)
        assert node is not None
        assert node.style == "legacy"
        assert node.file_path.name == "nodes.py"
        by_name = {i.name: i for i in node.inputs}
        assert by_name["positive"].connected is True
        assert by_name["negative"].connected is True
        assert by_name["seed"].type_name == "INT"

    def test_basic_scheduler(self):
        node = ns.extract_from_comfyui_src("BasicScheduler", _COMFYUI_SRC)
        assert node is not None
        assert node.style == "define_schema"
        by_name = {i.name: i for i in node.inputs}
        assert by_name["model"].connected is True
        assert by_name["steps"].type_name == "INT"
        assert by_name["scheduler"].type_name == "COMBO"
