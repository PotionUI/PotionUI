"""Tests for NAG (Normalized Attention Guidance) wiring in the native LTX-2
preset: the Enhance tab's three sliders must reach BOTH generator stages, and
`nag_scale` must also reach `prompt_encoder` -- without that mirror the
encoder's `_do_cfg()` skips the negative pass at cfg 1.0, `_attach_nag`'s
`uncond is None` guard drops NAG, and the control is a silent no-op in exactly
the distilled/refine case it exists for.

Krea-2 canon: NAG now lives on the Enhance tab (moved off Advanced, which
holds the Sampling/Sigma-schedule/Step-cache sections instead), and every
field-level `description` was stripped repo-wide with real guidance folded
into `ai_hint`.
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor
from src.features.video_director.normalize import derive_ltx_media_fields

PRESET_DIR = Path("content/presets/marketplace/LTX-2")

_DOC_T2V = {
    "settings": {"seed": 123, "duration": 5, "fps": 25},
    "segments": [{"prompt": "a cat", "negative_prompt": "blurry"}],
    "media": [], "audio": [], "ic_lora": [],
}

_UPSCALE_ON = {"upscale": "2.0x", "upscale_model": "/models/upscaler.safetensors"}
_NAG_ON = {"nag_scale": 1.35, "nag_tau": 4.0, "nag_alpha": 0.25}


@pytest.fixture(scope="module")
def ltx_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2")), None)
    if template is None:
        pytest.skip("native/LTX-2 preset not present")
    return template


def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


def _process(ltx_template, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "model": "/models/ltx.safetensors",
        "text_encoder": "/models/gemma3.safetensors",
        "resolution": "768x512",
        "video_director": doc,
        "upscale_refine_sigmas": "",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(ltx_template, generation_data)


# -- rendering: the three values reach the generator ------------------------

def test_nag_values_reach_generator_stage1_as_floats(ltx_template):
    pipes = _process(ltx_template, _NAG_ON)
    cfg = _pipe(pipes, "generator_stage1")["config"]
    assert cfg["nag_scale"] == 1.35
    assert cfg["nag_tau"] == 4.0
    assert cfg["nag_alpha"] == 0.25
    # The pipe's PipeConfigSpecs are float-typed; a string-rendered "1.35"
    # would still pass float() but drift the golden and the spec check.
    assert all(isinstance(cfg[k], float) for k in ("nag_scale", "nag_tau", "nag_alpha"))


def test_nag_values_reach_generator_stage2_when_upscaling(ltx_template):
    pipes = _process(ltx_template, {**_UPSCALE_ON, **_NAG_ON})
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["enabled"] is True
    assert stage2["config"]["nag_scale"] == 1.35
    assert stage2["config"]["nag_tau"] == 4.0
    assert stage2["config"]["nag_alpha"] == 0.25


def test_nag_scale_mirrored_onto_prompt_encoder(ltx_template):
    # The whole point: without this the negative pass is never encoded at
    # cfg 1.0 and NAG silently does nothing.
    pipes = _process(ltx_template, _NAG_ON)
    assert _pipe(pipes, "prompt_encoder")["config"]["nag_scale"] == 1.35


def test_nag_survives_distilled_profile_where_cfg_is_one(ltx_template):
    # Distilled bakes cfg to 1.0 -- true CFG off, so prompt_encoder._do_cfg()
    # depends ENTIRELY on the mirrored nag_scale being > 1.0 here.
    pipes = _process(ltx_template, {**_NAG_ON, "speed_profile": "distilled", "cfg": 1.0})
    encoder = _pipe(pipes, "prompt_encoder")["config"]
    assert encoder["guidance_scale"] == 1.0
    assert encoder["nag_scale"] == 1.35
    assert _pipe(pipes, "generator_stage1")["config"]["nag_scale"] == 1.35


def test_stage2_runs_nag_at_its_hardcoded_cfg_one(ltx_template):
    # Stage 2's cfg is pinned to 1.0 by Lightricks' refine recipe, so NAG is
    # the only path by which the negative prompt reaches that pass at all.
    pipes = _process(ltx_template, {**_UPSCALE_ON, **_NAG_ON})
    stage2 = _pipe(pipes, "generator_stage2")["config"]
    assert stage2["cfg"] == 1.0
    assert stage2["nag_scale"] == 1.35


# -- defaults: off by default, byte-identical to pre-NAG ---------------------

def test_nag_defaults_to_off_everywhere(ltx_template):
    pipes = _process(ltx_template)
    assert _pipe(pipes, "generator_stage1")["config"]["nag_scale"] == 1.0
    assert _pipe(pipes, "prompt_encoder")["config"]["nag_scale"] == 1.0


def test_nag_off_leaves_true_cfg_deciding_the_negative_pass(ltx_template):
    # nag_scale 1.0 must not force the negative pass on: at cfg 1.0 with NAG
    # off, _do_cfg() stays False and the encode is skipped exactly as before.
    pipes = _process(ltx_template, {"speed_profile": "distilled", "cfg": 1.0})
    encoder = _pipe(pipes, "prompt_encoder")["config"]
    assert encoder["guidance_scale"] == 1.0
    assert encoder["nag_scale"] == 1.0


def test_upscale_off_stage2_config_still_unrendered(ltx_template):
    # Adding NAG keys to generator_stage2 must not perturb the disabled-stage
    # contract the golden snapshot pins.
    pipes = _process(ltx_template)
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["enabled"] is False
    assert stage2["config"] == {}


# -- form definition --------------------------------------------------------

def _field_by_name(fields, name):
    for f in fields:
        if f.get("name") == name:
            return f
        if "children" in f:
            found = _field_by_name(f["children"], name)
            if found is not None:
                return found
    return None


@pytest.fixture(scope="module")
def enhance_tab():
    # NAG lives on the Enhance tab now, not Advanced (Krea-2 canon layout --
    # see this preset's tabs/enhance.yml header comment).
    return yaml.safe_load((PRESET_DIR / "modes/video/tabs/enhance.yml").read_text())


@pytest.mark.parametrize("name,default,min_v,max_v,step", [
    ("nag_scale", 1.0, 1.0, 2.0, 0.05),
    ("nag_tau", 3.5, 1.0, 10.0, 0.5),
    ("nag_alpha", 0.5, 0.0, 1.0, 0.05),
])
def test_nag_sliders_match_wan_shape_and_ranges(enhance_tab, name, default, min_v, max_v, step):
    field = _field_by_name(enhance_tab["fields"], name)
    assert field is not None
    assert field["type"] == "slider"
    assert field["default"] == default
    assert field["configuration"]["min"] == min_v
    assert field["configuration"]["max"] == max_v
    assert field["configuration"]["step"] == step


def test_nag_scale_min_equals_off_value(enhance_tab):
    # A min below 1.0 would let the user pick a value the pipe treats as "off"
    # while the slider suggests it is doing something.
    field = _field_by_name(enhance_tab["fields"], "nag_scale")
    assert field["configuration"]["min"] == field["default"] == 1.0


def test_nag_scale_description_explains_the_cfg_one_case(enhance_tab):
    # The reason to reach for NAG at all. `description` was stripped repo-wide
    # by the canon pass -- this guidance now lives in `ai_hint` only, and
    # `description` must be ABSENT so the canon can't silently regress.
    field = _field_by_name(enhance_tab["fields"], "nag_scale")
    assert "description" not in field
    ai_hint = field["ai_hint"]
    assert "CFG 1.0" in ai_hint
    assert "1.0 (off)" in ai_hint
