from __future__ import annotations

from unittest.mock import Mock

import pytest
import yaml

from src.features.forms.binding import bind_form
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_MODELS = {
    "model": "/models/minimax_h3.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
}
_ADAPTER = "/models/pika_h3_flex_fl2va_v1_bf16.safetensors"
_VIDEO_LADDER = "1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0"
_AUDIO_LADDER = "1.0, 0.9, 0.75, 0.5, 0.25, 0.111111, 0.0"
_STYLE_LORA = [{"model": "/models/style.safetensors", "strength": 0.8}]

_FORMS = {
    "video": {"resolution": "832x480"},
    "extend": {"source_video": "/media/clip.mp4", "added_seconds": 4},
    "refs": {"references": [{"type": "image", "path": "/media/ref.png"}], "resolution": "832x480"},
    "remix": {"source_video": "/media/clip.mp4"},
    "talk": {"portrait": "/media/face.png", "voice": "/media/voice.wav"},
}
_MODES = tuple(_FORMS)


def _process(template, mode, form=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, mode, form_name=None, raw_form_data={**_MODELS, **_FORMS[mode], **(form or {})},
        user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": [{"positive": "a dragon", "negative": ""}], "mode": mode, "form_data": dict(bound.values),
    })


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/video_minimax_h3")["config"]


def _loader(pipes):
    return next(p for p in pipes if p["name"] == "model_loader/minimax_h3")["config"]


def _flex(template, mode, **extra):
    return _process(template, mode, {"speed_profile": "flex", "flex_adapter": _ADAPTER, **extra})


def _runtime_files(config):
    return [entry["file_path"] for entry in config.get("runtime_loras") or []]


@pytest.mark.parametrize("mode", _MODES)
def test_flex_renders_the_six_knot_recipe(h3_template, mode):
    cfg = _generator(_flex(h3_template, mode))
    assert cfg["sampler"] == "lcm"
    assert cfg["scheduler"] == "simple"
    assert cfg["manual_sigmas"] == _VIDEO_LADDER
    assert cfg["manual_audio_sigmas"] == _AUDIO_LADDER
    assert cfg["step_cache_threshold"] == 0.0
    assert cfg["steps"] == 6


@pytest.mark.parametrize("mode", _MODES)
def test_the_flex_ladders_have_one_more_knot_than_steps(h3_template, mode):
    cfg = _generator(_flex(h3_template, mode))
    for ladder in (cfg["manual_sigmas"], cfg["manual_audio_sigmas"]):
        knots = [float(value) for value in ladder.split(",")]
        assert len(knots) == cfg["steps"] + 1
        assert knots[0] == 1.0 and knots[-1] == 0.0


@pytest.mark.parametrize("mode", _MODES)
def test_flex_applies_the_adapter_at_runtime_and_never_bakes_it(h3_template, mode):
    pipes = _flex(h3_template, mode)
    assert _runtime_files(_generator(pipes)) == [_ADAPTER]
    assert list(_loader(pipes)["loras"]) == []


@pytest.mark.parametrize("mode", _MODES)
def test_flex_ignores_the_lora_picker(h3_template, mode):
    pipes = _flex(h3_template, mode, loras=_STYLE_LORA)
    assert _runtime_files(_generator(pipes)) == [_ADAPTER]
    assert list(_loader(pipes)["loras"]) == []


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("profile", ("quality", "turbo", "custom"))
def test_the_other_profiles_render_exactly_as_before(h3_template, mode, profile):
    pipes = _process(h3_template, mode, {"speed_profile": profile, "flex_adapter": _ADAPTER, "loras": _STYLE_LORA})
    cfg = _generator(pipes)
    assert cfg["sampler"] == "euler"
    assert cfg["scheduler"] == "simple"
    assert not cfg["manual_sigmas"]
    assert not cfg["manual_audio_sigmas"]
    assert _runtime_files(cfg) == []
    assert [entry["file_path"] for entry in _loader(pipes)["loras"]] == ["/models/style.safetensors"]


@pytest.mark.parametrize("mode", _MODES)
def test_the_flex_choice_is_offered_beside_turbo(h3_template, mode):
    path = f"content/presets/marketplace/MiniMax-H3/modes/{mode}/tabs/generation.yml"
    with open(path, encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]
    options = [o["value"] for o in next(f for f in fields if f.get("name") == "speed_profile")["configuration"]["options"]]
    assert options == ["quality", "turbo", "flex", "custom"]


def test_upscale_never_offers_flex():
    with open("content/presets/marketplace/MiniMax-H3/modes/upscale/tabs/generation.yml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]
    options = [o["value"] for o in next(f for f in fields if f.get("name") == "speed_profile")["configuration"]["options"]]
    assert "flex" not in options


def test_turbo_stays_the_video_default():
    with open("content/presets/marketplace/MiniMax-H3/modes/video/tabs/generation.yml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]
    assert next(f for f in fields if f.get("name") == "speed_profile")["default"] == "turbo"


def test_flex_pins_the_steps_slider_and_hides_the_solver_knobs():
    with open("content/presets/marketplace/MiniMax-H3/files/form/advanced_sampling.yml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]

    def walk(nodes):
        for node in nodes:
            if node.get("type") in ("row", "section"):
                yield from walk(node.get("children", []))
            else:
                yield node

    by_name = {f["name"]: f for f in walk(fields) if f.get("name")}
    steps = next(r for r in by_name["steps"]["reactions"] if r["when"]["equals"] == "flex")
    assert steps["then"] == {"set_value": 6, "set_disabled": True}
    for name in ("sampler", "scheduler", "manual_sigmas", "manual_audio_sigmas"):
        hide = next(r for r in by_name[name]["reactions"] if r["when"].get("equals") == "flex")
        assert hide["then"] == {"set_visibility": False}


@pytest.mark.parametrize("kind, mode", (("fl2va", "extend"), ("ref2va", "remix"), ("ref2va", "talk")))
def test_the_shared_model_files_carry_a_pinned_flex_adapter(kind, mode):
    name = "models_fl2va" if kind == "fl2va" else "models_ref2va"
    with open(f"content/presets/marketplace/MiniMax-H3/files/form/{name}.yml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]
    adapter = next(f for f in fields if f["name"] == "flex_adapter")
    recommendation = adapter["configuration"]["recommendations"][0]
    assert recommendation["name"] == f"pika_h3_flex_{kind}_v1_bf16.safetensors"
    assert len(recommendation["sha256"]) == 64
    assert adapter["configuration"]["model_type"] == "lora"
    assert {r["then"]["set_required"] for r in adapter["reactions"]} == {True, False}


@pytest.mark.parametrize("mode, kind", (("video", "fl2va"), ("refs", "ref2va")))
def test_the_inline_model_sections_carry_a_pinned_flex_adapter(mode, kind):
    with open(f"content/presets/marketplace/MiniMax-H3/modes/{mode}/tabs/generation.yml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)["fields"]
    models = next(f for f in fields if f.get("label") == "Models")["children"]
    adapter = next(f for f in models if f["name"] == "flex_adapter")
    recommendation = adapter["configuration"]["recommendations"][0]
    assert recommendation["name"] == f"pika_h3_flex_{kind}_v1_bf16.safetensors"
    assert len(recommendation["sha256"]) == 64
    assert {r["then"]["set_required"] for r in adapter["reactions"]} == {True, False}


@pytest.mark.parametrize("mode", _MODES)
def test_the_hand_set_solver_knobs_still_reach_the_generator_outside_flex(h3_template, mode):
    cfg = _generator(_process(h3_template, mode, {
        "speed_profile": "custom", "sampler": "dpmpp_2m", "manual_sigmas": "1.0, 0.5, 0.0",
        "manual_audio_sigmas": "1.0, 0.4, 0.0", "step_cache_threshold": 0.1,
    }))
    assert cfg["sampler"] == "dpmpp_2m"
    assert cfg["manual_sigmas"] == "1.0, 0.5, 0.0"
    assert cfg["manual_audio_sigmas"] == "1.0, 0.4, 0.0"
    assert cfg["step_cache_threshold"] == 0.1


def test_flex_hides_the_lora_picker_and_the_step_cache_fields():
    root = "content/presets/marketplace/MiniMax-H3"
    with open(f"{root}/modes/video/tabs/lora.yml", encoding="utf-8") as handle:
        picker = yaml.safe_load(handle)["fields"][0]
    with open(f"{root}/files/form/advanced_step_cache.yml", encoding="utf-8") as handle:
        cache = yaml.safe_load(handle)["fields"][0]["children"]
    for field in (picker, *cache):
        hide = next(r for r in field["reactions"] if r["when"].get("equals") == "flex")
        assert hide["then"] == {"set_visibility": False}
