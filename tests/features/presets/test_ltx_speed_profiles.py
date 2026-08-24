"""Tests for the native LTX-2 preset's "Speed" selector (``form.speed_profile``,
): replaces the old ``distilled_mode``/``quality_mode`` checkboxes
with a single ``speed_profiles:`` selection (``balanced`` /
``distilled`` / ``quality``) on the ``video``/Director mode.

Each profile bakes steps/CFG/sampler (and, for ``distilled``/``quality``, the
manual sigma schedule and MultiModalGuider params) as a *baseline* that an
explicit form field still overrides -- the same "profile supplies the
baseline, form fields win" idiom every other ``speed_profiles:`` preset in
this repo uses (see docs/presets.md "Speed profiles"). Because there is now
exactly one selector field instead of two independently-toggleable booleans,
the old drift bug (CFG slider showing 1 while generation ran CFG~3 because
both checkboxes ended up engaged at once) is structurally unrepresentable --
``test_video_legacy_checkbox_keys_are_inert`` proves the removed keys have no
effect at all, not just that they're hard to set simultaneously.

Complements ``test_video_director_pipeline.py`` (which owns the broader
Director-mode pipeline contract; this file is scoped to the speed-profile
selector alone). Replaces the retired ``test_ltx_distilled_mode.py`` /
``test_ltx_quality_mode.py``. The standalone
``txt2vid`` MODE (the ``video``/Director mode already covers plain t2v -- a
Director document with no keyframes); this file's former "-- txt2vid mode --"
section covered the exact same speed-profile behavior the "-- video (Director)
mode --" section below already independently exercises, so it was deleted
rather than ported (no coverage was lost).
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


@pytest.fixture(scope="module")
def ltx_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2")), None)
    if template is None:
        pytest.skip("native/LTX-2 preset not present")
    return template


def _pipe(pipes, name):
    return next(p for p in pipes if p["name"] == name)


# -- video (Director) mode ------------------------------------------------------

_DOC_T2V = {
    "settings": {"seed": 123, "duration": 5, "fps": 25},
    "segments": [{"prompt": "a cat", "negative_prompt": "ugly"}],
    "media": [], "audio": [], "ic_lora": [],
}


def _process_video(ltx_template, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "video_director": doc,
        "model": "/models/ltx.safetensors",
        "text_encoder": "/models/gemma3.safetensors",
        "resolution": "768x512",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(ltx_template, generation_data)


def test_video_balanced_is_the_default(ltx_template):
    pipes = _process_video(ltx_template)
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["steps"] == 24
    assert gen["config"]["cfg"] == 4.0
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["manual_sigmas"] == ""
    assert gen["config"]["quality_mode"] is False
    encoder = _pipe(pipes, "prompt_encoder")
    assert encoder["config"]["guidance_scale"] == 4.0


def test_video_balanced_respects_explicit_fields(ltx_template):
    pipes = _process_video(ltx_template, form_over={
        "speed_profile": "balanced", "sampler": "dpmpp_2m", "cfg": 7.0, "steps": 10,
        "manual_sigmas": "1.0, 0.5, 0.0",
    })
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["sampler"] == "dpmpp_2m"
    assert gen["config"]["cfg"] == 7.0
    assert gen["config"]["steps"] == 10
    assert gen["config"]["manual_sigmas"] == "1.0, 0.5, 0.0"


def test_video_distilled_profile_bakes_recipe(ltx_template):
    # Sampler is euler_cfg_pp (deterministic), not euler_ancestral_cfg_pp --
    # Lightricks' own distilled pass runs no ancestral noise (see
    # docs/models/ltx.md's "First-party validation" section); the earlier
    # ancestral default is still selectable, just no longer the baked default.
    pipes = _process_video(ltx_template, form_over={"speed_profile": "distilled"})
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["steps"] == 8
    assert gen["config"]["cfg"] == 1.0
    assert gen["config"]["sampler"] == "euler_cfg_pp"
    assert gen["config"]["manual_sigmas"] == DISTILLED_RECIPE
    assert gen["config"]["quality_mode"] is False
    encoder = _pipe(pipes, "prompt_encoder")
    assert encoder["config"]["guidance_scale"] == 1.0


def test_video_distilled_profile_baseline_still_yields_to_explicit_fields(ltx_template):
    # See the txt2vid counterpart for why manual_sigmas is excluded from this
    # "explicit field wins" check: it's a deliberate hard override while
    # Distilled is selected, not a baseline.
    pipes = _process_video(ltx_template, form_over={
        "speed_profile": "distilled", "sampler": "euler", "cfg": 7.0, "steps": 10,
        "manual_sigmas": "1.0, 0.5, 0.0",
    })
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["cfg"] == 7.0
    assert gen["config"]["steps"] == 10
    assert gen["config"]["manual_sigmas"] == DISTILLED_RECIPE


def test_video_quality_profile_bakes_recipe(ltx_template):
    pipes = _process_video(ltx_template, form_over={"speed_profile": "quality"})
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["steps"] == 30
    assert gen["config"]["cfg"] == 3.0
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["quality_mode"] is True
    assert gen["config"]["quality_cfg"] == 3.0
    assert gen["config"]["quality_stg"] == 1.0
    assert gen["config"]["quality_rescale"] == 0.7
    assert gen["config"]["quality_modality"] == 3.0
    assert gen["config"]["quality_stg_blocks"] == "28"


def test_video_quality_profile_prompt_encoder_encodes_negative(ltx_template):
    pipes = _process_video(ltx_template, form_over={"speed_profile": "quality"})
    encoder = _pipe(pipes, "prompt_encoder")
    assert encoder["config"]["guidance_scale"] == 3.0


def test_video_legacy_checkbox_keys_are_inert(ltx_template):
    pipes = _process_video(ltx_template, form_over={"distilled_mode": True, "quality_mode": True})
    gen = _pipe(pipes, "generator/video_ltx")
    assert gen["config"]["steps"] == 24
    assert gen["config"]["cfg"] == 4.0
    assert gen["config"]["sampler"] == "euler"
    assert gen["config"]["quality_mode"] is False


def test_video_cfg_stays_native_float_type_all_profiles(ltx_template):
    # Regression guard ported from the retired txt2vid-mode test of the same
    # name: an earlier draft used a multi-block Jinja
    # {% if %} for this override, which the template engine renders as a
    # STRING (only a single {{ expression }} preserves native types -- see
    # TemplateProcessor.process_template's docstring). A string "1.0"/"4.0"
    # would still work end-to-end (validate_pipe_configuration coerces
    # str->float), but the get_speed_profile()/default idiom is cheaper and
    # keeps golden snapshots stable -- assert the type directly so a
    # regression back to {% if %} is caught here, not just in a golden diff.
    for profile in ("balanced", "distilled", "quality"):
        pipes = _process_video(ltx_template, form_over={"speed_profile": profile})
        assert isinstance(_pipe(pipes, "generator/video_ltx")["config"]["cfg"], float)
