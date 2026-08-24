"""Tests for the native Wan preset's Speed selector (``form.speed_profile``):
a single ``balanced``/``fast`` selection replaces the old flat
``preset.vars.default_steps/default_cfg/default_sampler``, mirroring the
LTX-2 speed_profiles idiom (see ``test_ltx_speed_profiles.py``) but with only
two profiles -- Wan has no distilled-sigma-recipe/quality-mode analogue.
``fast`` is the lightning/lightx2v-LoRA regime (6 steps, CFG 1.0); it bakes a
baseline the same way ``balanced`` does, not a hard override -- an explicit
form field still wins in either profile (unlike LTX's ``distilled``, which
hard-overrides ``manual_sigmas``).

The preset's only mode is ``video``, the Video Director (the standalone
``txt2vid``/``img2vid`` modes were retired -- their t2v/i2v sub-types route
through the same ``generator/txt2vid_wan22``/``generator/img2vid_wan22``
pipes via the Director document's ``mode`` field). Profiles set the
per-REQUEST default only. Chain segments can still individually pin their
own steps/cfg inside the submitted document, which reaches
``generator/chain_video_wan22`` as a raw passthrough
(``document: "{{ form.video_director }}"``) -- untouched by whichever
speed_profile is selected for the request. That passthrough guarantee is
what ``test_video_chain_segment_overrides_survive_any_profile`` pins.

Complements ``test_video_director_pipeline.py`` (owns the broader Director
mode-routing/media-loader contract; this file is scoped to the speed-profile
selector alone, across the t2v/i2v/chain sub-types).
"""

from __future__ import annotations

import copy
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.video_director.normalize import derive_segment_routing
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def wan_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "native/Wan" in str(p.path)), None)
    if template is None:
        pytest.skip("native/Wan preset not present")
    return template


def _pipe(pipes, name):
    return next(p for p in pipes if p["name"] == name)


def _process(wan_template, mode, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "t2v_high_noise_model": "/models/wan_t2v_high.safetensors",
        "t2v_low_noise_model": "/models/wan_t2v_low.safetensors",
        "i2v_high_noise_model": "/models/wan_i2v_high.safetensors",
        "i2v_low_noise_model": "/models/wan_i2v_low.safetensors",
        "text_encoder": "/models/umt5.safetensors",
        "vae": "/models/wan_vae.safetensors",
        "resolution": "832x480",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": mode, "form_data": form_data}
    return processor.process(wan_template, generation_data)


# -- video (Director) mode: request-level defaults + per-segment passthrough --

def _settings(**over):
    base = {"fps": 16, "duration": 5, "resolution": "", "seed": 424242, "continuation": None}
    base.update(over)
    return base


def _segment(seg_id="seg-0", prompt="a cat", **over):
    seg = {
        "id": seg_id, "prompt": prompt, "negative_prompt": "blurry", "start": None, "end": None,
        "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
    }
    seg.update(over)
    return seg


DOC_T2V = {
    "schema_version": 1, "mode": "t2v",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [], "audio": [], "ic_lora": [],
}

DOC_I2V = {
    "schema_version": 1, "mode": "i2v",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [{
        "id": "media-first", "role": "first", "segment_id": "seg-0", "at": None,
        "strength": 1.0, "media": {"path": "/storage/uploads/start.png"},
    }],
    "audio": [], "ic_lora": [],
}

# (doc, generator) pairs the request-level profile tests below run against --
# t2v and i2v are the two sub-types that route through a plain single-pass
# generator (chain has its own passthrough-focused tests further down).
_VIDEO_SUBTYPES = [
    (DOC_T2V, "generator/txt2vid_wan22"),
    (DOC_I2V, "generator/img2vid_wan22"),
]

DOC_CHAIN = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": None, "overlap_frames": 4, "stitch": True}),
    "segments": [
        _segment("seg-0", "a cat walks", frames=81, steps=18, cfg=6.5),
        _segment("seg-1", "the cat sits", frames=81),
        _segment("seg-2", "the cat sleeps", frames=49, seed=999, steps=4, cfg=1.0),
    ],
    "media": [{
        "id": "media-first", "role": "first", "segment_id": "seg-0", "at": None,
        "strength": 1.0, "media": {"path": "/storage/uploads/start.png"},
    }],
    "audio": [], "ic_lora": [],
}


def _process_video(wan_template, doc, form_over=None):
    # The pipeline branches its two model-set loaders on needs_t2v_set/
    # needs_i2v_set, which normalize_video_director -> derive_segment_routing
    # precomputes; these fixtures are hand-built, so run that derivation here.
    doc = copy.deepcopy(doc)
    doc.update(derive_segment_routing(doc["segments"], doc["media"]))
    return _process(wan_template, "video", form_over={
        "video_director": doc, **(form_over or {}),
    })


@pytest.mark.parametrize("doc,generator", _VIDEO_SUBTYPES)
@pytest.mark.parametrize("profile,steps,cfg", [("balanced", 30, 5.0), ("fast", 6, 1.0)])
def test_video_request_level_bakes_profile(wan_template, doc, generator, profile, cfg, steps):
    pipes = _process_video(wan_template, doc, form_over={"speed_profile": profile})
    gen = _pipe(pipes, generator)
    assert gen["config"]["steps"] == steps
    assert gen["config"]["cfg"] == cfg
    assert gen["config"]["sampler"] == "unipc"
    encoder = _pipe(pipes, "prompt_encoder")
    assert encoder["config"]["guidance_scale"] == cfg


@pytest.mark.parametrize("doc,generator", _VIDEO_SUBTYPES)
def test_video_explicit_fields_win(wan_template, doc, generator):
    pipes = _process_video(wan_template, doc, form_over={
        "speed_profile": "fast", "sampler": "dpmpp_2m", "cfg": 7.0, "steps": 10,
    })
    gen = _pipe(pipes, generator)
    assert gen["config"]["sampler"] == "dpmpp_2m"
    assert gen["config"]["cfg"] == 7.0
    assert gen["config"]["steps"] == 10


@pytest.mark.parametrize("profile", ["balanced", "fast"])
def test_video_chain_segment_overrides_survive_any_profile(wan_template, profile):
    # The chain generator gets the request-level baked steps/cfg as its OWN
    # top-level config (a per-request default, which DOES track the selected
    # profile) -- but the full document is passed through verbatim, so each
    # segment's individually-pinned steps/cfg must reach chain_video_wan22
    # untouched no matter which profile the request-level selector has.
    pipes = _process_video(wan_template, DOC_CHAIN, form_over={"speed_profile": profile})
    chain = _pipe(pipes, "generator/chain_video_wan22")

    expected_request_steps = 30 if profile == "balanced" else 6
    expected_request_cfg = 5.0 if profile == "balanced" else 1.0
    assert chain["config"]["steps"] == expected_request_steps
    assert chain["config"]["cfg"] == expected_request_cfg

    doc_segments = chain["config"]["document"]["segments"]
    assert doc_segments[0]["steps"] == 18
    assert doc_segments[0]["cfg"] == 6.5
    assert doc_segments[1]["steps"] is None  # no per-segment override -> untouched None
    assert doc_segments[1]["cfg"] is None
    assert doc_segments[2]["steps"] == 4
    assert doc_segments[2]["cfg"] == 1.0
    # Passthrough is a real dict, not a Jinja-stringified copy -- same
    # invariant test_video_director_pipeline.py pins for the whole document.
    assert isinstance(chain["config"]["document"], dict)


@pytest.mark.parametrize("doc,generator", _VIDEO_SUBTYPES)
def test_video_cfg_and_steps_stay_native_typed(wan_template, doc, generator):
    for profile in ("balanced", "fast"):
        pipes = _process_video(wan_template, doc, form_over={"speed_profile": profile})
        config = _pipe(pipes, generator)["config"]
        assert isinstance(config["cfg"], float)
        assert isinstance(config["steps"], int)
