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

_MODE_INPUTS = {
    "video": {},
    "refs": {"references": ["/media/ref.png"]},
    "extend": {"source_video": "/media/clip.mp4"},
    "remix": {"source_video": "/media/clip.mp4"},
    "talk": {"portrait": "/media/face.png", "voice": "/media/voice.wav"},
    "txt2video": {},
    "img2video": {"start_image": "/media/first.png"},
    "first_last": {"start_image": "/media/first.png", "end_image": "/media/last.png"},
    "motion_transfer": {
        "control_model": "/models/h3_control_int8.safetensors",
        "input_image": "/media/person.png",
        "input_video": "/media/dance.mp4",
    },
}

_RECIPE = {
    "speed_profile": "custom",
    "steps": 8,
    "sampler": "euler",
    "scheduler": "beta57",
    "video_sigma_shift": 12.0,
    "audio_sigma_shift": 3.0,
}


def _process(template, mode, form):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, mode, form_name=None, raw_form_data={**_MODELS, "prompt": "a dragon", **form},
        user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": [{"positive": "a dragon", "negative": ""}], "mode": mode, "form_data": dict(bound.values),
    })


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/video_minimax_h3")


def _sampling_fields():
    with open("content/presets/marketplace/MiniMax-H3/files/form/advanced_sampling.yml") as handle:
        tab = yaml.safe_load(handle)

    def walk(fields):
        for field in fields:
            if "children" in field and isinstance(field["children"], list):
                yield from walk(field["children"])
            elif field.get("name"):
                yield field

    return {field["name"]: field for field in walk(tab["fields"])}


@pytest.mark.parametrize("mode", sorted(_MODE_INPUTS))
def test_custom_eight_step_beta57_recipe_renders_into_the_generator_exactly(h3_template, mode):
    pipes = _process(h3_template, mode, {**_MODE_INPUTS[mode], **_RECIPE})
    config = _generator(pipes)["config"]
    assert config["steps"] == 8
    assert config["sampler"] == "euler"
    assert config["scheduler"] == "beta57"
    assert config["video_sigma_shift"] == 12.0
    assert config["audio_sigma_shift"] == 3.0
    assert config["manual_sigmas"] == ""
    assert config["manual_audio_sigmas"] == ""


@pytest.mark.parametrize("mode", sorted(_MODE_INPUTS))
def test_the_recipe_forces_no_lora(h3_template, mode):
    pipes = _process(h3_template, mode, {**_MODE_INPUTS[mode], **_RECIPE})
    assert _generator(pipes)["config"].get("runtime_loras", []) == []
    for pipe in pipes:
        if pipe["name"].startswith("model_loader/"):
            assert pipe["config"].get("loras", []) == [], pipe["name"]


@pytest.mark.parametrize("mode", sorted(_MODE_INPUTS))
def test_non_default_shifts_reach_the_generator(h3_template, mode):
    pipes = _process(h3_template, mode, {
        **_MODE_INPUTS[mode], **_RECIPE, "video_sigma_shift": 6.5, "audio_sigma_shift": 2.0,
    })
    config = _generator(pipes)["config"]
    assert (config["video_sigma_shift"], config["audio_sigma_shift"]) == (6.5, 2.0)


def test_the_custom_profile_pins_no_sampler_schedule_or_shift(h3_template):
    custom = h3_template.speed_profiles["custom"]
    for key in ("sampler", "schedule", "shift"):
        assert custom.get(key) is None, key


def test_the_shift_fields_default_to_the_released_twelve_and_three(h3_template):
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    fields = _sampling_fields()
    spec = {s.name: s for s in GeneratorMinimaxH3Pipe.configuration()}
    assert fields["video_sigma_shift"]["default"] == spec["video_sigma_shift"].default == 12.0
    assert fields["audio_sigma_shift"]["default"] == spec["audio_sigma_shift"].default == 3.0
    for name in ("video_sigma_shift", "audio_sigma_shift"):
        assert fields[name]["audience"] == "advanced"
        assert fields[name]["configuration"]["min"] >= spec[name].min_value
        assert fields[name]["configuration"]["max"] <= spec[name].max_value
    config = _generator(_process(h3_template, "video", {}))["config"]
    assert (config["video_sigma_shift"], config["audio_sigma_shift"]) == (12.0, 3.0)


def test_the_recipe_passes_pipe_validation(h3_template):
    from src.pipelines.pipes.generator.video_minimax_h3.main import validate_minimax_h3_config

    config = _generator(_process(h3_template, "video", _RECIPE))["config"]
    validate_minimax_h3_config(config, pipe_id="generator/video_minimax_h3")
