"""Tests for the MiniMax-H3 latent-upscale wiring: the `video` mode's Enhance
tab two-stage refine (generator_stage1 -> latent_upscaler -> generator_stage2)
and the standalone `upscale` mode's own pipeline.

Mirrors tests/features/presets/test_ltx_stage2_loras.py's harness and
tests/features/presets/test_ltx_upscale_ux.py's fixtures shape, adapted to
MiniMax-H3's own generator (no cfg, no NAG, no media_placements/document
reconnection at stage 2 -- generator/video_minimax_h3's `initial_latent`
refine entry is mutually exclusive with document/image/reference inputs).
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_H3_PRESET_ID = "01KX47H3MINIMAX000000000VA"


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if p.id == _H3_PRESET_ID), None)
    assert template is not None, (
        f"MiniMax-H3 preset {_H3_PRESET_ID} not loaded from content/presets "
        f"(loaded: {sorted(p.id for p in loader.presets)})"
    )
    return template


def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


def _inputs(pipe):
    return {edge["name"]: (edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _process(h3_template, mode="video", form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_h3.safetensors",
        "text_encoder": "/models/qwen3_vl.safetensors",
        "video_vae": "/models/h3_video_vae.safetensors",
        "audio_vae": "/models/h3_audio_vae.safetensors",
        "resolution": "1344x768",
        "prompt": "a dragon",
    }
    if mode == "upscale":
        form_data["input_video"] = "/media/source.mp4"
        form_data.pop("resolution")
    if form_over:
        form_data.update(form_over)
    bound = bind_form(
        h3_template, mode, form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": mode,
        "form_data": dict(bound.values),
    }
    return processor.process(h3_template, generation_data)



def test_latent_upscale_off_stage1_decodes_stage2_disabled(h3_template):
    pipes = _process(h3_template)
    stage1 = _pipe(pipes, "generator_stage1")
    stage2 = _pipe(pipes, "generator_stage2")
    upscaler = _pipe(pipes, "latent_upscaler")
    assert stage1["config"]["decode"] is True
    assert stage2["enabled"] is False
    assert stage2["config"] == {}
    assert upscaler["enabled"] is False


def test_latent_upscale_on_stage1_skips_decode_and_stage2_refines(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    stage1 = _pipe(pipes, "generator_stage1")
    stage2 = _pipe(pipes, "generator_stage2")
    upscaler = _pipe(pipes, "latent_upscaler")
    assert stage1["config"]["decode"] is False
    assert upscaler["enabled"] is True
    assert stage2["enabled"] is True
    assert stage2["config"]["decode"] is True
    assert stage2["config"]["audio_source"] == "passthrough"


def test_latent_upscale_on_wires_initial_latent_and_stage1_audio(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    stage2 = _pipe(pipes, "generator_stage2")
    inputs = _inputs(stage2)
    assert inputs["initial_latent"] == ("latent_upscaler", "latent")
    assert inputs["initial_audio_latent"] == ("generator_stage1", "audio_latent")
    assert inputs["audio"] == ("generator_stage1", "audio")
    assert "image" not in inputs
    assert "director_image" not in inputs


def test_latent_upscale_on_stage2_audio_refine_defaults_to_lock(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["config"]["audio_refine"] == "lock"


def test_latent_upscale_on_stage2_audio_refine_reads_the_form(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors", "audio_refine": "resample",
    })
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["config"]["audio_refine"] == "resample"


def test_stage2_carries_no_document_or_keyframe_anchors(h3_template):
    """generator/video_minimax_h3's refine entry path is mutually exclusive
    with document/image/reference conditioning (module docstring) -- stage 2
    must not set either config key."""
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    stage2 = _pipe(pipes, "generator_stage2")
    assert "document" not in stage2["config"]
    assert "keyframe_anchors" not in stage2["config"]


@pytest.mark.parametrize("latent_upscale,target_mode,megapixels,scale", [
    ("hd", "megapixels", 2.1, 1.5),
    ("2k", "megapixels", 3.7, 1.5),
    ("1.5x", "scale", 2.1, 1.5),
    ("2x", "scale", 2.1, 2.0),
])
def test_latent_upscale_selection_maps_to_latent_upscaler_target(h3_template, latent_upscale, target_mode, megapixels, scale):
    pipes = _process(h3_template, form_over={
        "latent_upscale": latent_upscale, "upscale_model": "/models/h3_upscaler.safetensors",
    })
    cfg = _pipe(pipes, "latent_upscaler")["config"]
    assert cfg["target_mode"] == target_mode
    assert cfg["megapixels"] == pytest.approx(megapixels)
    assert cfg["scale"] == pytest.approx(scale)


@pytest.mark.parametrize("refine_denoise,expected", [
    ("0.30", 0.30), ("0.45", 0.45), ("0.60", 0.60),
])
def test_refine_denoise_select_reaches_stage2_as_a_float(h3_template, refine_denoise, expected):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
        "refine_denoise": refine_denoise,
    })
    cfg = _pipe(pipes, "generator_stage2")["config"]
    assert cfg["denoise"] == pytest.approx(expected)
    assert isinstance(cfg["denoise"], float)


def test_refine_defaults_are_denoise_045_shift_9_steps_4(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    cfg = _pipe(pipes, "generator_stage2")["config"]
    assert cfg["denoise"] == pytest.approx(0.45)
    assert cfg["video_sigma_shift"] == pytest.approx(9.0)
    assert cfg["steps"] == 4


def test_gallery_reads_stage2_when_latent_upscale_on_else_stage1(h3_template):
    off = _pipe(_process(h3_template), "gallery")
    on = _pipe(_process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    }), "gallery")
    off_source = _inputs(off)["video"][0]
    on_source = _inputs(on)["video"][0]
    assert off_source == "generator_stage1"
    assert on_source == "generator_stage2"


def test_upscale_model_reaches_model_loader(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    loader = _pipe(pipes, "model_loader")
    assert loader["config"]["upscale_model"]["file_path"] == "/models/h3_upscaler.safetensors"


def test_upscale_model_blank_when_latent_upscale_off(h3_template):
    pipes = _process(h3_template)
    loader = _pipe(pipes, "model_loader")
    from src.pipelines.pipes._shared.generation.loader_helpers import path_of
    assert path_of(loader["config"]["upscale_model"]) is None



def test_only_one_model_loader_node_and_both_stages_share_it(h3_template):
    """The DiT is acquired exactly once, regardless of Latent Upscale -- both
    generator stages read the SAME model_loader node, never a second one."""
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
        "refine_loras": [{"model": "/models/loras/distilled.safetensors", "strength": 1.0}],
    })
    assert sum(1 for p in pipes if p["name"] == "model_loader/minimax_h3") == 1
    stage1 = _pipe(pipes, "generator_stage1")
    stage2 = _pipe(pipes, "generator_stage2")
    assert _inputs(stage1)["model"] == ("model_loader", "model")
    assert _inputs(stage2)["model"] == ("model_loader", "model")


def test_latent_upscale_off_model_loader_bakes_form_loras_byte_identical_to_baseline(h3_template):
    """The pre-Enhance-tab baseline: with Latent Upscale off, the LoRA tab's
    `loras` is still baked at load time exactly as before this feature
    existed, and generator_stage1's runtime_loras stays empty (a no-op)."""
    pipes = _process(h3_template, form_over={
        "loras": [{"model": "/models/loras/style.safetensors", "strength": 0.8}],
    })
    loader = _pipe(pipes, "model_loader")
    stage1 = _pipe(pipes, "generator_stage1")
    assert loader["config"]["loras"] == [{"file_path": "/models/loras/style.safetensors", "weight": 0.8}]
    assert stage1["config"]["runtime_loras"] == []


def test_latent_upscale_on_model_loader_bakes_no_loras_stage1_applies_loras_at_runtime(h3_template):
    """Once Latent Upscale is on, `loras` is no longer baked (so the SAME DiT
    acquisition -- same cache key AND fingerprint -- serves both stages
    without a reload); stage 1 applies the LoRA tab's `loras` itself, at
    sampling time, via runtime_loras instead."""
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
        "loras": [{"model": "/models/loras/style.safetensors", "strength": 0.8}],
    })
    loader = _pipe(pipes, "model_loader")
    stage1 = _pipe(pipes, "generator_stage1")
    assert loader["config"]["loras"] == []
    assert stage1["config"]["runtime_loras"] == [{"file_path": "/models/loras/style.safetensors", "weight": 0.8}]


def test_refine_loras_picker_flattens_to_file_path_and_weight_on_stage2_runtime_loras(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
        "refine_loras": [
            {"model": "/models/loras/distilled.safetensors", "strength": 1.0},
            {"model": "/models/loras/detail.safetensors", "strength": 0.6},
        ],
    })
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["config"]["runtime_loras"] == [
        {"file_path": "/models/loras/distilled.safetensors", "weight": 1.0},
        {"file_path": "/models/loras/detail.safetensors", "weight": 0.6},
    ]


def test_stage1_runtime_loras_never_carries_refine_loras_and_vice_versa(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
        "refine_loras": [{"model": "/models/loras/distilled.safetensors", "strength": 1.0}],
        "loras": [{"model": "/models/loras/style.safetensors", "strength": 0.8}],
    })
    stage1 = _pipe(pipes, "generator_stage1")
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage1["config"]["runtime_loras"] == [{"file_path": "/models/loras/style.safetensors", "weight": 0.8}]
    assert stage2["config"]["runtime_loras"] == [{"file_path": "/models/loras/distilled.safetensors", "weight": 1.0}]


def test_refine_loras_field_hidden_when_latent_upscale_off():
    import yaml
    from pathlib import Path

    tab_path = Path("content/presets/marketplace/MiniMax-H3/modes/video/tabs/enhance.yml")
    data = yaml.safe_load(tab_path.read_text())
    field = next(f for f in data["fields"] if f.get("name") == "refine_loras")
    assert field["type"] == "lora_picker"
    assert field["default"] == []
    off_reaction = next(r for r in field["reactions"] if r["when"] == {"field": "latent_upscale", "equals": "off"})
    assert off_reaction["then"] == {"set_visibility": False}
    on_reaction = next(r for r in field["reactions"] if r["when"] == {"field": "latent_upscale", "not_equals": "off"})
    assert on_reaction["then"] == {"set_visibility": True}



def test_standalone_upscale_mode_wires_video_through_latent_upscaler_and_refine(h3_template):
    pipes = _process(h3_template, mode="upscale", form_over={
        "upscale_model": "/models/h3_upscaler.safetensors",
    })
    media = _pipe(pipes, "media_loader")
    upscaler = _pipe(pipes, "latent_upscaler")
    refine = _pipe(pipes, "generator_refine")
    assert media["config"]["media"][0]["path"] == "/media/source.mp4"
    upscaler_inputs = _inputs(upscaler)
    assert upscaler_inputs["video"] == ("media_loader", "video")
    refine_inputs = _inputs(refine)
    assert refine_inputs["initial_latent"] == ("latent_upscaler", "latent")
    assert refine_inputs["audio"] == ("media_loader", "video")
    assert "initial_audio_latent" not in refine_inputs
    assert refine["config"]["audio_source"] == "passthrough"
    assert refine["config"]["audio_refine"] == "lock"


def test_standalone_upscale_mode_audio_refine_reads_the_form(h3_template):
    pipes = _process(h3_template, mode="upscale", form_over={
        "upscale_model": "/models/h3_upscaler.safetensors", "audio_refine": "resample",
    })
    refine = _pipe(pipes, "generator_refine")
    assert refine["config"]["audio_refine"] == "resample"


def test_standalone_upscale_mode_default_recipe(h3_template):
    pipes = _process(h3_template, mode="upscale", form_over={
        "upscale_model": "/models/h3_upscaler.safetensors",
    })
    cfg = _pipe(pipes, "generator_refine")["config"]
    assert cfg["denoise"] == pytest.approx(0.45)
    assert cfg["video_sigma_shift"] == pytest.approx(9.0)
    assert cfg["steps"] == 4


@pytest.mark.parametrize("refine_denoise,expected", [
    ("0.30", 0.30), ("0.45", 0.45), ("0.60", 0.60),
])
def test_standalone_upscale_mode_refine_denoise_select(h3_template, refine_denoise, expected):
    pipes = _process(h3_template, mode="upscale", form_over={
        "upscale_model": "/models/h3_upscaler.safetensors", "refine_denoise": refine_denoise,
    })
    cfg = _pipe(pipes, "generator_refine")["config"]
    assert cfg["denoise"] == pytest.approx(expected)


def test_standalone_upscale_mode_refine_loras_reach_the_model_loader(h3_template):
    pipes = _process(h3_template, mode="upscale", form_over={
        "upscale_model": "/models/h3_upscaler.safetensors",
        "refine_loras": [{"model": "/models/loras/distilled.safetensors", "strength": 1.0}],
    })
    loader = _pipe(pipes, "model_loader/minimax_h3")
    assert loader["config"]["loras"] == [{"file_path": "/models/loras/distilled.safetensors", "weight": 1.0}]


def _resolvable(pipes):
    enabled = [p for p in pipes if p.get("enabled")]
    counts = {}
    for p in enabled:
        counts[p["name"]] = counts.get(p["name"], 0) + 1
    known = {(p.get("id") or p["name"]) for p in pipes} | {p["name"] for p in pipes}
    ambiguous = {p["name"] for p in enabled if counts[p["name"]] > 1 and not any(q.get("id") == p["name"] for q in enabled)}
    return [(p.get("id") or p["name"], e["provider"]) for p in enabled for e in (p.get("input") or []) if e["provider"] not in known or e["provider"] in ambiguous]


def test_every_input_reference_resolves_with_upscale_on(h3_template):
    pipes = _process(h3_template, form_over={
        "latent_upscale": "hd", "upscale_model": "/models/h3_upscaler.safetensors",
    })
    assert _resolvable(pipes) == []


def test_every_input_reference_resolves_with_upscale_off(h3_template):
    assert _resolvable(_process(h3_template)) == []
