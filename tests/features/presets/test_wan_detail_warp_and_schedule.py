"""Tests for the Wan preset's Detail warp (sigma daemon) controls and the
`linear_quadratic` schedule option, added on the Advanced tab.

Three seams are exercised:

- Preset rendering (real `PresetTemplateLoader` + `PresetProcessor`): the
  Advanced tab's fields must reach every generator this mode can route to
  (t2v/i2v/flf/director), typed correctly, and default to the pipes' own
  no-op values so an untouched form renders exactly as it did before these
  controls existed -- same idiom as `test_wan_step_cache_wiring.py`.
- The real override/build seam a rendered config actually feeds:
  `schedule_settings_overrides()` -> `build_sigmas()`. This is where the
  `linear_quadratic_linear_steps` field's "optional number, native None when
  automatic" wiring earns its keep -- a naive render would hand `build_sigmas`
  the STRING "" instead of `None`, which crashes `int("")` inside
  `_linear_quadratic_sigmas`.
- The REAL request boundary (`bind_form` -> `PipelineBuilder.build_pipeline`),
  not just `PresetProcessor.process` with a hand-typed form dict: this is
  where `linear_quadratic_linear_steps_auto` earns its keep. The shared
  `NumberInput.svelte` component sends 0 (never a real "unset") when its
  input is cleared (`parseFloat(target.value) || 0` in `handleInput`), and a
  submitted 0 is not "absent" to `bind_form` -- a client-sent value always
  wins over a field's own default. Without a dedicated automatic flag, a
  stale 0 left behind by a clear would render as `linear_steps=0`, which
  `_linear_quadratic_sigmas` floors to 1 -- silently NOT the advertised
  automatic `steps // 2`. The automatic checkbox makes the numeric field's
  submitted value irrelevant whenever it is true, regardless of what stale
  number sits in it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.forms.binding import bind_form
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.video_director.normalize import derive_segment_routing
from src.pipelines.pipes._shared.generation.guidance_options import schedule_settings_overrides
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/Wan")

_GENERATOR_FOR_MODE = {
    "t2v": "generator/txt2vid_wan22",
    "i2v": "generator/img2vid_wan22",
    "flf": "generator/img2vid_wan22",
    "director": "generator/chain_video_wan22",
}

_DETAIL_KEYS = ("detail_strength", "detail_start", "detail_end")

_DETAIL_ON = {
    "detail_strength": 0.2,
    "detail_start": 0.2,
    "detail_end": 0.8,
}

_LQ_ON = {
    "schedule": "linear_quadratic",
    "linear_quadratic_threshold_noise": 0.05,
    "linear_quadratic_linear_steps_auto": False,
    "linear_quadratic_linear_steps": 12,
}


@pytest.fixture(scope="module")
def wan_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).rstrip("/").endswith("/Wan")), None)
    if template is None:
        pytest.skip("Wan preset not present")
    return template


def _document(mode: str) -> dict:
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
    bound = bind_form(
        wan_template, "video", form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {"prompts": [], "mode": "video", "form_data": dict(bound.values)}
    return processor.process(wan_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: detail warp reaches every generator, typed, off by default ---

@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_detail_warp_defaults_to_off(wan_template, mode):
    # 0.0/0.1/0.9 are build_sigmas' own no-op values (strength 0 skips the warp
    # outright), so an untouched form is byte-identical to not threading these
    # keys at all.
    cfg = _pipe(_process(wan_template, mode), _GENERATOR_FOR_MODE[mode])["config"]
    assert cfg["detail_strength"] == 0.0
    assert cfg["detail_start"] == 0.1
    assert cfg["detail_end"] == 0.9
    for key in _DETAIL_KEYS:
        assert isinstance(cfg[key], float)


@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_detail_warp_values_reach_the_enabled_generator(wan_template, mode):
    cfg = _pipe(_process(wan_template, mode, _DETAIL_ON), _GENERATOR_FOR_MODE[mode])["config"]
    assert cfg["detail_strength"] == pytest.approx(0.2)
    assert cfg["detail_start"] == pytest.approx(0.2)
    assert cfg["detail_end"] == pytest.approx(0.8)


def test_detail_warp_reaches_every_generator_that_declares_it(wan_template):
    for mode, generator in _GENERATOR_FOR_MODE.items():
        cfg = _pipe(_process(wan_template, mode, _DETAIL_ON), generator)["config"]
        missing = [k for k in _DETAIL_KEYS if k not in cfg]
        assert not missing, f"{generator} (mode {mode}) is missing {missing}"


# -- rendering: linear_quadratic schedule_options ----------------------------

@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_schedule_options_default_is_the_native_none_sentinel(wan_template, mode):
    # The untouched `linear_quadratic_linear_steps` field must arrive as a real
    # Python None, not the empty string other optional-number fields in this
    # preset (e.g. expert_switch_step) render to -- build_sigmas' linear_steps
    # branch checks `is None`, not falsy-string.
    cfg = _pipe(_process(wan_template, mode), _GENERATOR_FOR_MODE[mode])["config"]
    assert cfg["schedule_options"]["linear_steps"] is None
    assert cfg["schedule_options"]["threshold_noise"] == 0.025
    assert isinstance(cfg["schedule_options"]["threshold_noise"], float)


@pytest.mark.parametrize("mode", ["t2v", "i2v", "flf", "director"])
def test_schedule_options_values_reach_the_enabled_generator_as_native_types(wan_template, mode):
    cfg = _pipe(_process(wan_template, mode, _LQ_ON), _GENERATOR_FOR_MODE[mode])["config"]
    assert cfg["schedule"] == "linear_quadratic"
    assert cfg["schedule_options"]["threshold_noise"] == pytest.approx(0.05)
    assert cfg["schedule_options"]["linear_steps"] == 12
    assert isinstance(cfg["schedule_options"]["linear_steps"], int)


def test_linear_quadratic_is_a_valid_schedule_choice(wan_template):
    """The pipe spec pins no choice list any more -- the schedule registry is
    what decides which keys `build_sigmas` accepts."""
    from src.pipelines.pipes._shared.generation.guidance_options import schedule_settings_config_specs
    from src.platform.runtime.native.sampling.registry import schedule_registry

    spec = next(s for s in schedule_settings_config_specs() if s.name == "schedule")
    assert spec.choices is None
    assert "linear_quadratic" in spec.description
    assert schedule_registry.has("linear_quadratic")


# -- the real override/build seam: schedule_settings_overrides -> build_sigmas -

def test_unset_detail_fields_render_sigmas_identical_to_no_detail_kwargs(wan_template):
    cfg = _pipe(_process(wan_template, "t2v"), "generator/txt2vid_wan22")["config"]
    overrides = schedule_settings_overrides(cfg)
    baseline = build_sigmas(20, shift=8.0)
    rendered = build_sigmas(20, shift=8.0, **overrides)
    assert __import__("torch").equal(baseline, rendered)


def test_configured_detail_strength_actually_warps_the_schedule(wan_template):
    cfg = _pipe(_process(wan_template, "t2v", _DETAIL_ON), "generator/txt2vid_wan22")["config"]
    overrides = schedule_settings_overrides(cfg)
    baseline = build_sigmas(20, shift=8.0)
    warped = build_sigmas(20, shift=8.0, **overrides)
    assert not __import__("torch").equal(baseline, warped)


def test_rendered_linear_quadratic_config_matches_the_reference_schedule(wan_template):
    # End-to-end: the preset's rendered config, fed through the exact seam the
    # pipes use, must reproduce build_sigmas' own linear_quadratic output for
    # the same threshold/linear_steps -- proving the native-None/int passthrough
    # from the form field survives the whole render, not just a mocked dict.
    cfg = _pipe(_process(wan_template, "t2v", _LQ_ON), "generator/txt2vid_wan22")["config"]
    overrides = schedule_settings_overrides(cfg)
    rendered = build_sigmas(20, shift=8.0, **overrides)
    reference = build_sigmas(
        20, schedule="linear_quadratic",
        schedule_options={"threshold_noise": 0.05, "linear_steps": 12},
    )
    assert __import__("torch").equal(rendered, reference)


def test_rendered_linear_quadratic_with_unset_linear_steps_does_not_crash(wan_template):
    # linear_quadratic_linear_steps left blank must resolve to native None so
    # build_sigmas falls back to steps // 2 -- a stringly-typed "" would raise
    # inside _linear_quadratic_sigmas' int(linear_steps).
    cfg = _pipe(
        _process(wan_template, "t2v", {"schedule": "linear_quadratic"}),
        "generator/txt2vid_wan22",
    )["config"]
    overrides = schedule_settings_overrides(cfg)
    rendered = build_sigmas(20, shift=8.0, **overrides)
    reference = build_sigmas(20, schedule="linear_quadratic", schedule_options={"threshold_noise": 0.025})
    assert __import__("torch").equal(rendered, reference)


def test_automatic_flag_ignores_a_stale_numeric_value_end_to_end(wan_template):
    # The exact scenario NumberInput.svelte's clear-to-0 leaves behind: the
    # user re-checks "Automatic" but the underlying linear_quadratic_linear_steps
    # field still carries a leftover value (0, or anything else) rather than a
    # real unset. The automatic flag must make it irrelevant regardless.
    form_over = {
        "schedule": "linear_quadratic",
        "linear_quadratic_linear_steps_auto": True,
        "linear_quadratic_linear_steps": 0,
    }
    cfg = _pipe(_process(wan_template, "t2v", form_over), "generator/txt2vid_wan22")["config"]
    assert cfg["schedule_options"]["linear_steps"] is None
    overrides = schedule_settings_overrides(cfg)
    rendered = build_sigmas(20, shift=8.0, **overrides)
    reference = build_sigmas(20, schedule="linear_quadratic", schedule_options={"threshold_noise": 0.025})
    assert __import__("torch").equal(rendered, reference)


def test_manual_sigmas_takes_priority_over_linear_quadratic_end_to_end(wan_template):
    # manual_sigmas set alongside schedule=linear_quadratic: the manual list
    # must win outright (schedule_settings_overrides' own contract), so the
    # rendered linear_quadratic knobs are silently ignored, not combined.
    form_over = {**_LQ_ON, "manual_sigmas": "1.0, 0.5, 0.0"}
    cfg = _pipe(_process(wan_template, "t2v", form_over), "generator/txt2vid_wan22")["config"]
    overrides = schedule_settings_overrides(cfg)
    assert overrides["schedule"] == "manual"
    rendered = build_sigmas(20, shift=8.0, **overrides)
    assert __import__("torch").equal(rendered, __import__("torch").tensor([1.0, 0.5, 0.0]))


# -- form definition ----------------------------------------------------------

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
    ("detail_strength", 0.0, -0.3, 0.3, 0.01),
    ("detail_start", 0.1, 0.0, 1.0, 0.05),
    ("detail_end", 0.9, 0.0, 1.0, 0.05),
])
def test_detail_warp_sliders_are_present_with_their_ranges(advanced_tab, name, default, min_v, max_v, step):
    field = _field_by_name(advanced_tab["fields"], name)
    assert field is not None, f"{name} is not on the Advanced tab"
    assert field["type"] == "slider"
    assert field["default"] == default
    assert field["configuration"]["min"] == min_v
    assert field["configuration"]["max"] == max_v
    assert field["configuration"]["step"] == step


def test_schedule_select_offers_linear_quadratic(advanced_tab):
    field = _field_by_name(advanced_tab["fields"], "schedule")
    values = [opt["value"] for opt in field["configuration"]["options"]]
    assert "linear_quadratic" in values


def test_linear_quadratic_threshold_and_auto_toggle_hidden_unless_schedule_picked(advanced_tab):
    for name in ("linear_quadratic_threshold_noise", "linear_quadratic_linear_steps_auto"):
        field = _field_by_name(advanced_tab["fields"], name)
        assert field is not None, f"{name} is not on the Advanced tab"
        reactions = field["reactions"]
        shown = next(r for r in reactions if r["when"]["equals"] == "linear_quadratic")
        hidden = next(r for r in reactions if r["when"].get("not_equals") == "linear_quadratic")
        assert shown["then"]["set_visibility"] is True
        assert hidden["then"]["set_visibility"] is False


def test_linear_quadratic_linear_steps_auto_defaults_to_true(advanced_tab):
    field = _field_by_name(advanced_tab["fields"], "linear_quadratic_linear_steps_auto")
    assert field["type"] == "checkbox"
    assert field["default"] is True


def test_linear_quadratic_linear_steps_visible_only_when_not_automatic(advanced_tab):
    # Single-condition reactions only (bind_form's reaction evaluator has no
    # AND/OR support -- see test_reaction_resolution.py's
    # TestRealPresetTreeReactionShapes): this field keys off the automatic
    # flag alone, not a compound condition with `schedule` too.
    field = _field_by_name(advanced_tab["fields"], "linear_quadratic_linear_steps")
    assert field is not None, "linear_quadratic_linear_steps is not on the Advanced tab"
    reactions = field["reactions"]

    shown = next(r for r in reactions if r["when"]["equals"] is False)
    assert shown["when"]["field"] == "linear_quadratic_linear_steps_auto"
    assert shown["then"]["set_visibility"] is True

    hidden = next(r for r in reactions if r["when"]["equals"] is True)
    assert hidden["when"]["field"] == "linear_quadratic_linear_steps_auto"
    assert hidden["then"]["set_visibility"] is False
    # Resets the stale value to a valid in-bounds placeholder (the field's own
    # min) whenever hidden, so a leftover 0 from a NumberInput clear can never
    # reach `bind_form`'s min-range validation while the field is inert.
    assert hidden["then"]["set_value"] == 1


def test_linear_quadratic_linear_steps_min_is_zero_not_one(advanced_tab):
    # NumberInput.svelte's clear handler sends 0, never a real unset -- the
    # field's own bounds must tolerate that stale value even if the reset
    # reaction above somehow didn't fire (e.g. a raw API resubmission of an
    # old payload), since `_linear_quadratic_sigmas` already floors it at 1
    # rather than erroring.
    field = _field_by_name(advanced_tab["fields"], "linear_quadratic_linear_steps")
    assert field["configuration"]["min"] == 0


# -- the ACTUAL request boundary: bind_form -> PipelineBuilder ---------------
#
# Everything above renders through PresetProcessor.process() with a hand-typed
# form dict, which never exercises field defaulting/reaction-resolution/
# validation -- the exact layer where a submitted "0" is or isn't distinct
# from "absent". These go through the real `bind_form` (the production
# request boundary `GenerationOrchestrator.start_generation` calls) and the
# real `PipelineBuilder`, mirroring `tests/features/generation/
# test_model_identity_wan_preset.py`'s idiom for driving the marketplace Wan
# preset end to end.

_T2V_DOC = {
    "mode": "t2v",
    "settings": {"seed": 424242, "duration": 5, "fps": 16},
    "segments": [{"prompt": "a dragon", "negative_prompt": "blurry"}],
    "media": [],
}


def _ref(model_id: str) -> str:
    return f"model:{model_id}"


@pytest.fixture(scope="module")
def wan_pipeline_builder(wan_template):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return PipelineBuilder(preset_template_loader=Mock(), preset_processor=processor), wan_template


def _bind_and_build(wan_pipeline_builder, raw_form_over: dict):
    """Raw WIRE form_data (as a browser submission would send it) through the
    real `bind_form` boundary, then the real `PipelineBuilder` -- the same two
    calls `GenerationOrchestrator.start_generation` makes back to back (see
    `src/features/generation/orchestrator.py`), minus the enqueue/persistence
    side effects neither this test nor a preview needs."""
    builder, template = wan_pipeline_builder
    doc = copy.deepcopy(_T2V_DOC)
    doc.update(derive_segment_routing(doc["segments"], doc["media"]))
    raw_form_data = {
        "video_director": doc,
        "t2v_high_noise_model": _ref("t2v-high"),
        "t2v_low_noise_model": _ref("t2v-low"),
        "i2v_high_noise_model": _ref("i2v-high"),
        "i2v_low_noise_model": _ref("i2v-low"),
        "text_encoder": _ref("shared-te"),
        "vae": _ref("shared-vae"),
        "resolution": "832x480",
    }
    raw_form_data.update(raw_form_over)
    bound = bind_form(
        template, "video", None, raw_form_data, user_id=None,
        storage_dir=None, field_overrides=None,
    )
    built = builder.build_pipeline(
        preset_id=template, form_data=bound.values, mode="video",
        form_name=bound.form_name, user_id=None,
    )
    return bound, built.pipes


def _schedule_options(pipes):
    return _pipe(pipes, "generator/txt2vid_wan22")["config"]["schedule_options"]


def test_bind_form_initial_submission_is_automatic(wan_pipeline_builder):
    # A brand-new submission that never touches the Advanced tab at all --
    # bind_form fills in every declared field's own default (auto=true,
    # linear_steps absent), and the render must resolve linear_steps to a
    # native None reaching build_sigmas' own steps // 2 fallback.
    bound, pipes = _bind_and_build(wan_pipeline_builder, {"schedule": "linear_quadratic"})
    assert bound.values["linear_quadratic_linear_steps_auto"] is True
    options = _schedule_options(pipes)
    assert options["linear_steps"] is None
    reference = build_sigmas(20, schedule="linear_quadratic", schedule_options={"threshold_noise": 0.025})
    rendered = build_sigmas(20, shift=8.0, **schedule_settings_overrides(_pipe(pipes, "generator/txt2vid_wan22")["config"]))
    assert __import__("torch").equal(rendered, reference)


def test_bind_form_explicit_value_is_bound_and_typed(wan_pipeline_builder):
    # The user unchecks Automatic and types a real value: bind_form validates
    # and coerces it (numeric-string leniency included), and it must reach
    # the generator as a real int.
    bound, pipes = _bind_and_build(wan_pipeline_builder, {
        "schedule": "linear_quadratic",
        "linear_quadratic_linear_steps_auto": False,
        "linear_quadratic_linear_steps": 12,
    })
    assert bound.values["linear_quadratic_linear_steps_auto"] is False
    assert bound.values["linear_quadratic_linear_steps"] == 12
    options = _schedule_options(pipes)
    assert options["linear_steps"] == 12
    assert isinstance(options["linear_steps"], int)


def test_bind_form_clear_and_reset_to_automatic_ignores_the_stale_value(wan_pipeline_builder):
    # The exact NumberInput.svelte scenario: the user had typed 12, cleared
    # the box (which sends 0, never a real unset) and re-checked Automatic.
    # bind_form's own numeric validation must not reject the now-in-bounds
    # stale 0 (min is 0, not 1 -- see test_linear_quadratic_linear_steps_min_is_zero_not_one),
    # and the render must ignore it outright because Automatic is true.
    bound, pipes = _bind_and_build(wan_pipeline_builder, {
        "schedule": "linear_quadratic",
        "linear_quadratic_linear_steps_auto": True,
        "linear_quadratic_linear_steps": 0,
    })
    assert bound.values["linear_quadratic_linear_steps_auto"] is True
    assert bound.values["linear_quadratic_linear_steps"] == 0
    options = _schedule_options(pipes)
    assert options["linear_steps"] is None
    reference = build_sigmas(20, schedule="linear_quadratic", schedule_options={"threshold_noise": 0.025})
    rendered = build_sigmas(20, shift=8.0, **schedule_settings_overrides(_pipe(pipes, "generator/txt2vid_wan22")["config"]))
    assert __import__("torch").equal(rendered, reference)


def test_bind_form_saved_automatic_flag_round_trips(wan_pipeline_builder):
    # A saved/restored session resubmits its own bound values verbatim (the
    # normal "reuse this generation's settings" path) -- the automatic flag
    # itself must survive a bind_form round trip unchanged, both ways.
    bound_auto, _ = _bind_and_build(wan_pipeline_builder, {
        "schedule": "linear_quadratic",
        "linear_quadratic_linear_steps_auto": True,
    })
    resubmitted_auto, _ = _bind_and_build(wan_pipeline_builder, dict(bound_auto.values))
    assert resubmitted_auto.values["linear_quadratic_linear_steps_auto"] is True

    bound_manual, _ = _bind_and_build(wan_pipeline_builder, {
        "schedule": "linear_quadratic",
        "linear_quadratic_linear_steps_auto": False,
        "linear_quadratic_linear_steps": 7,
    })
    resubmitted_manual, _ = _bind_and_build(wan_pipeline_builder, dict(bound_manual.values))
    assert resubmitted_manual.values["linear_quadratic_linear_steps_auto"] is False
    assert resubmitted_manual.values["linear_quadratic_linear_steps"] == 7
