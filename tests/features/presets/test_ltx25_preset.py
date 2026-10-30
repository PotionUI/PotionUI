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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts.preset_render import (
    ROOT,
    build_fixture_form_data,
    build_processor,
    load_all_presets,
    render_preset_mode,
)
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.prompt_enhancement.operations import _gather_preset_guide
from src.platform.templating.processor import TemplateProcessor
from src.features.video_director.normalize import derive_ltx_media_fields

from ._bound_form import bound_form_data

DISTILLED_RECIPE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
STAGE2_RECIPE = "0.909375, 0.725, 0.421875, 0.0"


@pytest.fixture(scope="module")
def ltx25_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5")), None)
    if template is None:
        pytest.fail("marketplace/LTX-2.5 preset not present")
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
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
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
        "dfr_rounds": "off",
        "temporal_upscale_model": "",
    }
    if form_over:
        form_data.update(form_over)
    form_data = bound_form_data(ltx25_template, "video", form_data)
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
    form_data = bound_form_data(ltx25_template, "upscale", form_data)
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
    form_data = bound_form_data(ltx25_template, "video", form_data)
    pipes = _processor().process(ltx25_template, {"prompts": [], "mode": "video", "form_data": form_data})
    loader = _pipe(pipes, "model_loader/ltx")
    assert loader["config"]["audio_model"]["file_path"] is None


# -- stage-1 sampler: ancestral on every profile ----------------------------

@pytest.mark.parametrize("profile,steps,cfg", [
    ("balanced", 24, 4.0),
    ("distilled", 8, 1.0),
    ("quality", 30, 3.0),
    ("custom", 8, 1.0),
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


def test_no_mode_caps_clip_frames_with_a_preset_number():
    root = Path("content/presets/marketplace/LTX-2.5")
    files = [root / "preset.yml", *sorted(root.glob("modes/*/*.yml"))]
    assert [f for f in files if "edit_max_frames" in f.read_text(encoding="utf-8")] == []


def test_every_windowed_edit_mode_takes_its_frames_from_the_clip():
    windowed = 0
    for path in sorted(Path("content/presets/marketplace/LTX-2.5/modes").glob("*/pipeline.yml")):
        for pipe in yaml.safe_load(path.read_text(encoding="utf-8"))["pipeline"]:
            config = pipe.get("configuration") or {}
            if pipe.get("name") == "generator/video_ltx" and config.get("match_video"):
                assert "frames" not in config, path
                windowed += bool(config.get("windows"))
    assert windowed >= 6


OPTIONAL_PROMPT_MODES = [
    "restore", "colorize", "day_to_night", "add_water", "remove_object", "layout_to_render", "cinemagraph",
]
PROMPT_PASS_THROUGH_MODES = ["extend", "refine_faces"]


def _prompt_built_from_an_empty_prompt(template, mode):
    generation = {
        "mode": mode, "form_data": build_fixture_form_data(template, mode),
        "prompts": [{"positive": "", "negative": ""}],
    }
    pipes = build_processor().process(template, generation)
    return next(p for p in pipes if p["name"] == "prompt_encoder")["config"]["p_prompt"]["output"]


def test_the_modes_with_a_built_in_sentence_take_an_optional_prompt(ltx25_template):
    assert ltx25_template.vars["optional_prompt_modes"] == OPTIONAL_PROMPT_MODES
    assert "promptless_modes" not in ltx25_template.vars
    assert set(OPTIONAL_PROMPT_MODES) <= set(ltx25_template.modes)


@pytest.mark.parametrize("mode", OPTIONAL_PROMPT_MODES)
def test_each_optional_prompt_mode_builds_a_prompt_when_none_is_written(ltx25_template, mode):
    assert _prompt_built_from_an_empty_prompt(ltx25_template, mode).strip()


@pytest.mark.parametrize("mode", PROMPT_PASS_THROUGH_MODES)
def test_the_modes_that_pass_the_prompt_straight_through_keep_requiring_it(ltx25_template, mode):
    assert mode not in ltx25_template.vars["optional_prompt_modes"]
    assert not _prompt_built_from_an_empty_prompt(ltx25_template, mode).strip()


TRIGGER_WORDS = "ADD WATER, COLORIZE, DEBLUR, ENHANCE QUALITY or CINEMAGRAPH_MOTION"
MODE_GUIDE_RULES = {
    "restore": ["The Fix field picks the task", "describe the period, place, light, materials and clothing"],
    "colorize": ["one or two plain sentences naming the colours to bring back", "Name real colours"],
    "day_to_night": ["Describe the night look"],
    "add_water": ["Write only the water: its type, its motion and how it touches the subject"],
    "remove_object": ["Describe the empty scene and name what must not appear"],
    "layout_to_render": [
        "one or two sentences describing the finished shot, matching the First Frame",
        "Never use the words clay, 3D or Unreal",
    ],
    "cinemagraph": ["Name exactly what moves", "everything else stays still", "there are no camera moves"],
}


def _guide(template, mode):
    return " ".join(template.llm["modes"][mode]["guide"].split())


def _prompt_writer_guide(template, mode):
    collaborators = SimpleNamespace(
        preset_collaborators=SimpleNamespace(file_repo=Mock(find_preset_by_id=Mock(return_value=template))),
    )
    return _gather_preset_guide(collaborators, {"preset": template.id, "mode": mode})


def test_only_the_reference_video_modes_override_the_video_guide(ltx25_template):
    assert set(ltx25_template.llm["modes"]) == set(MODE_GUIDE_RULES) == set(OPTIONAL_PROMPT_MODES)
    assert ltx25_template.llm["guide"].startswith("Write ONE chronological paragraph")


@pytest.mark.parametrize("mode", MODE_GUIDE_RULES)
def test_each_reference_video_mode_tells_the_prompt_writer_what_that_mode_wants(ltx25_template, mode):
    guide = _guide(ltx25_template, mode)

    for rule in MODE_GUIDE_RULES[mode]:
        assert rule in guide
    assert "The prompt is optional, and the mode adds the caption and trigger word itself" in guide
    assert f"never type {TRIGGER_WORDS}" in guide
    assert guide != " ".join(ltx25_template.llm["guide"].split())


def test_the_restore_guide_names_the_fixes_by_the_labels_the_form_shows(ltx25_template):
    path = Path("content/presets/marketplace/LTX-2.5/modes/restore/tabs/references.yml")
    fields = yaml.safe_load(path.read_text(encoding="utf-8"))["fields"]
    task = next(field for field in fields if field["name"] == "restore_task")
    labels = [option["label"] for option in task["configuration"]["options"]]
    guide = _guide(ltx25_template, "restore")

    assert f"The {task['label']} field" in guide
    assert labels == ["Restore old footage", "Remove blur", "Remove compression"]
    assert all(label in guide for label in labels)


@pytest.mark.parametrize("mode", [*MODE_GUIDE_RULES, "video", "upscale", "extend", "refine_faces"])
def test_the_prompt_writer_gets_the_guide_of_the_active_mode(ltx25_template, mode):
    expected = ltx25_template.llm["modes"][mode]["guide"] if mode in MODE_GUIDE_RULES else ltx25_template.llm["guide"]

    assert _prompt_writer_guide(ltx25_template, mode) == expected


# -- default profile: distilled on every mode -------------------------------

def _generator_configs(record):
    return [
        p["config"] for p in record["pipes"]
        if p["name"] == "generator/video_ltx" and "steps" in p["config"]
    ]


def _rendered(ltx25_template, mode, speed_profile=None):
    form_data = build_fixture_form_data(ltx25_template, mode)
    if speed_profile is not None:
        form_data["speed_profile"] = speed_profile
    return render_preset_mode(build_processor(), ltx25_template, mode, form_data)


@pytest.fixture(scope="module")
def ltx25_preset():
    presets, _errors = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return next(p for p in presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5"))


def _speed_modes(preset):
    return sorted(
        mode for mode in preset.modes
        if _generator_configs(render_preset_mode(build_processor(), preset, mode))
    )


def test_every_generating_mode_defaults_to_the_distilled_profile(ltx25_preset):
    modes = _speed_modes(ltx25_preset)
    assert {"video", "extend", "cinemagraph", "day_to_night"} <= set(modes)
    for mode in modes:
        for config in _generator_configs(_rendered(ltx25_preset, mode)):
            assert config["steps"]["value"] == 8, mode
            assert config["cfg"]["value"] == 1.0, mode
            assert config["quality_mode"]["value"] is False, mode
            assert config["manual_sigmas"]["value"] == DISTILLED_RECIPE, mode


@pytest.mark.parametrize("mode", ["cinemagraph", "day_to_night"])
def test_distilled_run_ignores_the_modes_quality_cfg(ltx25_preset, mode):
    distilled = _generator_configs(_rendered(ltx25_preset, mode, "distilled"))[0]
    assert distilled["quality_mode"]["value"] is False
    assert distilled["cfg"]["value"] == 1.0
    quality = _generator_configs(_rendered(ltx25_preset, mode, "quality"))[0]
    assert quality["quality_mode"]["value"] is True
