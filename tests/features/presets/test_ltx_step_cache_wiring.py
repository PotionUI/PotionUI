"""Tests for FBCache step-cache wiring in the native LTX-2 preset's `video`
mode: the Advanced tab's three controls must reach `generator_stage1` (the
base generation pass), default to the pipe's own off values so an untouched
form renders exactly as it did before the controls existed, and must NOT
reach `generator_stage2` (the upscale refine pass) -- a deliberate choice,
see this file's own tests for the rationale.

Mirrors tests/features/presets/test_wan_step_cache_wiring.py. Unlike Wan's
`video` mode (which routes to one of three distinct generator pipes
depending on t2v/i2v/flf/director), LTX-2's `video` mode always runs the
same `generator/video_ltx` pipe (as `generator_stage1`), optionally followed
by a second instance (`generator_stage2`) when Upscale is on -- so there is
no per-mode parametrization here, only an upscale-on/upscale-off split.
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

PRESET_DIR = Path("content/presets/marketplace/LTX-2")

_CACHE_ON = {
    "step_cache_threshold": 0.12,
    "step_cache_warmup_steps": 6,
    "step_cache_max_skips": 2,
}

_KEYS = ("step_cache_threshold", "step_cache_warmup_steps", "step_cache_max_skips")


@pytest.fixture(scope="module")
def ltx_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2")), None)
    if template is None:
        pytest.skip("native/LTX-2 preset not present")
    return template


def _document() -> dict:
    """A normalized Video Director document, as the orchestrator hands it
    over -- plain t2v (no media, no audio, no IC-LoRA), the minimal shape
    generator_stage1/2 accept."""
    return {
        "mode": "t2v",
        "settings": {"seed": 123, "duration": 5, "fps": 24},
        "segments": [{"prompt": "a dragon", "negative_prompt": "blurry"}],
        "media": [],
        "media_images": [],
        "media_videos": [],
        "media_placements": [],
        "audio": [],
        "ic_lora": [],
    }


def _process(ltx_template, form_over: dict | None = None, *, upscale: str = "off", mode: str = "video"):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/ltx2.safetensors",
        "text_encoder": "/models/gemma3.safetensors",
        "resolution": "768x512",
    }
    if mode == "upscale":
        # Standalone `upscale` mode: a different mode entirely from the
        # in-flow upscale-on/off split above -- mirrors
        # test_ltx_upscale_ux.py's `_process` "upscale" branch. This mode's
        # raw-override field is named `refine_sigmas`, not
        # `upscale_refine_sigmas` (that's the `video` mode's field).
        form_data.update({
            "input_video": "/media/in.mp4",
            "upscale_model": "/models/upscaler.safetensors",
            "refine_sigmas": "",
        })
    else:
        form_data.update({
            "upscale": upscale,
            # `or`'d against a `preset.vars` fallback in generator_stage2's
            # `refine_sigmas` (never `| default(...)`, see that config key's own
            # pipeline.yml comment) -- Jinja's `or` needs the attribute defined
            # at all, unlike `default()` which tolerates Undefined.
            "upscale_refine_sigmas": "",
            "video_director": copy.deepcopy(_document()),
        })
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": mode, "form_data": form_data}
    return processor.process(ltx_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: the values reach generator_stage1, never generator_stage2 ----

def test_step_cache_values_reach_generator_stage1(ltx_template):
    pipes = _process(ltx_template, _CACHE_ON)
    generator = _pipe(pipes, "generator_stage1")
    assert generator["enabled"] is True
    cfg = generator["config"]
    assert cfg["step_cache_threshold"] == 0.12
    assert cfg["step_cache_warmup_steps"] == 6
    assert cfg["step_cache_max_skips"] == 2


def test_step_cache_values_reach_generator_stage1_with_upscale_on(ltx_template):
    # The controls must still land on stage 1 when the two-stage upscale
    # path is active -- stage 1's own config doesn't change shape just
    # because a stage 2 now follows it.
    pipes = _process(ltx_template, _CACHE_ON, upscale="2.0x")
    cfg = _pipe(pipes, "generator_stage1")["config"]
    assert cfg["step_cache_threshold"] == 0.12
    assert cfg["step_cache_warmup_steps"] == 6
    assert cfg["step_cache_max_skips"] == 2


def test_step_cache_values_arrive_as_the_declared_types(ltx_template):
    # The PipeConfigSpecs are float/int/int-typed; a string-rendered "0.12"
    # would still survive float() but drift the golden and the spec check.
    cfg = _pipe(pipes := _process(ltx_template, _CACHE_ON), "generator_stage1")["config"]
    assert pipes  # guard against an empty render silently passing the asserts
    assert isinstance(cfg["step_cache_threshold"], float)
    assert isinstance(cfg["step_cache_warmup_steps"], int)
    assert isinstance(cfg["step_cache_max_skips"], int)


def test_step_cache_dict_key_stays_unset_so_the_flat_controls_win(ltx_template):
    # sampler_step_cache_kwargs gives the `step_cache` dict outright priority
    # over the flat keys. If this pipeline ever set it, the three controls
    # would render into the config and still do nothing.
    cfg = _pipe(_process(ltx_template, _CACHE_ON), "generator_stage1")["config"]
    assert not cfg.get("step_cache"), "generator_stage1 sets step_cache"


def test_step_cache_never_reaches_generator_stage2(ltx_template):
    # Deliberate choice (see this preset's pipeline.yml comment above
    # generator_stage1's step_cache_* keys): stage 2 is a short, low-noise
    # REFINE pass, not the base generation pass -- caching a refine stage
    # is not assumed to behave like caching a base pass, so it stays
    # unbound here. A disabled-cache state must not leave stage 2
    # half-configured: none of the three keys should appear on it at all,
    # cached or not.
    cfg = _pipe(_process(ltx_template, _CACHE_ON, upscale="2.0x"), "generator_stage2")["config"]
    missing = [k for k in _KEYS if k in cfg]
    assert not missing, f"generator_stage2 unexpectedly carries {missing}"


def test_step_cache_never_reaches_upscale_mode_generator_refine(ltx_template):
    # Same deliberate exclusion, this preset's OTHER refine-only pipe: the
    # standalone `upscale` mode's `generator_refine` node (see the comment
    # above its `refine_sigmas` key in modes/upscale/pipeline.yml) is itself
    # the whole generative step in that mode -- there is no earlier
    # generator_stage1 feeding it, just latent_upscaler's VAE-encoded and
    # upsampled latent -- so it stays unbound here too, for the same reason
    # as generator_stage2 above.
    cfg = _pipe(_process(ltx_template, _CACHE_ON, mode="upscale"), "generator_refine")["config"]
    missing = [k for k in _KEYS if k in cfg]
    assert not missing, f"generator_refine unexpectedly carries {missing}"


# -- defaults: off unless the user asks for it -------------------------------

def test_step_cache_defaults_to_off(ltx_template):
    # threshold 0.0 is the pipe's own "never skip" value, so an untouched
    # form is byte-identical to not threading these keys at all.
    cfg = _pipe(_process(ltx_template), "generator_stage1")["config"]
    assert cfg["step_cache_threshold"] == 0.0
    assert cfg["step_cache_warmup_steps"] == 4
    assert cfg["step_cache_max_skips"] == 3


def test_step_cache_defaults_match_the_pipe_spec_defaults(ltx_template):
    # The preset must not invent its own defaults: pin them to the specs the
    # generator actually declares.
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_config_specs,
    )

    spec_defaults = {s.name: s.default for s in sampler_step_cache_config_specs()}
    cfg = _pipe(_process(ltx_template), "generator_stage1")["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_off_by_default_resolves_to_no_cache_at_all(ltx_template):
    # End-to-end on the resolver the pipe actually calls: an untouched form
    # must produce step_cache_options=None, not an empty-but-truthy dict.
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_kwargs,
    )

    cfg = _pipe(_process(ltx_template), "generator_stage1")["config"]
    assert sampler_step_cache_kwargs(cfg)["step_cache_options"] is None


def test_configured_values_resolve_into_the_denoise_kwargs(ltx_template):
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_kwargs,
    )

    cfg = _pipe(_process(ltx_template, _CACHE_ON), "generator_stage1")["config"]
    assert sampler_step_cache_kwargs(cfg)["step_cache_options"] == {
        "rel_threshold": 0.12,
        "warmup_steps": 6,
        "max_consecutive_skips": 2,
    }


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
def advanced_tab():
    return yaml.safe_load((PRESET_DIR / "modes/video/tabs/advanced.yml").read_text())


@pytest.mark.parametrize("name,default,min_v,max_v,step", [
    ("step_cache_threshold", 0.0, 0.0, 0.3, 0.01),
    ("step_cache_warmup_steps", 4, 0, 12, 1),
    ("step_cache_max_skips", 3, 0, 10, 1),
])
def test_step_cache_sliders_are_present_with_their_ranges(advanced_tab, name, default, min_v, max_v, step):
    field = _field_by_name(advanced_tab["fields"], name)
    assert field is not None, f"{name} is not on the Advanced tab"
    assert field["type"] == "slider"
    assert field["default"] == default
    assert field["configuration"]["min"] == min_v
    assert field["configuration"]["max"] == max_v
    assert field["configuration"]["step"] == step


def test_threshold_min_equals_the_off_value(advanced_tab):
    # A min above 0.0 would leave the user no way back to "off" from the UI.
    field = _field_by_name(advanced_tab["fields"], "step_cache_threshold")
    assert field["configuration"]["min"] == field["default"] == 0.0


@pytest.mark.parametrize("name", _KEYS)
def test_step_cache_controls_carry_user_facing_help(advanced_tab, name):
    # Krea-2 canon: every field-level `description` was stripped repo-wide and
    # any real guidance folded into `ai_hint` -- `description` must now be
    # ABSENT so the canon can't silently regress back to a dual source of
    # truth. `configuration.description` was always inert (base_field.
    # create_base_schema only ever reads top-level `description`) and stays
    # a preset_lint warning either way.
    field = _field_by_name(advanced_tab["fields"], name)
    assert field.get("ai_hint"), f"{name} has no ai_hint"
    assert "description" not in field, f"{name} still carries a description -- canon moved guidance into ai_hint"
    assert "description" not in (field.get("configuration") or {})


def test_threshold_help_is_honest_about_the_speed_for_fidelity_trade(advanced_tab):
    # The one thing a user must not be able to miss: this buys speed by
    # skipping work, and the skipped work cost something. Guidance now lives
    # entirely in ai_hint (description was stripped by the canon pass).
    field = _field_by_name(advanced_tab["fields"], "step_cache_threshold")
    assert "description" not in field
    ai_hint = field["ai_hint"]
    assert "speed-for-fidelity trade" in ai_hint
    assert "never a quality improvement" in ai_hint
    assert "Keep it at 0.0" in ai_hint
