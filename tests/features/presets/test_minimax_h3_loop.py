from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets.mode_labels import MODE_NAMES_FILE
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.generator.video_minimax_h3.loop import max_loop_seconds, plan_loop
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe
from src.platform.templating.processor import TemplateProcessor

_FORM = {
    "model": "/models/minimax_h3_fl2va.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
}


def _pipes(template, form=None, prompt="the reeds sway"):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, "loop", form_name=None, raw_form_data={**_FORM, **(form or {})}, user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": [{"positive": prompt, "negative": ""}], "mode": "loop", "form_data": dict(bound.values),
    })


def _pipe(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id or p["name"] == pipe_id)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _document(pipes):
    return _pipe(pipes, "prompt_encoder")["config"]["p_prompt"]["output"]


def test_the_loop_mode_is_listed_and_named_for_a_person(h3_template):
    assert "loop" in h3_template.modes
    assert '"loop"' in MODE_NAMES_FILE.read_text(encoding="utf-8")
    assert "Perfect loop mode" in h3_template.llm["modes"]["loop"]["guide"]


def test_defaults_make_a_silent_subtle_six_second_loop_shaped_like_the_picture(h3_template):
    pipes = _pipes(h3_template)
    generator = _pipe(pipes, "generator")["config"]

    assert generator["loop"] is True
    assert generator["frames"] == 144
    assert generator["audio_source"] == "none"
    assert generator["resolution"] == "auto"
    assert generator["keyframe_anchors"] == []
    assert _pipe(pipes, "start_picture")["enabled"] is False
    document = _document(pipes)
    assert document.startswith("integrated_multimodal_description: [Shot 1] Filmed from a fixed camera")
    assert "Only slight, slow movement: the reeds sway" in document
    assert document.endswith("overall_soundscape: N/A\nnon_diegetic_music: N/A")
    GeneratorMinimaxH3Pipe.validate_config({**GeneratorMinimaxH3Pipe.get_default_config(), **generator})


def test_a_start_picture_is_the_first_frame_and_the_scene_in_the_prompt(h3_template):
    pipes = _pipes(h3_template, {"start_picture": "/media/cabin.png"})
    generator = _pipe(pipes, "generator")

    assert _pipe(pipes, "start_picture")["enabled"] is True
    assert ("image", "start_picture", "image") in _wired(generator)
    assert ("image", "start_picture", "image") in _wired(_pipe(pipes, "prompt_encoder"))
    assert generator["config"]["keyframe_anchors"] == ["first"]
    assert "[Shot 1] The scene of <Picture 1>, filmed from a fixed camera" in _document(pipes)


def test_sound_lively_movement_screen_and_length_reach_the_generator(h3_template):
    pipes = _pipes(h3_template, {"sound": True, "movement": "lively", "screen": "768x1344", "loop_seconds": 8.5})
    generator = _pipe(pipes, "generator")["config"]

    assert generator["audio_source"] == "generate"
    assert generator["resolution"] == "768x1344"
    assert generator["frames"] == 204
    assert "Steady, clearly visible movement: the reeds sway" in _document(pipes)
    assert "overall_soundscape: Quiet ambient sound" in _document(pipes)


@pytest.mark.parametrize("sound", [False, True])
def test_the_length_slider_never_asks_for_more_than_two_windows_allow(h3_template, sound):
    fields = {field.name: field for field in _walk(h3_template.modes["loop"].forms[0].fields)}
    top = fields["loop_seconds"].configuration["max"]

    assert plan_loop(round(top * 24), with_sound=sound).seconds <= max_loop_seconds(with_sound=sound)
    assert top + 1 > max_loop_seconds(with_sound=False)


def _walk(fields):
    for field in fields:
        yield field
        yield from _walk(field.children or [])


@pytest.mark.parametrize("form, flags", [
    ({}, {"picture": False, "lively": False, "sound": False}),
    ({"start_picture": "/media/cabin.png", "movement": "lively", "sound": True},
     {"picture": True, "lively": True, "sound": True}),
])
def test_the_gpu_check_writes_the_same_document_as_the_mode(h3_template, form, flags):
    from tests.manual.h3_perfect_loop_check import loop_document

    assert loop_document("the reeds sway", **flags) == _document(_pipes(h3_template, form))


def test_the_gpu_check_passes_a_periodic_clip_and_fails_a_jump_at_the_seam():
    import numpy as np

    from tests.manual.h3_perfect_loop_check import seam_metric

    phase = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    smooth = (128 + 40 * np.sin(phase)).astype(np.uint8)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)
    report = seam_metric(smooth)
    assert report["pass"] and report["frames"] == 48

    ramp = (40 + 2 * np.arange(48)).astype(np.uint8)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)
    report = seam_metric(ramp)
    assert not report["pass"]
    assert report["seam_step"] == pytest.approx(94) and report["largest_step"] == pytest.approx(2)


def test_a_prompt_that_is_already_a_full_document_is_not_wrapped_in_a_second_one(h3_template):
    written = (
        "integrated_multimodal_description: [Shot 1] A woman on a cliff, her hair streaming in the wind.\n\n"
        "overall_soundscape: Distant surf.\n\nnon_diegetic_music: None."
    )
    document = _document(_pipes(h3_template, {"start_picture": "/media/cabin.png"}, prompt=written))

    assert document == written
    assert document.count("integrated_multimodal_description:") == 1
