"""Tests for the standalone native LTX-2.5 preset (``content/presets/marketplace/LTX-2.5``).

LTX-2.5 ships SPLIT checkpoints -- a transformer-only DiT plus standalone video
VAE, audio VAE and Gemma4-with-projection text encoder -- which is why it is its
own preset rather than another branch inside ``LTX-2 / 2.3``. This file pins the
three contracts that actually differ from that preset, all of which are silent
failures if they regress:

1. ``model_loader/ltx``'s ``vae``/``audio_model`` slots are wired
   (``video_vae`` REQUIRED -- no ``| default('')`` -- because a transformer-only
   DiT has no embedded ``vae.*`` to slice; ``audio_vae`` optional).
2. Stage 1 samples with ``euler_ancestral`` on EVERY speed profile, where the
   2.0/2.3 preset bakes deterministic ``euler``/``euler_cfg_pp``; stage 2 and
   the standalone ``upscale`` mode's refine pass stay deterministic ``euler``.
3. Stage 1 selects ``schedule: "ltx_dynamic"`` (LTX-2.5's resolution-aware
   shift) and nothing else does -- the refine passes run explicit
   ``refine_sigmas`` lists, which leave no generated curve to shape.

Mirrors ``test_ltx_speed_profiles.py``'s structure for the LTX-2 preset. See
docs/models/ltx.md for the sampler/schedule provenance.
"""

from __future__ import annotations

import copy
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor
from src.features.video_director.normalize import derive_ltx_media_fields

DISTILLED_RECIPE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
STAGE2_RECIPE = "0.909375, 0.725, 0.421875, 0.0"


@pytest.fixture(scope="module")
def ltx25_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2.5")), None)
    if template is None:
        pytest.skip("native/LTX-2.5 preset not present")
    return template


def _pipe(pipes, name, pipe_id=None):
    if pipe_id is not None:
        return next(p for p in pipes if p.get("id") == pipe_id)
    return next(p for p in pipes if p["name"] == name)


_DOC_T2V = {
    "settings": {"seed": 123, "duration": 5, "fps": 25},
    "segments": [{"prompt": "a cat", "negative_prompt": "ugly"}],
    "media": [], "audio": [], "ic_lora": [],
}


def _processor():
    return PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )


def _process_video(ltx25_template, form_over=None):
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "video_director": doc,
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "audio_vae": "/models/ltx25_audio_vae.safetensors",
        "resolution": "768x512",
        # Advanced-tab fields the real form always submits (their own defaults
        # are DEFINED empty strings, which the pipeline reads with `or`).
        "manual_sigmas": "",
        "upscale_refine_sigmas": "",
        # DFR (Enhance tab) at its off default. Present here so the common path
        # exercises the real submitted values rather than the Jinja fallbacks;
        # test_renders_without_any_dfr_keys below is what keeps those fallbacks
        # covered.
        "dfr_rounds": "off",
        "temporal_upscale_model": "",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return _processor().process(ltx25_template, generation_data)


def _process_upscale(ltx25_template, form_over=None):
    form_data = {
        "input_video": "/media/clip.mp4",
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "upscale_model": "/models/ltx25_upscaler.safetensors",
        "refine_sigmas": "",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "upscale", "form_data": form_data}
    return _processor().process(ltx25_template, generation_data)


# -- split-checkpoint wiring ------------------------------------------------

def test_video_loader_wires_both_split_vaes(ltx25_template):
    loader = _pipe(_process_video(ltx25_template), "model_loader/ltx")
    assert loader["config"]["vae"]["file_path"] == "/models/ltx25_video_vae.safetensors"
    assert loader["config"]["audio_model"]["file_path"] == "/models/ltx25_audio_vae.safetensors"


def test_upscale_loader_wires_video_vae_and_never_audio(ltx25_template):
    # The upscale mode generates no audio, so it hands model_loader/ltx no
    # `audio:`/`audio_model:` config at all -- the audio VAE + vocoder are never
    # loaded and there is no audio_vae picker on that mode's Models tab.
    loader = _pipe(_process_upscale(ltx25_template), "model_loader/ltx")
    assert loader["config"]["vae"]["file_path"] == "/models/ltx25_video_vae.safetensors"
    assert "audio_model" not in loader["config"]
    assert "audio" not in loader["config"]


@pytest.mark.parametrize("mode", ["video", "upscale"])
def test_video_vae_has_no_empty_string_fallback(ltx25_template, mode):
    # Bite-check for the `| default('')` idiom content/presets/marketplace/LTX-2 uses: with
    # a transformer-only DiT there is nothing to fall back TO, so an unset
    # picker must fail loudly at render time rather than silently render "" and
    # blow up later inside VAE construction.
    processor = _processor()
    if mode == "video":
        doc = copy.deepcopy(_DOC_T2V)
        doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
        form_data = {"video_director": doc, "model": "/m.safetensors",
                     "text_encoder": "/te.safetensors", "resolution": "768x512",
                     "manual_sigmas": "", "upscale_refine_sigmas": ""}
    else:
        form_data = {"input_video": "/media/clip.mp4", "model": "/m.safetensors",
                     "text_encoder": "/te.safetensors",
                     "upscale_model": "/up.safetensors", "refine_sigmas": ""}
    # Match the model_loader slot's own config path, not just the field name:
    # param_emitter references `form.video_vae` undefaulted too, so a bare
    # "video_vae" match would still pass with a `| default('')` back on the
    # loader -- i.e. it would stop testing the thing this guards.
    with pytest.raises(Exception, match=r"config_path='config\.vae\.file_path'"):
        processor.process(ltx25_template, {"prompts": [], "mode": mode, "form_data": form_data})


def test_audio_vae_is_optional(ltx25_template):
    # Audio itself is optional, so an unset audio_vae must still render (the
    # loader's `_require_embedded_component` pre-flight only raises when audio
    # is actually requested).
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {"video_director": doc, "model": "/m.safetensors",
                 "text_encoder": "/te.safetensors",
                 "video_vae": "/models/ltx25_video_vae.safetensors",
                 "resolution": "768x512", "manual_sigmas": "", "upscale_refine_sigmas": ""}
    pipes = _processor().process(ltx25_template, {"prompts": [], "mode": "video", "form_data": form_data})
    loader = _pipe(pipes, "model_loader/ltx")
    assert loader["config"]["audio_model"]["file_path"] == ""


# -- stage-1 sampler: ancestral on every profile ----------------------------

@pytest.mark.parametrize("profile,steps,cfg", [
    ("balanced", 24, 4.0),
    ("distilled", 8, 1.0),
    ("quality", 30, 3.0),
    ("custom", 24, 4.0),
])
def test_every_profile_bakes_euler_ancestral_stage1(ltx25_template, profile, steps, cfg):
    gen = _pipe(_process_video(ltx25_template, form_over={"speed_profile": profile}),
                "generator/video_ltx", pipe_id="generator_stage1")
    assert gen["config"]["sampler"] == "euler_ancestral"
    assert gen["config"]["steps"] == steps
    assert gen["config"]["cfg"] == cfg


def test_explicit_sampler_still_overrides_the_profile(ltx25_template):
    gen = _pipe(_process_video(ltx25_template, form_over={"speed_profile": "balanced", "sampler": "euler"}),
                "generator/video_ltx", pipe_id="generator_stage1")
    assert gen["config"]["sampler"] == "euler"


def test_distilled_profile_bakes_the_sigma_recipe(ltx25_template):
    # Byte-identical to content/presets/marketplace/LTX-2's recipe -- the distilled schedule
    # did not change from 2.3 to 2.5, only the sampler did.
    gen = _pipe(_process_video(ltx25_template, form_over={"speed_profile": "distilled"}),
                "generator/video_ltx", pipe_id="generator_stage1")
    assert gen["config"]["manual_sigmas"] == DISTILLED_RECIPE


def test_quality_profile_bakes_multimodal_guider_params(ltx25_template):
    gen = _pipe(_process_video(ltx25_template, form_over={"speed_profile": "quality"}),
                "generator/video_ltx", pipe_id="generator_stage1")
    assert gen["config"]["quality_mode"] is True
    assert gen["config"]["quality_cfg"] == 3.0
    assert gen["config"]["quality_stg"] == 1.0
    assert gen["config"]["quality_rescale"] == 0.7
    assert gen["config"]["quality_modality"] == 3.0
    assert gen["config"]["quality_stg_blocks"] == "28"


# -- refine passes stay deterministic ---------------------------------------

def test_stage2_refine_is_deterministic_euler(ltx25_template):
    gen = _pipe(_process_video(ltx25_template, form_over={"upscale": "2.0x"}),
                "generator/video_ltx", pipe_id="generator_stage2")
    assert gen["enabled"] is True
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["cfg"] == 1.0
    assert gen["config"]["refine_sigmas"] == STAGE2_RECIPE


def test_upscale_mode_refine_is_deterministic_euler(ltx25_template):
    gen = _pipe(_process_upscale(ltx25_template), "generator/txt2vid_ltx")
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["cfg"] == 1.0
    assert gen["config"]["refine_sigmas"] == STAGE2_RECIPE


# -- ltx_dynamic schedule ---------------------------------------------------

def test_stage1_selects_ltx_dynamic(ltx25_template):
    gen = _pipe(_process_video(ltx25_template), "generator/video_ltx", pipe_id="generator_stage1")
    assert gen["config"]["schedule"] == "ltx_dynamic"
    # schedule_options unset -> guidance_options.py's LTX-2.5 anchors apply.
    assert "schedule_options" not in gen["config"]


def test_refine_passes_select_no_schedule(ltx25_template):
    stage2 = _pipe(_process_video(ltx25_template, form_over={"upscale": "2.0x"}),
                   "generator/video_ltx", pipe_id="generator_stage2")
    assert "schedule" not in stage2["config"]
    refine = _pipe(_process_upscale(ltx25_template), "generator/txt2vid_ltx")
    assert "schedule" not in refine["config"]


# -- Jinja fallback coverage for the DFR keys -------------------------------

def test_renders_without_any_dfr_keys(ltx25_template):
    # The DFR fields (Enhance tab) arrived after this preset shipped, so every
    # read of them must carry a fallback -- the template evaluator raises on a
    # missing form_data key rather than rendering empty. `_process_video` above
    # now submits both keys at their defaults, so this is the ONLY case left
    # that exercises those fallbacks: drop a `| default(...)` from any
    # dfr_rounds/temporal_upscale_model read and this is what catches it.
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "video_director": doc,
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "resolution": "768x512",
        "manual_sigmas": "",
        "upscale_refine_sigmas": "",
    }
    pipes = _processor().process(ltx25_template, {"prompts": [], "mode": "video", "form_data": form_data})
    dfr = _pipe(pipes, "generator/dfr_video_ltx", pipe_id="dfr")
    assert dfr["enabled"] is False
    # Absent keys must resolve to the same "DFR off" wiring as an explicit
    # "off": stage 1 still decodes, and the gallery reads stage 1 directly.
    assert _pipe(pipes, "generator/video_ltx", pipe_id="generator_stage1")["config"]["decode"] is True
    gallery_video = next(i for i in _pipe(pipes, "gallery")["input"] if i["name"] == "video")
    assert gallery_video["provider"] == "generator_stage1"


# -- the removed runtime version-gate ---------------------------------------

@pytest.mark.parametrize("mode", ["video", "upscale"])
def test_no_pipe_receives_a_sampler_v25_key(ltx25_template, mode):
    # `sampler_v25`/`resolve_ltx_sampler` were removed from both LTX generator
    # pipes when 2.5 got its own preset: the preset names the stage-1 sampler
    # outright, so there is nothing left for a runtime model_version gate to
    # decide.
    pipes = _process_video(ltx25_template) if mode == "video" else _process_upscale(ltx25_template)
    for pipe in pipes:
        assert "sampler_v25" not in pipe["config"], pipe.get("id") or pipe["name"]
