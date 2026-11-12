from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError, bind_form
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


def test_defaults_make_a_silent_subtle_four_second_desktop_loop(h3_template):
    pipes = _pipes(h3_template)
    generator = _pipe(pipes, "generator")["config"]

    assert generator["loop"] is True
    assert generator["frames"] == 96
    assert generator["audio_source"] == "none"
    assert generator["resolution"] == "1344x768"
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


def test_sound_lively_movement_resolution_and_length_reach_the_generator(h3_template):
    pipes = _pipes(h3_template, {"sound": True, "movement": "lively", "resolution": "768x1344", "loop_seconds": 8})
    generator = _pipe(pipes, "generator")["config"]

    assert generator["audio_source"] == "generate"
    assert generator["resolution"] == "768x1344"
    assert generator["frames"] == 192
    assert "Steady, clearly visible movement: the reeds sway" in _document(pipes)
    assert "overall_soundscape: Quiet ambient sound" in _document(pipes)


def test_the_length_is_a_pick_of_whole_seconds_each_a_loop_the_mode_can_make(h3_template):
    fields = {field.name: field for field in _walk(h3_template.modes["loop"].forms[0].fields)}
    length = fields["loop_seconds"]
    offered = [option["value"] for option in length.configuration["options"]]

    assert length.type == "select"
    assert offered == [2, 3, 4, 5, 6, 8, 10]
    for seconds in offered:
        for sound in (False, True):
            plan = plan_loop(round(seconds * 24), with_sound=sound)
            assert plan.seconds <= max_loop_seconds(with_sound=sound)
            assert abs(plan.seconds - seconds) <= (1.1 if sound else 0.36)


@pytest.mark.parametrize("seconds", [4.5, 1, 26])
def test_a_length_that_is_not_offered_is_refused_at_the_server(h3_template, seconds):
    with pytest.raises(FormBindingError, match="loop_seconds"):
        _pipes(h3_template, {"loop_seconds": seconds})


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

    triangle = 100 + 2 * np.minimum(np.arange(48), 48 - np.arange(48))
    smooth = triangle.astype(np.uint8)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)
    report = seam_metric(smooth)
    assert report["pass"] and report["frames"] == 48

    ramp = (40 + 2 * np.arange(48)).astype(np.uint8)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)
    report = seam_metric(ramp)
    assert not report["pass"]
    assert report["seam_step"] == pytest.approx(94) and report["largest_step"] == pytest.approx(2)
    assert report["jumps"][0]["at_wrap"] and report["jumps"][0]["back_to"] == 0


def _clip(values):
    import numpy as np

    return np.asarray(values, dtype=np.uint8)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)


def test_the_gpu_check_finds_a_reset_inside_the_period_and_names_the_frame_it_jumps_back_to():
    import numpy as np

    from tests.manual.h3_perfect_loop_check import seam_metric

    values = 40 + 2.5 * np.arange(60, dtype=float)
    values[45:] = values[20:35]
    report = seam_metric(_clip(values))

    assert not report["pass"]
    assert [jump["frames"] for jump in report["jumps"]][0] == [44, 45]
    assert report["jumps"][0]["back_to"] == 20


def test_the_gpu_check_finds_motion_that_stops_and_restarts_but_not_the_vae_cadence():
    import numpy as np

    from tests.manual.h3_perfect_loop_check import seam_metric

    steps = np.where(np.arange(60) % 4 == 0, 6.0, 2.0)
    signs = np.where(np.arange(60) % 30 < 15, 1, -1)
    cadence = np.cumsum(steps * signs) + 60
    assert seam_metric(_clip(cadence - cadence.min() + 40))["pass"]

    stopping = np.cumsum(np.where((np.arange(60) >= 28) & (np.arange(60) < 33), 0.0, 2.0) * signs) + 60
    report = seam_metric(_clip(stopping - stopping.min() + 40))
    assert not report["pass"] and report["stops"]


def test_the_gpu_check_finds_a_slow_zoom():
    import numpy as np
    from PIL import Image

    from tests.manual.h3_perfect_loop_check import zoom_drift

    base = Image.fromarray((np.random.default_rng(1).random((168, 96)) * 255).astype(np.uint8)).resize((192, 336))
    def zoom(scale):
        grown = base.resize((round(192 * scale), round(336 * scale)))
        left, top = (grown.size[0] - 192) // 2, (grown.size[1] - 336) // 2
        return np.asarray(grown.crop((left, top, left + 192, top + 336)).convert("RGB"))

    still = np.stack([zoom(1.0)] * 48)
    assert zoom_drift(still)["zoom_ok"]
    drifting = np.stack([zoom(1.0 + 0.03 * index / 47) for index in range(48)])
    report = zoom_drift(drifting)
    assert not report["zoom_ok"] and report["worst_zoom"] >= 1.02


def test_a_prompt_that_is_already_a_full_document_is_not_wrapped_in_a_second_one(h3_template):
    written = (
        "integrated_multimodal_description: [Shot 1] A woman on a cliff, her hair streaming in the wind.\n\n"
        "overall_soundscape: Distant surf.\n\nnon_diegetic_music: None."
    )
    document = _document(_pipes(h3_template, {"start_picture": "/media/cabin.png"}, prompt=written))

    assert document == written
    assert document.count("integrated_multimodal_description:") == 1


def test_the_gpu_check_measures_a_picture_that_arrives_early_and_holds_until_the_wrap():
    import numpy as np

    from tests.manual.h3_perfect_loop_check import seam_metric

    travel = 100 + 2 * np.minimum(np.arange(74), 74 - np.arange(74))
    hold = np.full(28, travel[-1])
    hold[[3, 11, 16, 20]] += 1
    report = seam_metric(_clip(np.concatenate([travel, hold])))

    assert not report["pass"]
    assert report["stops"] == [{"start": 73, "frames": 29}]
    assert not report["jumps"]


def test_the_gpu_check_reports_motion_either_side_of_the_wrap_and_fails_an_ease_into_the_seam():
    import numpy as np

    from tests.manual.h3_perfect_loop_check import seam_metric

    steady = np.full(80, 4.0)
    signs = np.where(np.arange(80) < 40, 1, -1)
    report = seam_metric(_clip(40 + np.cumsum(steady * signs) - (steady * signs)[0]))
    assert report["pass"]
    assert report["before_seam_over_median"] == pytest.approx(1.0, abs=0.15)
    assert report["after_seam_over_median"] == pytest.approx(1.0, abs=0.15)

    easing = np.where(np.arange(80) < 68, 4.0, 1.0)
    path = np.cumsum(easing * signs)
    report = seam_metric(_clip(40 + path - path.min()))
    assert report["before_seam_over_median"] < 0.5
    assert not report["seam_motion_ok"] and not report["pass"]


def _subtle_clip(*, wrap_offset, scale=1.0, chunk_bump=4.0):
    import numpy as np

    values = 100.0 + np.arange(51) % 2
    values[[17, 34]] += chunk_bump
    values[0] += wrap_offset
    return (values * scale)[:, None, None, None].repeat(4, 1).repeat(4, 2).repeat(3, 3)


def test_the_gpu_check_passes_a_near_still_loop_whose_only_bumps_are_the_vae_chunk_boundaries():
    from tests.manual.h3_perfect_loop_check import seam_metric

    report = seam_metric(_subtle_clip(wrap_offset=4.0))

    assert report["jumps"] == [] and report["pass"]


def test_the_gpu_check_still_fails_a_near_still_loop_that_jumps_at_the_wrap():
    from tests.manual.h3_perfect_loop_check import seam_metric

    report = seam_metric(_subtle_clip(wrap_offset=12.0))

    assert not report["pass"]
    wrap = [jump for jump in report["jumps"] if jump["at_wrap"]]
    assert [jump["frames"] for jump in wrap] == [[50, 0]]
    assert wrap[0]["step"] == pytest.approx(12.0)


def test_the_gpu_check_ignores_a_wrap_step_too_small_to_see_whatever_its_ratio():
    from tests.manual.h3_perfect_loop_check import seam_metric

    tiny = seam_metric(_subtle_clip(wrap_offset=9.0, scale=0.04, chunk_bump=0.0))
    assert tiny["seam_over_median"] > 5 and tiny["seam_step"] < 0.5
    assert tiny["jumps"] == []

    visible = seam_metric(_subtle_clip(wrap_offset=9.0, scale=0.1, chunk_bump=0.0))
    assert visible["seam_step"] > 0.5 and visible["jumps"]


def _drifting_clip(offsets):
    import numpy as np

    x = np.arange(192, dtype=np.float64)[None, :]
    y = np.arange(128, dtype=np.float64)[:, None]
    frames = []
    for shift in offsets:
        phase = 2 * np.pi * (x + shift) / 192
        value = (128 + 40 * np.sin(phase) * (0.7 + 0.3 * np.sin(2 * np.pi * y / 41))
                 + 25 * np.sin(2 * phase + 1.3) * (0.6 + 0.4 * np.cos(2 * np.pi * y / 29))
                 + 15 * np.sin(3 * phase + 0.4) * np.sin(2 * np.pi * y / 53 + 0.8))
        frames.append(np.clip(value, 0, 255).astype(np.uint8)[:, :, None].repeat(3, 2))
    return np.stack(frames)


def _rewinding_offsets(rewind_steps):
    import numpy as np

    steps = np.where(np.isin(np.arange(48), rewind_steps), -30.0, 2.0)
    return np.concatenate([[0.0], np.cumsum(steps)[:-1]])


def test_the_gpu_check_passes_a_loop_that_flows_one_way_all_round():
    pytest.importorskip("cv2")
    import numpy as np

    from tests.manual.h3_perfect_loop_check import rewinds, seam_metric

    clip = _drifting_clip(4.0 * np.arange(48))
    report = rewinds(clip)

    assert report["rewind_check"] == "flow" and report["rewinds"] == [] and report["rewind_ok"]
    assert seam_metric(clip)["pass"]


def test_the_gpu_check_passes_a_sway_whose_motion_turns_back_smoothly():
    pytest.importorskip("cv2")
    import numpy as np

    from tests.manual.h3_perfect_loop_check import rewinds

    assert rewinds(_drifting_clip(20 * np.sin(2 * np.pi * np.arange(48) / 48)))["rewinds"] == []


def test_the_gpu_check_fails_a_drift_that_snaps_back_inside_the_clip():
    pytest.importorskip("cv2")
    from tests.manual.h3_perfect_loop_check import rewinds

    report = rewinds(_drifting_clip(_rewinding_offsets([30, 31, 32])))

    assert not report["rewind_ok"]
    assert sorted({rewind["step"] for rewind in report["rewinds"]}) == [30, 31, 32]
    assert min(rewind["speed_over_usual"] for rewind in report["rewinds"]) >= 8


def test_the_gpu_check_fails_a_drift_whose_velocity_reverses_across_the_wrap():
    pytest.importorskip("cv2")
    from tests.manual.h3_perfect_loop_check import rewinds

    report = rewinds(_drifting_clip(_rewinding_offsets([45, 46, 47])))

    assert not report["rewind_ok"]
    assert any(rewind["at_wrap"] for rewind in report["rewinds"])


def test_the_gpu_check_skips_the_rewind_rule_with_a_note_when_opencv_is_missing(monkeypatch):
    import builtins

    import numpy as np

    from tests.manual.h3_perfect_loop_check import rewinds

    real_import = builtins.__import__

    def no_cv2(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("no cv2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_cv2)
    report = rewinds(_drifting_clip(_rewinding_offsets([30, 31, 32])))

    assert report["rewind_ok"] and report["rewinds"] == []
    assert report["rewind_check"].startswith("skipped")


def test_the_gpu_check_does_not_call_a_fast_burst_along_the_drift_a_rewind():
    pytest.importorskip("cv2")
    import numpy as np

    from tests.manual.h3_perfect_loop_check import rewinds

    steps = np.where(np.isin(np.arange(48), [30, 31, 32]), 34.0, 2.0)
    report = rewinds(_drifting_clip(np.concatenate([[0.0], np.cumsum(steps)[:-1]])))

    assert report["rewinds"] == []
