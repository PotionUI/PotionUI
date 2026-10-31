from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan
from src.platform.templating.processor import TemplateProcessor

_FORM = {
    "model": "/models/minimax_h3.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
    "resolution": "832x480",
}
_FPS = 24
_OVERLAP = 17


def _process(template, form=None, prompt="a long walk"):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, "long", form_name=None, raw_form_data={**_FORM, **(form or {})}, user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": [{"positive": prompt, "negative": ""}], "mode": "long", "form_data": dict(bound.values),
    })


def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


def _document(pipes):
    return _pipe(pipes, "generator")["config"]["document"]


def _plan(pipes):
    return build_director_plan(_document(pipes), default_seed=7)


@pytest.mark.parametrize("total, piece", [(10, 10), (12, 14), (30, 10), (45, 5), (60, 14), (120, 14), (61, 7)])
def test_the_chain_reaches_the_requested_length_without_a_spare_part(h3_template, total, piece):
    plan = _plan(_process(h3_template, {"total_seconds": total, "piece_seconds": piece}))
    target = total * _FPS
    assert plan.total_frames >= target
    assert plan.total_frames - target < 17 + 5
    without_last = plan.total_frames - plan.windows[-1].emitted_frames
    assert without_last < target or len(plan.windows) == 1


@pytest.mark.parametrize("piece", [5, 8, 10, 14])
def test_no_part_exceeds_the_per_window_ceiling(h3_template, piece):
    plan = _plan(_process(h3_template, {"total_seconds": 90, "piece_seconds": piece}))
    assert all(window.frames / _FPS <= 15 for window in plan.windows)


def test_every_part_after_the_first_continues_the_one_before(h3_template):
    plan = _plan(_process(h3_template, {"total_seconds": 40}))
    assert len(plan.windows) > 2
    assert [w.continues_previous for w in plan.windows] == [False] + [True] * (len(plan.windows) - 1)
    assert all(w.overlap_frames == _OVERLAP for w in plan.windows[1:])
    assert plan.stitch is True


def test_a_text_start_opens_on_a_text_part(h3_template):
    plan = _plan(_process(h3_template))
    assert plan.windows[0].sub_type == "t2v"
    assert all(w.keyframes == () for w in plan.windows)
    assert _pipe(_process(h3_template), "director_media")["enabled"] is False


def test_a_start_picture_pins_the_first_frame_of_the_first_part(h3_template):
    pipes = _process(h3_template, {"start_image": "/media/start.png"})
    plan = _plan(pipes)
    assert plan.windows[0].sub_type == "i2v"
    assert [(kf.image_index, kf.frame) for kf in plan.windows[0].keyframes] == [(0, 0)]
    assert all(w.keyframes == () for w in plan.windows[1:])
    loader = _pipe(pipes, "director_media")
    assert loader["enabled"] is True
    assert loader["config"]["media"] == [{"type": "image", "path": "/media/start.png"}]


def test_the_encoder_and_the_plan_agree_on_the_number_of_parts(h3_template):
    pipes = _process(h3_template, {"total_seconds": 55, "piece_seconds": 9})
    encoder = _pipe(pipes, "prompt_encoder")["config"]
    assert encoder["quantity"] == len(encoder["pairs"]) == len(_plan(pipes).windows)


def test_one_prompt_serves_every_part(h3_template):
    pipes = _process(h3_template, {"total_seconds": 30}, prompt="a fox in snow")
    assert {pair["positive"] for pair in _pipe(pipes, "prompt_encoder")["config"]["pairs"]} == {"a fox in snow"}
    assert {w.prompt for w in _plan(pipes).windows} == {"a fox in snow"}


def test_a_prompt_per_part_overrides_in_order_and_falls_back_to_the_main_prompt(h3_template):
    pipes = _process(
        h3_template, {"total_seconds": 40, "piece_prompts": "the fox wakes\n\nthe fox runs"}, prompt="a fox in snow",
    )
    expected = ["the fox wakes", "a fox in snow", "the fox runs"]
    prompts = [w.prompt for w in _plan(pipes).windows]
    assert prompts[:3] == expected
    assert set(prompts[3:]) <= {"a fox in snow"}
    assert [p["positive"] for p in _pipe(pipes, "prompt_encoder")["config"]["pairs"]] == prompts


def test_the_finished_video_is_trimmed_to_the_requested_length(h3_template):
    pipes = _process(h3_template, {"total_seconds": 33})
    trim = _pipe(pipes, "trim")
    assert trim["config"]["keep"] == "head"
    assert trim["config"]["seconds"] == 33
    assert ("video", "generator", "video") in [(e["name"], e["provider"], e["output_var"]) for e in trim["input"]]
    gallery = _pipe(pipes, "gallery")
    assert ("video", "trim", "video") in [(e["name"], e["provider"], e["output_var"]) for e in gallery["input"]]


def test_a_soundtrack_is_muxed_over_the_whole_chain(h3_template):
    pipes = _process(h3_template, {"soundtrack": "/media/score.wav"})
    assert _plan(pipes).mux_audio_path == "/media/score.wav"
    assert _plan(_process(h3_template)).mux_audio_path is None


def test_the_colour_hold_reaches_the_generator(h3_template):
    assert _pipe(_process(h3_template, {"drift_correction": 0.8}), "generator")["config"]["drift_correction"] == 0.8


def test_the_chain_is_one_generation_not_a_batch(h3_template):
    pipes = _process(h3_template, {"quantity": 3})
    assert _pipe(pipes, "generator")["config"]["quantity"] == 1
    assert _pipe(pipes, "seed_generator")["config"]["quantity"] == 1


def test_flex_runs_the_six_knot_recipe_across_the_chain(h3_template):
    pipes = _process(h3_template, {
        "speed_profile": "flex", "flex_adapter": "/models/pika_h3_flex_fl2va_v1_bf16.safetensors",
        "loras": [{"model": "/models/style.safetensors", "strength": 0.8}],
    })
    cfg = _pipe(pipes, "generator")["config"]
    assert cfg["sampler"] == "lcm"
    assert cfg["manual_sigmas"] == "1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0"
    assert [e["file_path"] for e in cfg["runtime_loras"]] == ["/models/pika_h3_flex_fl2va_v1_bf16.safetensors"]
    assert list(_pipe(pipes, "model_loader")["config"]["loras"]) == []


def test_the_long_mode_is_declared(h3_template):
    assert "long" in h3_template.modes
