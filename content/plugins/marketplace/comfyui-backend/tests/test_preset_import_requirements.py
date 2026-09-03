"""The workflow importer (backend/preset_import/emit.py) infers a preset's
`requirements:` block straight from the parsed workflow graph - independent
of which inputs the admin chose as form fields, since an unchosen model
loader still needs its file present to run.

Every fixture under tests/fixtures/ happens to be built entirely from
ComfyUI's own built-in node classes (see CORE_NODE_CLASS_TYPES), so none of
them exercise the `comfyui_node` (custom node) branch on their own - one
test below injects a synthetic non-core node into a copy of sdxl_basic_api's
data to cover it.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend.preset_import.emit import _infer_requirements, emit_preset
from backend.preset_import.parser import parse_api_workflow
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


class TestEmittedRequirementsAreSchemaValid:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_emitted_preset_yml_carries_a_schema_valid_requirements_block(self, dest_root):
        """The importer's own emission, end to end: every `requirements:`
        entry written to preset.yml validates against its checker's schema
        (backend/requirements.py) - the same per-entry check PresetLinter
        performs once this plugin is enabled and its checkers registered
        (a bare `scripts/preset_lint.py` run never loads plugin-contributed
        checkers - see src/features/presets/linter.py's
        `_requirement_checker_registry` docstring - so this validates the
        entries directly against their schemas instead of shelling out)."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        result = emit_preset(
            workflow, [], model_family="ReqSchemaTest", variant="v1",
            display_name="Requirements Schema Test", dest_root=dest_root,
        )

        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        requirements = preset_yml["requirements"]
        assert requirements  # the fixture's checkpoint loader always yields at least one entry

        schemas = {"comfyui_node": ComfyUINodeRequirementSchema, "comfyui_model": ComfyUIModelRequirementSchema}
        for entry in requirements:
            schemas[entry["type"]].model_validate(entry)
