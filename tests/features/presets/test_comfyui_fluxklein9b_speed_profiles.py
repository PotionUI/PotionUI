"""Tests for the ComfyUI FluxKlein9b preset's Speed selector (``form.speed_profile``):
one preset (id ``01KFXKLEIN9BTXT2IMG001``) selected via a single
``balanced``/``fast`` field, mirroring the native Wan/LTX-2/Krea2
``speed_profiles`` idiom (see ``test_wan_speed_profiles.py``) -- the first
ComfyUI preset to use it.

Unlike Wan/LTX-2/Krea2, "fast" here is not a LoRA on top of "balanced": it's a
*separately-trained distilled checkpoint*, with its own ComfyUI workflow graph
(distinct node-id namespace, ``77:`` vs. ``75:``) -- ``preset.vars.node_prefix``
looks up the right namespace per profile, and ``workflow_file`` itself switches
graphs (``txt2img_balanced.json`` / ``txt2img_fast.json``) via a Jinja ternary.
The distilled graph has no negative-prompt node at all (it zeroes conditioning
in-graph instead), so the negative prompt is applied via a `node_manipulations`
`update_node_input` gated by `condition:` (skipped entirely under "fast") rather
than a `field_mappings` entry -- pointing a field_mapping at a node id that
doesn't exist in the selected graph would log a per-generation error.

``img2img`` has only ONE graph (ported from the distilled variant's img2img
workflow, node namespace fixed at ``75:``) -- both profiles run it, varying only
steps/cfg/checkpoint; there's no `workflow_file` ternary in that mode.

Moved here from the plugin's own tests/ when the plugin graduated into
content/plugins/marketplace/: the plugin may only import `src.plugin_api`, but
this test drives the real PresetTemplateLoader/PresetProcessor/TemplateProcessor
as a rendering harness (there is no plugin_api surface for that - core is the
only thing that ever renders a preset), so it lives in core's test tree
instead, pointed at the plugin's own preset files.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def fluxklein_template():
    loader = PresetTemplateLoader(
        ["content/presets", "content/plugins/marketplace/comfyui-backend/presets"]
    )
    loader.load_presets()
    template = next(
        (p for p in loader.presets if "FluxKlein9b" in str(p.path)), None
    )
    if template is None:
        pytest.skip("FluxKlein9b preset not present")
    return template


def _pipe(pipes, name):
    return next(p for p in pipes if p["name"] == name)


def _process(fluxklein_template, mode, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "flux-2-klein-base-9b-fp8.safetensors",
        "clip": "qwen_3_8b_fp8mixed.safetensors",
        "vae": "flux2-vae.safetensors",
        "resolution": "1024x1024",
    }
    if mode == "img2img":
        form_data["input_image"] = "/storage/uploads/start.png"
        form_data["megapixels"] = 1.0
    if form_over:
        form_data.update(form_over)
    generation_data = {
        "prompts": [{"positive": "a cat", "negative": "blurry"}],
        "mode": mode,
        "form_data": form_data,
    }
    return processor.process(fluxklein_template, generation_data)


# -- txt2img: workflow graph + node namespace switch on Speed profile --------

def test_balanced_is_the_default_txt2img(fluxklein_template):
    pipes = _process(fluxklein_template, "txt2img")
    comfyui = _pipe(pipes, "comfyui")["config"]

    assert comfyui["workflow_file"].endswith("txt2img_balanced.json")

    field_mappings = comfyui["field_mappings"]
    by_path = {m[1]: m[0] for m in field_mappings}
    assert by_path["75:62.inputs.steps"] == 20
    assert by_path["75:63.inputs.cfg"] == 5.0
    assert by_path["75:61.inputs.sampler_name"] == "euler"
    assert by_path["75:70.inputs.unet_name"] == "flux-2-klein-base-9b-fp8.safetensors"
    # No node targets the "fast" graph's namespace.
    assert not any(path.startswith("77:") for path in by_path)

    # Negative prompt is applied via a conditioned node_manipulation, not a
    # field_mapping -- confirm it's present, targets the base-graph's
    # CLIPTextEncode node, and its condition is truthy for "balanced".
    negative_manip = next(
        m for m in comfyui["node_manipulations"]
        if isinstance(m, dict) and m.get("node_id") == "75:67"
    )
    assert negative_manip["input_value"] == "blurry"
    assert negative_manip["condition"] == "true"
    assert not any(m[1] == "75:67.inputs.text" for m in field_mappings)


def test_fast_profile_bakes_distilled_recipe_txt2img(fluxklein_template):
    pipes = _process(fluxklein_template, "txt2img", form_over={
        "speed_profile": "fast",
        "diffusion_model": "flux-2-klein-9b-fp8.safetensors",
    })
    comfyui = _pipe(pipes, "comfyui")["config"]

    assert comfyui["workflow_file"].endswith("txt2img_fast.json")

    field_mappings = comfyui["field_mappings"]
    by_path = {m[1]: m[0] for m in field_mappings}
    assert by_path["77:62.inputs.steps"] == 4
    assert by_path["77:63.inputs.cfg"] == 1.0
    assert by_path["77:61.inputs.sampler_name"] == "euler"
    assert by_path["77:70.inputs.unet_name"] == "flux-2-klein-9b-fp8.safetensors"
    # No node targets the "balanced" graph's namespace.
    assert not any(path.startswith("75:") for path in by_path)

    # The negative-prompt node_manipulation is skipped under "fast" (its
    # condition renders false) -- it must never target "75:67", which
    # doesn't exist in the distilled graph.
    negative_manip = next(
        m for m in comfyui["node_manipulations"]
        if isinstance(m, dict) and m.get("node_id") == "75:67"
    )
    assert negative_manip["condition"] == "false"


@pytest.mark.parametrize("profile", ["balanced", "fast"])
def test_explicit_fields_win_over_either_profile_txt2img(fluxklein_template, profile):
    pipes = _process(fluxklein_template, "txt2img", form_over={
        "speed_profile": profile, "steps": 12, "cfg": 3.5, "sampler_name": "dpmpp_2m",
    })
    comfyui = _pipe(pipes, "comfyui")["config"]
    prefix = "75" if profile == "balanced" else "77"
    by_path = {m[1]: m[0] for m in comfyui["field_mappings"]}
    assert by_path[f"{prefix}:62.inputs.steps"] == 12
    assert by_path[f"{prefix}:63.inputs.cfg"] == 3.5
    assert by_path[f"{prefix}:61.inputs.sampler_name"] == "dpmpp_2m"


@pytest.mark.parametrize("profile", ["balanced", "fast"])
def test_cfg_and_steps_stay_native_typed_txt2img(fluxklein_template, profile):
    pipes = _process(fluxklein_template, "txt2img", form_over={"speed_profile": profile})
    comfyui = _pipe(pipes, "comfyui")["config"]
    prefix = "75" if profile == "balanced" else "77"
    by_path = {m[1]: m[0] for m in comfyui["field_mappings"]}
    assert isinstance(by_path[f"{prefix}:62.inputs.steps"], int)
    assert isinstance(by_path[f"{prefix}:63.inputs.cfg"], float)


def test_negative_prompt_param_only_emitted_for_balanced(fluxklein_template):
    # param_emitter's `parameters:` carries the negative_prompt entry (for
    # generation tracking/UI display) only when Balanced is selected -- Fast
    # has no real negative prompt, so it must not appear at all.
    balanced_params = _pipe(_process(fluxklein_template, "txt2img"), "param_emitter")["config"]["parameters"]
    fast_params = _pipe(
        _process(fluxklein_template, "txt2img", form_over={"speed_profile": "fast"}),
        "param_emitter",
    )["config"]["parameters"]

    def _flatten(parameters):
        flat = []
        for item in parameters:
            if isinstance(item, list) and item and isinstance(item[0], list):
                flat.extend(item)
            elif isinstance(item, list) and item:
                flat.append(item)
        return flat

    assert ["negative_prompt", "blurry"] in _flatten(balanced_params)
    assert not any(p[0] == "negative_prompt" for p in _flatten(fast_params))


# -- img2img: single shared graph, no workflow_file ternary ------------------

@pytest.mark.parametrize(
    "profile,steps,cfg,model",
    [
        ("balanced", 20, 5.0, "flux-2-klein-base-9b-fp8.safetensors"),
        ("fast", 4, 1.0, "flux-2-klein-9b-fp8.safetensors"),
    ],
)
def test_img2img_shares_one_graph_across_profiles(fluxklein_template, profile, steps, cfg, model):
    pipes = _process(fluxklein_template, "img2img", form_over={
        "speed_profile": profile, "diffusion_model": model,
    })
    comfyui = _pipe(pipes, "comfyui")["config"]

    # Same graph regardless of profile -- no ternary/prefix switch in this mode.
    assert comfyui["workflow_file"].endswith("modes/img2img/files/workflows/img2img.json")

    by_path = {m[1]: m[0] for m in comfyui["field_mappings"]}
    assert by_path["75:62.inputs.steps"] == steps
    assert by_path["75:63.inputs.cfg"] == cfg
    assert by_path["75:70.inputs.unet_name"] == model

    # This graph has no negative-prompt node at all, under either profile.
    assert not any("67" in path for path in by_path)


def test_img2img_explicit_fields_win(fluxklein_template):
    pipes = _process(fluxklein_template, "img2img", form_over={
        "speed_profile": "fast", "steps": 8, "cfg": 2.0,
    })
    comfyui = _pipe(pipes, "comfyui")["config"]
    by_path = {m[1]: m[0] for m in comfyui["field_mappings"]}
    assert by_path["75:62.inputs.steps"] == 8
    assert by_path["75:63.inputs.cfg"] == 2.0
