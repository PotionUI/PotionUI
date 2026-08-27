"""Tests for FBCache step-cache wiring in the native Wan preset: the Advanced
tab's three controls must reach EVERY generator this mode can route to
(txt2vid / img2vid / chain), and must default to the pipes' own off values so an
untouched form renders exactly as it did before the controls existed.

The flat `step_cache_threshold`/`step_cache_warmup_steps`/`step_cache_max_skips`
keys are only consulted when the `step_cache` DICT config key is empty (see
sampler_step_cache_kwargs) -- this pipeline never sets that dict, which is what
makes the flat controls live.
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

PRESET_DIR = Path("content/presets/marketplace/Wan")

_CACHE_ON = {
    "step_cache_threshold": 0.12,
    "step_cache_warmup_steps": 6,
    "step_cache_max_skips": 2,
}

_KEYS = ("step_cache_threshold", "step_cache_warmup_steps", "step_cache_max_skips")

# mode -> the generator pipe that mode enables.
_GENERATOR_FOR_MODE = {
    "t2v": "generator/txt2vid_wan22",
    "i2v": "generator/img2vid_wan22",
    "flf": "generator/img2vid_wan22",
    "director": "generator/chain_video_wan22",
}


@pytest.fixture(scope="module")
def wan_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "native/Wan" in str(p.path)), None)
    if template is None:
        pytest.skip("native/Wan preset not present")
    return template


def _document(mode: str) -> dict:
    """A normalized Video Director document, as the orchestrator hands it over."""
    media = []
    if mode in ("i2v", "flf"):
        media.append({"role": "first", "media": {"path": "/media/first.png"}})
    if mode == "flf":
        media.append({"role": "last", "media": {"path": "/media/last.png"}})
    return {
        "mode": mode,
        "settings": {"seed": 123, "duration": 5, "fps": 24},
        "segments": [{"prompt": "a dragon", "negative_prompt": "blurry", "sub_type": mode}],
        "media": media,
        "needs_t2v_set": mode in ("t2v", "director"),
        "needs_i2v_set": mode in ("i2v", "flf", "director"),
    }


def _process(wan_template, mode: str, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
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
        "video_director": copy.deepcopy(_document(mode)),
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(wan_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: the values reach every generator this mode can route to ------

@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_step_cache_values_reach_the_enabled_generator(wan_template, mode):
    pipes = _process(wan_template, mode, _CACHE_ON)
    generator = _pipe(pipes, _GENERATOR_FOR_MODE[mode])
    assert generator["enabled"] is True
    cfg = generator["config"]
    assert cfg["step_cache_threshold"] == 0.12
    assert cfg["step_cache_warmup_steps"] == 6
    assert cfg["step_cache_max_skips"] == 2


@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_step_cache_values_arrive_as_the_declared_types(wan_template, mode):
    # The PipeConfigSpecs are float/int/int-typed; a string-rendered "0.12"
    # would still survive float() but drift the golden and the spec check.
    cfg = _pipe(pipes := _process(wan_template, mode, _CACHE_ON), _GENERATOR_FOR_MODE[mode])["config"]
    assert pipes  # guard against an empty render silently passing the asserts
    assert isinstance(cfg["step_cache_threshold"], float)
    assert isinstance(cfg["step_cache_warmup_steps"], int)
    assert isinstance(cfg["step_cache_max_skips"], int)


def test_step_cache_reaches_every_generator_that_declares_it(wan_template):
    # The controls must not be wired to only the obvious generator: whichever
    # mode the user picks, the pipe that actually runs has to see them. Each
    # mode is rendered in isolation because a disabled pipe's config is not
    # rendered at all.
    for mode, generator in _GENERATOR_FOR_MODE.items():
        cfg = _pipe(_process(wan_template, mode, _CACHE_ON), generator)["config"]
        missing = [k for k in _KEYS if k not in cfg]
        assert not missing, f"{generator} (mode {mode}) is missing {missing}"


def test_step_cache_dict_key_stays_unset_so_the_flat_controls_win(wan_template):
    # sampler_step_cache_kwargs gives the `step_cache` dict outright priority
    # over the flat keys. If this pipeline ever set it, the three controls
    # would render into the config and still do nothing.
    for mode, generator in _GENERATOR_FOR_MODE.items():
        cfg = _pipe(_process(wan_template, mode, _CACHE_ON), generator)["config"]
        assert not cfg.get("step_cache"), f"{generator} (mode {mode}) sets step_cache"


# -- defaults: off unless the user asks for it -------------------------------

@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_step_cache_defaults_to_off(wan_template, mode):
    # threshold 0.0 is the pipes' own "never skip" value, so an untouched form
    # is byte-identical to not threading these keys at all.
    cfg = _pipe(_process(wan_template, mode), _GENERATOR_FOR_MODE[mode])["config"]
    assert cfg["step_cache_threshold"] == 0.0
    assert cfg["step_cache_warmup_steps"] == 4
    assert cfg["step_cache_max_skips"] == 3


def test_step_cache_defaults_match_the_pipe_spec_defaults(wan_template):
    # The preset must not invent its own defaults: pin them to the specs the
    # generators actually declare.
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_config_specs,
    )

    spec_defaults = {s.name: s.default for s in sampler_step_cache_config_specs()}
    cfg = _pipe(_process(wan_template, "t2v"), "generator/txt2vid_wan22")["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_off_by_default_resolves_to_no_cache_at_all(wan_template):
    # End-to-end on the resolver the pipes actually call: an untouched form
    # must produce step_cache_options=None, not an empty-but-truthy dict.
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_kwargs,
    )

    cfg = _pipe(_process(wan_template, "t2v"), "generator/txt2vid_wan22")["config"]
    assert sampler_step_cache_kwargs(cfg)["step_cache_options"] is None


def test_configured_values_resolve_into_the_denoise_kwargs(wan_template):
    from src.pipelines.pipes._shared.generation.guidance_options import (
        sampler_step_cache_kwargs,
    )

    cfg = _pipe(_process(wan_template, "t2v", _CACHE_ON), "generator/txt2vid_wan22")["config"]
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
    assert "only removes work" in ai_hint
    assert "Keep it at 0.0" in ai_hint
