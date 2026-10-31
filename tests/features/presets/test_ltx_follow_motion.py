from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

UNION_LORA = "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"
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


def _pipes(template, form_over=None, prompt=""):
    form_data = build_fixture_form_data(template, "follow_motion")
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    generation = {"prompts": [{"positive": prompt, "negative": ""}], "mode": "follow_motion", "form_data": form_data}
    return processor.process(template, generation)


def _by_id(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


@pytest.mark.parametrize("name", PRESETS)
def test_the_guide_defaults_to_depth_and_follows_the_choice(templates, name):
    assert _by_id(_pipes(templates[name]), "motion_guide")["config"]["type"] == "depth"
    assert _by_id(_pipes(templates[name], {"guide_type": "edges"}), "motion_guide")["config"]["type"] == "edges"


@pytest.mark.parametrize("name", PRESETS)
def test_only_the_video_is_read_into_the_guide(templates, name):
    guide = _by_id(_pipes(templates[name]), "motion_guide")
    assert guide["name"] == "control_video"
    assert _wired(guide) == {("video", "media_loader", "video")}
    assert "match_picture" not in guide["config"] and "ease_in" not in guide["config"]
    assert guide["config"]["size_multiple"] == 64


@pytest.mark.parametrize("name", PRESETS)
def test_the_guide_is_the_reference_clip_of_both_passes_and_the_picture_the_first_frame(templates, name):
    pipes = _pipes(templates[name])
    for stage in ("generator_stage1", "generator"):
        pipe = _by_id(pipes, stage)
        assert ("video", "motion_guide", "video") in _wired(pipe)
        assert ("image", "media_image", "image") in _wired(pipe)
        assert pipe["config"]["media_placements"] == [
            {"source": "video", "index": 0, "frame": "first", "strength": 1.0, "role": "reference"},
            {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
        ]
        assert pipe["config"]["match_video"] is True


@pytest.mark.parametrize("name", PRESETS)
def test_the_source_soundtrack_is_not_carried_over(templates, name):
    pipes = _pipes(templates[name])
    for stage in ("generator_stage1", "generator"):
        assert _by_id(pipes, stage)["config"]["audio"] is False
    assert all(("audio", "media_loader", "video") not in _wired(p) for p in pipes if p.get("input"))


@pytest.mark.parametrize("name", PRESETS)
def test_the_union_lora_is_scoped_to_both_passes_at_the_follow_strength(templates, name):
    pipes = _pipes(templates[name], {"effect_strength": 0.8})
    for stage in ("generator_stage1", "generator"):
        (entry,) = _by_id(pipes, stage)["config"]["scoped_loras"]
        assert entry["weight"] == 0.8 and entry["file_path"].endswith("effect_lora.safetensors")
    assert "scoped_loras" not in _by_id(pipes, "latent_upscaler")["config"]


@pytest.mark.parametrize("name", PRESETS)
def test_an_empty_prompt_falls_back_to_a_caption_and_a_written_one_wins(templates, name):
    pipes = _pipes(templates[name], prompt="")
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")["config"]["p_prompt"]["output"]
    assert "cinematic" in encoder
    written = next(p for p in _pipes(templates[name], prompt="a snowy village") if p["name"] == "prompt_encoder")
    assert written["config"]["p_prompt"]["output"] == "a snowy village"


@pytest.mark.parametrize("name", PRESETS)
def test_save_guide_reaches_the_pipe_and_the_pose_ease_in_is_gone(templates, name):
    assert _by_id(_pipes(templates[name]), "motion_guide")["config"]["save_guide"] is False
    assert _by_id(_pipes(templates[name], {"save_guide": True}), "motion_guide")["config"]["save_guide"] is True
    root = Path("content/presets/marketplace") / name / "modes" / "follow_motion"
    advanced = (root / "tabs" / "advanced.yml").read_text(encoding="utf-8")
    assert "pose_ease_in" not in advanced and "save_guide" in advanced


@pytest.mark.parametrize("name", PRESETS)
def test_the_form_offers_depth_and_edges_and_reuses_the_motion_transfer_models(templates, name):
    root = Path("content/presets/marketplace") / name / "modes" / "follow_motion"
    references = yaml.safe_load((root / "tabs" / "references.yml").read_text(encoding="utf-8"))
    guide = next(f for f in references["fields"] if f["name"] == "guide_type")
    assert [o["value"] for o in guide["configuration"]["options"]] == ["depth", "edges"]
    assert guide["default"] == "depth"
    form = yaml.safe_load((root / "form.yml").read_text(encoding="utf-8"))
    assert form["requirements"][0]["field"] == "effect_lora"
    tabs = form["fields"][0]["children"]
    assert tabs[0]["children"].endswith("modes/motion_transfer/tabs/generation.yml")
    assert (root.parent / "motion_transfer" / "tabs" / "generation.yml").is_file()
    models = yaml.safe_load((root.parent / "motion_transfer" / "tabs" / "generation.yml").read_text(encoding="utf-8"))
    section = next(f for f in models["fields"] if f.get("label") == "Models")
    lora = next(f for f in section["children"] if f.get("name") == "effect_lora")
    (recommendation,) = lora["configuration"]["recommendations"]
    assert recommendation["name"] == UNION_LORA
    assert re.fullmatch(r"[0-9a-f]{64}", recommendation["sha256"])
    source = recommendation.get("link") or json.loads(recommendation["ref"])["file"]
    assert source.rsplit("/", 1)[-1] == UNION_LORA
