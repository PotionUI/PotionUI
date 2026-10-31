from __future__ import annotations

import math
import tempfile
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
import yaml
from PIL import Image

from src.features.forms.binding import bind_form
from src.features.presets.processor import PresetProcessor
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.control_video.main import ControlVideoPipe
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import source_canvas_size
from src.pipelines.pipes.generator.video_minimax_h3.geometry import CANVAS_MAX_PIXELS, MAX_DURATION_S
from src.platform.templating.processor import TemplateProcessor
from tests.fixtures.pose_skeletons import skeleton
from tests.platform.media_tools.media_factory import make_video

ROOT = Path("content/presets/marketplace/MiniMax-H3")
INT8 = "minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors"
BF16 = "minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors"

_FORM = {
    "model": "/models/minimax_h3_fl2va.safetensors",
    "text_encoder": "/models/qwen3_vl.safetensors",
    "video_vae": "/models/h3_video_vae.safetensors",
    "audio_vae": "/models/h3_audio_vae.safetensors",
    "control_model": "/models/h3_control_int8.safetensors",
    "input_image": "/media/person.png",
    "input_video": "/media/dance.mp4",
}


def _pipes(template, form=None, prompt=""):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    bound = bind_form(
        template, "motion_transfer", form_name=None, raw_form_data={**_FORM, **(form or {})},
        user_id=None, storage_dir=None,
    )
    return processor.process(template, {
        "prompts": [{"positive": prompt, "negative": ""}], "mode": "motion_transfer",
        "form_data": dict(bound.values),
    })


def _by_id(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _fields(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))["fields"]


def _named(fields, name):
    for field in fields:
        if field.get("name") == name:
            return field
        found = _named(field["children"], name) if isinstance(field.get("children"), list) else None
        if found:
            return found
    return None


def test_the_guide_feeds_the_generator_control_video(h3_template):
    pipes = _pipes(h3_template)
    generator = _by_id(pipes, "generator")
    assert ("control_video", "pose_guide", "video") in _wired(generator)
    guide = _by_id(pipes, "pose_guide")
    assert ("video", "media_loader", "video") in _wired(guide)
    assert ("image", "media_image", "image") in _wired(guide)
    assert guide["config"]["type"] == "pose"
    assert guide["config"]["match_picture"] is True


def test_the_picture_is_the_first_frame_keyframe_by_default(h3_template):
    pipes = _pipes(h3_template)
    generator = _by_id(pipes, "generator")
    assert ("image", "media_image", "image") in _wired(generator)
    assert not any(name == "reference_images" for name, _, _ in _wired(generator))
    assert generator["config"]["mode"] == "video"
    assert generator["config"]["keyframe_anchors"] == ["first"]
    assert generator["config"]["references"] == []
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")
    assert ("image", "media_image", "image") in _wired(encoder)
    assert "<Picture 1>" in encoder["config"]["p_prompt"]["output"]


def test_one_preset_var_flips_the_identity_path_to_a_reference(h3_template, monkeypatch):
    monkeypatch.setitem(h3_template.vars, "motion_identity", "reference")
    pipes = _pipes(h3_template)
    generator = _by_id(pipes, "generator")
    assert ("reference_images", "media_image", "image") in _wired(generator)
    assert not any(name == "image" for name, _, _ in _wired(generator))
    assert generator["config"]["mode"] == "references"
    assert generator["config"]["keyframe_anchors"] == []
    assert generator["config"]["references"] == [_FORM["input_image"]]
    assert ("control_video", "pose_guide", "video") in _wired(generator)
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")
    assert ("reference_image", "media_image", "image") in _wired(encoder)
    assert "subject_definitions: <Subject 1> is the person in <Picture 1>" in encoder["config"]["p_prompt"]["output"]


def test_the_control_model_field_reaches_the_loader_control_module(h3_template):
    pipes = _pipes(h3_template, {"control_model": "/models/other_patch.safetensors"})
    loader = next(p for p in pipes if p["name"] == "model_loader/minimax_h3")
    assert loader["config"]["control_module"] == {
        "file_path": "/models/other_patch.safetensors", "name": "/models/other_patch.safetensors",
    }
    assert ("model", "/models/other_patch.safetensors") in [tuple(p) for p in _by_id(pipes, "param_emitter")["config"]["parameters"] if p]


def test_the_control_model_field_recommends_both_patches_with_checksums():
    field = _named(_fields("files/form/models_motion_transfer.yml"), "control_model")
    assert field["required"] is True
    assert field["configuration"]["model_type"] == "model_patch"
    recommended = {r["name"]: r for r in field["configuration"]["recommendations"]}
    assert list(recommended) == [INT8, BF16]
    assert recommended[INT8]["sha256"] == "890af58bb350f0c2f6c409b1c67ea8f4196654297d037769b72de1e6f504c612"
    assert recommended[BF16]["sha256"] == "cd48bdc11b1f1a3b934746c898d25123754b335c13d37d5c421f8373079282b4"
    for entry in recommended.values():
        assert entry["link"].endswith(f"/model_patches/{entry['name']}")
        assert entry["size"] and entry["description"]
    requirement = yaml.safe_load((ROOT / "modes/motion_transfer/form.yml").read_text(encoding="utf-8"))["requirements"][0]
    assert requirement["field"] == "control_model"


def test_the_models_section_keeps_the_fl2va_pickers_and_the_shared_fields_in_step():
    shared = {f["name"]: f for f in _fields("files/form/models_fl2va.yml") if f["name"] != "flex_adapter"}
    mode = {f["name"]: f for f in _fields("files/form/models_motion_transfer.yml")}
    assert list(mode) == [*shared, "control_model", "preview_decoder"]
    for name, field in shared.items():
        assert mode[name] == field


@pytest.mark.parametrize("sound,source", [("generate", "generate"), ("keep", "file")])
def test_the_sound_options(h3_template, sound, source):
    generator = _by_id(_pipes(h3_template, {"sound": sound}), "generator")
    assert generator["config"]["audio_source"] == source
    assert ("audio", "media_loader", "video") in _wired(generator)


def test_the_sound_field_offers_generate_and_keep_only():
    field = _named(_fields("modes/motion_transfer/tabs/generation.yml"), "sound")
    assert [o["value"] for o in field["configuration"]["options"]] == ["generate", "keep"]


def test_sound_defaults_to_generate(h3_template):
    assert _named(_fields("modes/motion_transfer/tabs/generation.yml"), "sound")["default"] == "generate"
    assert _by_id(_pipes(h3_template), "generator")["config"]["audio_source"] == "generate"



def test_the_preview_decoder_reaches_the_generator(h3_template):
    off = _by_id(_pipes(h3_template), "generator")["config"]["preview_decoder"]
    assert not off
    on = _by_id(_pipes(h3_template, {"preview_decoder": "/models/taeh3.safetensors"}), "generator")
    assert on["config"]["preview_decoder"] == "/models/taeh3.safetensors"


def test_the_preview_decoder_field_follows_the_live_preview_toggle():
    field = _named(_fields("files/form/models_motion_transfer.yml"), "preview_decoder")
    video = _named(_fields("modes/video/tabs/generation.yml"), "preview_decoder")
    assert field == video
    assert _named(_fields("modes/motion_transfer/tabs/advanced.yml"), "preview")["default"] is True


def test_the_default_profile_is_quality_and_steps_follow_the_profile(h3_template):
    field = _named(_fields("modes/motion_transfer/tabs/generation.yml"), "speed_profile")
    assert field["default"] == "quality"
    assert _by_id(_pipes(h3_template, {"steps": 24}), "generator")["config"]["steps"] == 24


def test_control_strength_ease_in_and_save_guide_reach_the_pipes(h3_template):
    default = _pipes(h3_template)
    assert _by_id(default, "generator")["config"]["control_context_scale"] == 1.0
    assert _by_id(default, "pose_guide")["config"]["ease_in"] == 0.5
    assert _by_id(default, "pose_guide")["config"]["save_guide"] is False
    tuned = _pipes(h3_template, {"control_strength": 0.8, "pose_ease_in": 1.2, "save_guide": True})
    assert _by_id(tuned, "generator")["config"]["control_context_scale"] == 0.8
    assert _by_id(tuned, "pose_guide")["config"]["ease_in"] == 1.2
    assert _by_id(tuned, "pose_guide")["config"]["save_guide"] is True


def test_the_length_follows_the_guide_and_the_guide_is_capped_at_the_generators_limit(h3_template):
    pipes = _pipes(h3_template)
    config = _by_id(pipes, "generator")["config"]
    assert config["control_length_from_video"] is True
    assert config["resolution"] == "auto"
    assert config["frames"] == int(MAX_DURATION_S * 24)
    assert _by_id(pipes, "pose_guide")["config"]["max_seconds"] == MAX_DURATION_S
    assert "max_frames" not in _by_id(pipes, "pose_guide")["config"]
    assert _by_id(pipes, "pose_guide")["config"]["max_pixels"] == CANVAS_MAX_PIXELS
    assert _by_id(pipes, "pose_guide")["config"]["size_multiple"] == 32


def test_the_prompt_is_optional_and_falls_back_to_a_dancing_caption(h3_template):
    assert "motion_transfer" in h3_template.vars["optional_prompt_modes"]
    caption = next(p for p in _pipes(h3_template) if p["name"] == "prompt_encoder")["config"]["p_prompt"]["output"]
    assert "dancing" in caption and "person from the picture" in caption
    typed = next(p for p in _pipes(h3_template, prompt="a knight waves") if p["name"] == "prompt_encoder")
    assert "a knight waves" in typed["config"]["p_prompt"]["output"]
    assert "dancing" not in typed["config"]["p_prompt"]["output"]


def test_the_mode_does_not_touch_the_video_director_or_the_gallery_audio(h3_template):
    pipes = _pipes(h3_template)
    config = _by_id(pipes, "generator")["config"]
    assert "document" not in config
    gallery = next(p for p in pipes if p["name"] == "gallery")
    assert _wired(gallery) == {("video", "generator", "video"), ("seed", "seed_generator", "seed")}


class _FakeEstimator:
    def detect(self, rgb):
        height, width = rgb.shape[:2]
        return np.array([[0, 0, width, height, 0.9]], dtype=np.float32)

    def estimate(self, rgb, box):
        height, width = rgb.shape[:2]
        return skeleton(neck=(width / 2, height * 0.25), size=height / 260.0)


def _render_guide(config, video, picture, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    values = ControlVideoPipe.get_default_config()
    values.update({k: v for k, v in config.items() if k in values})
    pipe = ControlVideoPipe(values)
    pipe._load_estimator = lambda _input: _FakeEstimator()
    return pipe.process(PipeInput(input={"video": [video], "image": [picture]}), lambda _o: None, None).output["video"][0]


def _guide_size(config, video, picture, tmp_path, monkeypatch):
    guide = _render_guide(config, video, picture, tmp_path, monkeypatch)
    capture = cv2.VideoCapture(str(guide))
    ok, frame = capture.read()
    capture.release()
    assert ok
    return frame.shape[1], frame.shape[0]


@pytest.mark.parametrize(
    "picture_size,video_size",
    [((1080, 1920), (320, 192)), ((1920, 1080), (192, 320)), ((1500, 1000), (192, 192))],
)
def test_the_canvas_follows_the_picture_never_the_video(h3_template, picture_size, video_size, tmp_path, monkeypatch):
    guide_config = _by_id(_pipes(h3_template), "pose_guide")["config"]
    video = str(make_video(tmp_path / "dance.mp4", seconds=0.5, size=video_size, fps=10, audio=False))
    picture = Image.new("RGB", picture_size, (120, 120, 120))
    width, height = _guide_size(guide_config, video, picture, tmp_path, monkeypatch)

    assert width % 32 == 0 and height % 32 == 0
    assert width * height <= guide_config["max_pixels"]
    canvas_height, canvas_width = source_canvas_size(width, height)
    assert canvas_height % 32 == 0 and canvas_width % 32 == 0
    assert (canvas_width > canvas_height) == (picture_size[0] > picture_size[1])
    stretch = abs(math.log((canvas_width / canvas_height) / (picture_size[0] / picture_size[1])))
    assert stretch < 32 / min(canvas_width, canvas_height)
    assert abs(math.log((canvas_width / canvas_height) / (video_size[0] / video_size[1]))) > 0.2 or video_size == picture_size
