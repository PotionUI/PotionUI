from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.generator.video_ltx.main import _fit_pixel_budget
from src.platform.runtime.pose.geometry import resolve_output_size
from src.platform.templating.processor import TemplateProcessor

UNION_LORA = "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"
REFERENCE_DOWNSCALE = 2
PRESETS = ["LTX-2.5", "LTX-2"]


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = {}
    for name in PRESETS:
        template = next((p for p in loader.presets if Path(p.path).as_posix().endswith(f"marketplace/{name}")), None)
        if template is None:
            pytest.fail(f"marketplace/{name} preset not present")
        found[name] = template
    return found


def _pipes(template, form_over=None):
    form_data = build_fixture_form_data(template, "motion_transfer")
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": "motion_transfer", "form_data": form_data})


def _by_id(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


@pytest.mark.parametrize("name", PRESETS)
def test_the_pose_guide_is_the_reference_clip_of_both_passes(templates, name):
    pipes = _pipes(templates[name])
    for stage in ("generator_stage1", "generator"):
        pipe = _by_id(pipes, stage)
        assert ("video", "pose_guide", "video") in _wired(pipe)
        placements = pipe["config"]["media_placements"]
        reference = next(p for p in placements if p["role"] == "reference")
        assert reference["source"] == "video" and reference["index"] == 0


@pytest.mark.parametrize("name", PRESETS)
def test_the_picture_is_the_first_frame_keyframe_and_the_retarget_target(templates, name):
    pipes = _pipes(templates[name])
    guide = _by_id(pipes, "pose_guide")
    assert ("image", "media_image", "image") in _wired(guide)
    assert ("video", "media_loader", "video") in _wired(guide)
    assert guide["config"]["type"] == "pose"
    assert guide["config"]["match_picture"] is True
    for stage in ("generator_stage1", "generator"):
        pipe = _by_id(pipes, stage)
        assert ("image", "media_image", "image") in _wired(pipe)
        keyframe = next(p for p in pipe["config"]["media_placements"] if p["role"] == "keyframe")
        assert keyframe["source"] == "image" and keyframe["frame"] == "first"
        assert keyframe["strength"] == 1.0


@pytest.mark.parametrize("name", PRESETS)
def test_the_sound_comes_from_the_driving_video_not_the_guide(templates, name):
    final = _by_id(_pipes(templates[name]), "generator")
    assert ("audio", "media_loader", "video") in _wired(final)
    assert final["config"]["audio_source"] == "file"


@pytest.mark.parametrize("name", PRESETS)
def test_the_union_lora_is_scoped_to_the_passes_and_never_baked_into_the_loader(templates, name):
    pipes = _pipes(templates[name])
    lora = "/GOLDEN/models/effect_lora.safetensors"
    loader = next(p for p in pipes if p["name"] == "model_loader/ltx")
    assert loader["config"]["loras"] == []
    stage1 = _by_id(pipes, "generator_stage1")
    assert [entry["file_path"] for entry in stage1["config"]["scoped_loras"]] == [lora]
    stage2 = _by_id(pipes, "generator")
    keeps_reference = stage2["config"]["refine_reference"]
    assert [entry["file_path"] for entry in stage2["config"].get("scoped_loras", [])] == ([lora] if keeps_reference else [])
    assert "scoped_loras" not in _by_id(pipes, "latent_upscaler")["config"]


@pytest.mark.parametrize("name", PRESETS)
def test_the_refine_pass_keeps_the_reference_by_default(templates, name):
    assert _by_id(_pipes(templates[name]), "generator")["config"]["refine_reference"] is True


@pytest.mark.parametrize("name", PRESETS)
def test_follow_strength_drives_the_lora_weight(templates, name):
    pipes = _pipes(templates[name], {"effect_strength": 0.8})
    assert _by_id(pipes, "generator_stage1")["config"]["scoped_loras"][0]["weight"] == 0.8


@pytest.mark.parametrize("name", PRESETS)
def test_ease_in_and_save_guide_reach_the_pose_guide(templates, name):
    default = _by_id(_pipes(templates[name]), "pose_guide")["config"]
    assert default["ease_in"] == 0.5
    assert default["save_guide"] is False
    tuned = _by_id(_pipes(templates[name], {"pose_ease_in": 1.2, "save_guide": True}), "pose_guide")["config"]
    assert tuned["ease_in"] == 1.2
    assert tuned["save_guide"] is True


@pytest.mark.parametrize("name", PRESETS)
@pytest.mark.parametrize("picture", [(1080, 1920), (1920, 1080), (1000, 1000), (3024, 4032), (720, 1280), (4096, 2160)])
def test_the_geometry_is_valid_for_the_half_resolution_reference(templates, name, picture):
    pipes = _pipes(templates[name])
    guide = _by_id(pipes, "pose_guide")["config"]
    stage1 = _by_id(pipes, "generator_stage1")["config"]
    assert stage1["match_video"] is True
    assert guide["size_multiple"] % (32 * REFERENCE_DOWNSCALE) == 0
    width, height = resolve_output_size(
        picture[0], picture[1], max_pixels=guide["max_pixels"], multiple=guide["size_multiple"],
    )
    assert width * height <= guide["max_pixels"]
    fitted = _fit_pixel_budget(width, height, stage1["max_pixels"], grid=32 * REFERENCE_DOWNSCALE)
    assert fitted == (width, height)
    assert width % 64 == 0 and height % 64 == 0


@pytest.mark.parametrize("name", PRESETS)
def test_the_mode_recommends_the_union_lora_by_filename(templates, name):
    root = Path("content/presets/marketplace") / name / "modes" / "motion_transfer"
    form = yaml.safe_load((root / "form.yml").read_text(encoding="utf-8"))
    requirement = form["requirements"][0]
    assert requirement["filename"] == UNION_LORA
    assert requirement["hint"]
    models = yaml.safe_load((root / "tabs" / "generation.yml").read_text(encoding="utf-8"))
    section = next(f for f in models["fields"] if f.get("label") == "Models")
    lora = next(f for f in section["children"] if f.get("name") == "effect_lora")
    assert [r["name"] for r in lora["configuration"]["recommendations"]] == [UNION_LORA]


@pytest.mark.parametrize("name", PRESETS)
def test_the_prompt_is_optional_and_falls_back_to_a_dancing_caption(templates, name):
    template = templates[name]
    assert "motion_transfer" in template.vars["optional_prompt_modes"]
    form_data = build_fixture_form_data(template, "motion_transfer")
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    pipes = processor.process(
        template,
        {"mode": "motion_transfer", "form_data": form_data, "prompts": [{"positive": "", "negative": ""}]},
    )
    caption = next(p for p in pipes if p["name"] == "prompt_encoder")["config"]["p_prompt"]["output"]
    assert "dancing" in caption and "person from the picture" in caption
