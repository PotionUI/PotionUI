"""The workflow importer (backend/preset_import/emit.py) infers a preset's
`requirements:` block straight from the parsed workflow graph - independent
of which inputs the admin chose as form fields, since an unchosen model
loader still needs its file present to run.

Every workflow fixture under tests/fixtures/ is built entirely from
ComfyUI's own built-in node classes, so the `comfyui_node` (custom node)
tests below inject a synthetic non-core node rather than relying on one -
except where they use `object_info_sdxl.json` (shared with
test_preset_import_ui_format.py), whose `FaceDetailer` entry is declared
non-core there already.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend.preset_import.emit import _infer_requirements, emit_preset
from backend.preset_import.parser import WorkflowNode, parse_api_workflow
from backend.preset_import.schema import ImportForm
from backend.requirements import ComfyUIModelRequirementSchema, ComfyUINodeRequirementSchema

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _by_type(requirements, type_name):
    return [r for r in requirements if r["type"] == type_name]


class TestModelRequirementsFromModelLoaders:
    def test_flux_subgraph_gets_one_comfyui_model_per_loader_no_custom_nodes(self):
        """Every node class in this fixture (VAELoader, UNETLoader,
        CLIPLoader, CLIPTextEncode, EmptyLatentImage, KSampler, VAEDecode,
        SaveImage) is a ComfyUI built-in, so no `comfyui_node` entry should
        be emitted - only the three referenced model files."""
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        requirements = _infer_requirements(workflow)

        assert _by_type(requirements, "comfyui_node") == []
        assert {(r["folder"], r["name"]) for r in _by_type(requirements, "comfyui_model")} == {
            ("diffusion_models", "flux1-dev.safetensors"),
            ("text_encoders", "t5xxl_fp16.safetensors"),
            ("vae", "ae.safetensors"),
        }

    def test_lora_chain_workflow_gets_a_comfyui_model_entry_per_lora(self):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        requirements = _infer_requirements(workflow)

        model_reqs = {(r["folder"], r["name"]) for r in _by_type(requirements, "comfyui_model")}
        assert ("checkpoints", "sdxlBase_v10.safetensors") in model_reqs
        assert ("loras", "style_a.safetensors") in model_reqs
        assert ("loras", "style_b.safetensors") in model_reqs

    def test_bite_check_no_model_loaders_means_no_requirements(self):
        """Confirms the assertions above can fail: a workflow with no model
        loader nodes at all gets zero comfyui_model entries."""
        workflow = parse_api_workflow({"1": {"class_type": "SaveImage", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow), "comfyui_model") == []


class TestNodeRequirementsFromNonCoreClasses:
    """No `object_info` given: falls back to the hand-kept
    `CORE_NODE_CLASS_TYPES` allowlist (a plain Export (API) import never
    fetches one - see api.py's `_parse_incoming_workflow`)."""

    def test_non_core_node_class_gets_a_comfyui_node_requirement(self):
        data = _load("sdxl_basic_api.json")
        data["20"] = {
            "class_type": "FaceDetailer",
            "inputs": {"guide_size": 384.0, "image": ["8", 0]},
            "_meta": {"title": "Face Detailer"},
        }
        workflow = parse_api_workflow(data)
        requirements = _infer_requirements(workflow)

        assert _by_type(requirements, "comfyui_node") == [
            {"type": "comfyui_node", "class_type": "FaceDetailer"}
        ]

    def test_bite_check_core_only_fixture_has_no_node_requirement(self):
        """Confirms the assertion above can fail: the unmodified fixture
        (every node a ComfyUI built-in) gets no comfyui_node entries."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        assert _by_type(_infer_requirements(workflow), "comfyui_node") == []


class TestNodeRequirementsFromObjectInfo:
    """When `object_info` is available at import time (a UI-format import
    always fetches one), `python_module` per class is authoritative instead
    of the static allowlist - this is how a core node the allowlist hasn't
    caught up with yet (e.g. `CFGGuider`) is correctly recognized as core."""

    def test_nodes_python_module_is_core(self):
        object_info = {"CFGGuider": {"python_module": "nodes"}}
        workflow = parse_api_workflow({"1": {"class_type": "CFGGuider", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == []

    def test_comfy_extras_python_module_is_core(self):
        object_info = {"LatentUpscaleBy": {"python_module": "comfy_extras.nodes_upscale_model"}}
        workflow = parse_api_workflow({"1": {"class_type": "LatentUpscaleBy", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == []

    def test_custom_nodes_python_module_is_a_requirement(self):
        object_info = {"FaceDetailer": {"python_module": "custom_nodes.ComfyUI-Impact-Pack"}}
        workflow = parse_api_workflow({"1": {"class_type": "FaceDetailer", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == [
            {"type": "comfyui_node", "class_type": "FaceDetailer"}
        ]

    def test_class_absent_from_object_info_is_a_requirement(self):
        """An uninstalled custom node isn't in the server's /object_info at
        all - treated as custom, not skipped as unrecognized-so-core."""
        object_info = {"KSampler": {"python_module": "nodes"}}
        workflow = parse_api_workflow(
            {"1": {"class_type": "KSampler", "inputs": {}}, "2": {"class_type": "NotInstalled", "inputs": {}}}
        )
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == [
            {"type": "comfyui_node", "class_type": "NotInstalled"}
        ]

    def test_object_info_overrides_the_static_allowlist_for_a_class_it_lacks(self):
        """`CFGGuider` isn't in CORE_NODE_CLASS_TYPES's predecessor set, but
        with object_info available it's read from the live server, not
        guessed - the allowlist is a fallback, never consulted once
        object_info answers a class either way."""
        object_info = {"TotallyUnknownToTheAllowlist": {"python_module": "nodes"}}
        workflow = parse_api_workflow({"1": {"class_type": "TotallyUnknownToTheAllowlist", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == []

    def test_bite_check_without_object_info_the_same_class_is_a_requirement(self):
        """Confirms the assertion above is really reading object_info: the
        identical unrecognized class, with no object_info given, falls back
        to the allowlist and (correctly) becomes a requirement."""
        workflow = parse_api_workflow({"1": {"class_type": "TotallyUnknownToTheAllowlist", "inputs": {}}})
        assert _by_type(_infer_requirements(workflow), "comfyui_node") == [
            {"type": "comfyui_node", "class_type": "TotallyUnknownToTheAllowlist"}
        ]

    def test_shared_fixture_flags_only_the_custom_node(self):
        """tests/fixtures/object_info_sdxl.json (also used by
        test_preset_import_ui_format.py) declares its core classes'
        `python_module` as `nodes`/`comfy_extras.*` and FaceDetailer's as
        `custom_nodes.ComfyUI-Impact-Pack` - only FaceDetailer should turn
        into a requirement."""
        object_info = json.loads((FIXTURES / "object_info_sdxl.json").read_text())
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        workflow.nodes["20"] = WorkflowNode(
            id="20", class_type="FaceDetailer", title=None, inputs={"image": ["8", 0]}
        )
        assert _by_type(_infer_requirements(workflow, object_info=object_info), "comfyui_node") == [
            {"type": "comfyui_node", "class_type": "FaceDetailer"}
        ]


class TestEmittedRequirementsAreSchemaValid:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_emitted_preset_yml_carries_a_schema_valid_requirements_block(self, dest_root):
        """The importer's own emission, end to end: every `requirements:`
        entry written to preset.yml validates against its checker's schema
        (backend/requirements.py) - the same per-entry check PresetLinter
        performs (see TestEndToEndRenderAndLint in test_preset_import_emit.py
        for the full `scripts/preset_lint.py` subprocess assertion; this test
        checks the schemas directly instead of shelling out)."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        result = emit_preset(
            workflow, ImportForm(tabs=[]), [], model_family="ReqSchemaTest", variant="v1",
            display_name="Requirements Schema Test", dest_root=dest_root,
        )

        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        requirements = preset_yml["requirements"]
        assert requirements  # the fixture's checkpoint loader always yields at least one entry

        schemas = {"comfyui_node": ComfyUINodeRequirementSchema, "comfyui_model": ComfyUIModelRequirementSchema}
        for entry in requirements:
            schemas[entry["type"]].model_validate(entry)
