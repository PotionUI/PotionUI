"""
Wiring test for the optional Krea2T Enhancer (node 300) in the comfyui-backend
plugin's Krea-2 txt2img preset (https://github.com/capitan01R/ComfyUI-Krea2T-Enhancer).

Moved here from the plugin's own tests/ when the plugin graduated into
content/plugins/marketplace/: the plugin may only import `src.plugin_api`, but
this test drives the real PresetProcessor/TemplateProcessor as a rendering
harness (there is no plugin_api surface for that - core is the only thing that
ever renders a preset), so it lives in core's test tree instead, pointed at the
plugin's own preset/pipe files.

Renders the real pipeline.yml through the real PresetProcessor (so `@loop`
node-manipulation expansion runs exactly as it does at generation time) and
applies it with the real ComfyUIPipe to the real txt2img workflow. Asserts:
  - toggle OFF (default): node 300 absent, KSampler model chain unchanged
  - toggle ON, no LoRAs: 37 -> 300 -> 3
  - toggle ON + LoRAs:   37 -> LoRAs -> 300 -> 3 (enhancer last, before sampler)
No GPU / ComfyUI server required.
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets.processor import PresetProcessor
from src.platform.templating import TemplateProcessor

REPO = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO / "content" / "plugins" / "marketplace" / "comfyui-backend"
MODE_ROOT = PLUGIN_DIR / "presets" / "Krea-2" / "modes" / "txt2img"
PIPELINE_YML = MODE_ROOT / "pipeline.yml"
WORKFLOW_JSON = MODE_ROOT / "files" / "workflows" / "txt2img.json"


def _load_comfyui_pipe():
    """Load the plugin's ComfyUIPipe by file path under a private module name.

    Not `sys.path.insert` + `from backend.pipes.comfyui.main import ...`: several
    marketplace plugins each ship their own top-level `backend` package, so
    whichever one another test file imports first would win the `backend` name
    for the rest of the process. `main.py` only imports stdlib/third-party and
    `src.plugin_api`, so loading it under a unique alias is safe.
    """
    module_name = "_comfyui_backend_pipe_for_krea2_enhancer_wiring_test"
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGIN_DIR / "backend" / "pipes" / "comfyui" / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PipeInput = importlib.import_module("src.pipelines.contracts").PipeInput
ComfyUIPipe = _load_comfyui_pipe().ComfyUIPipe


class TestComfyUIKrea2EnhancerWiring:
    BASE_FORM = {
        "seed": 42,
        "resolution": "1024x1024",
        "quantity": 1,
        "steps": 8,
        "cfg": 1.0,
    }

    async def _build(self, form_data: dict):
        first_pair = {"positive": "a cat", "negative": ""}
        context = {
            "form": form_data,
            "generation": {
                "prompts": {
                    "first": first_pair,
                    "pairs": [first_pair],
                    "positives": [first_pair["positive"]],
                    "negatives": [first_pair["negative"]],
                },
            },
        }
        tp = TemplateProcessor(settings=Mock())
        # model_directories/settings/preset_template_loader are only used by
        # PresetProcessor.process() (the full preset->pipeline build, which needs
        # a loaded PresetTemplate); process_value() - the recursive
        # string/dict/list/`@loop` renderer this test drives directly - only
        # touches template_processor, so mocks are fine for the rest.
        pp = PresetProcessor(
            template_processor=tp,
            model_directories=Mock(),
            settings=Mock(),
            preset_template_loader=Mock(),
        )
        pipeline = yaml.safe_load(PIPELINE_YML.read_text())
        comfy = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        cfg = comfy["configuration"]
        pipe = ComfyUIPipe({
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": "unused.json",
            "field_mappings": pp.process_value(cfg["field_mappings"], context),
            "node_manipulations": pp.process_value(cfg["node_manipulations"], context),
            "timeout": 600,
            "secure": False,
        })
        workflow = json.loads(WORKFLOW_JSON.read_text())
        gen_out = Mock()
        pipe_input = PipeInput(input={"seed": [42]})
        wf = await pipe.apply_node_manipulations(workflow, pipe_input, gen_out)
        wf = await pipe.apply_field_mappings(wf, pipe_input, gen_out)
        return wf

    @pytest.mark.asyncio
    async def test_off_by_default(self):
        wf = await self._build(dict(self.BASE_FORM))
        assert "300" not in wf
        assert wf["3"]["inputs"]["model"] == ["37", 0]

    @pytest.mark.asyncio
    async def test_off_explicit_false_string(self):
        """Checkbox may submit the string 'false' — must stay off."""
        form = dict(self.BASE_FORM)
        form["krea_enhancer"] = "false"
        wf = await self._build(form)
        assert "300" not in wf
        assert wf["3"]["inputs"]["model"] == ["37", 0]

    @pytest.mark.asyncio
    async def test_on_no_loras(self):
        form = dict(self.BASE_FORM)
        form["krea_enhancer"] = True
        form["krea_enhancer_strength"] = 1.35
        wf = await self._build(form)
        assert "300" in wf
        assert wf["300"]["class_type"] == "ComfyUI-Krea2T-Enhancer"
        assert wf["300"]["inputs"]["model"] == ["37", 0]
        assert float(wf["300"]["inputs"]["strength"]) == 1.35
        assert wf["300"]["inputs"]["enabled"]
        assert wf["3"]["inputs"]["model"] == ["300", 0]

    @pytest.mark.asyncio
    async def test_on_with_loras_enhancer_sits_last(self):
        form = dict(self.BASE_FORM)
        form["krea_enhancer"] = True
        form["loras"] = [
            {"model": "models/loras/style.safetensors", "strength": 0.8},
            {"model": "models/loras/detail.safetensors", "strength": 0.6},
        ]
        wf = await self._build(form)
        # LoRA chain built from the `loras` list, in order: 37 -> lora_1 -> lora_2
        assert wf["lora_1"]["inputs"]["model"] == ["37", 0]
        assert wf["lora_2"]["inputs"]["model"] == ["lora_1", 0]
        # Enhancer wraps the chain end, sampler reads the enhancer
        assert wf["300"]["inputs"]["model"] == ["lora_2", 0]
        assert wf["3"]["inputs"]["model"] == ["300", 0]

    @pytest.mark.asyncio
    async def test_on_string_true(self):
        """Checkbox may submit the string 'true' — must turn on."""
        form = dict(self.BASE_FORM)
        form["krea_enhancer"] = "true"
        wf = await self._build(form)
        assert "300" in wf
        assert wf["3"]["inputs"]["model"] == ["300", 0]
