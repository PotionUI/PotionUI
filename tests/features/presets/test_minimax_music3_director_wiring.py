"""Tests for MiniMax-Music3's single `song` mode.

No Music Director: lyrics are the STANDARD prompt section (the same
segmented editor every image preset uses), authored with [Verse]/[Chorus]
tags typed manually. This file proves modes/song/pipeline.yml's Jinja reads
`form.description`/`form.duration`/`form.instrumental` and the resolved
prompt (`generation.prompts.first.positive`) correctly, including the
"Instrumental (no vocals)" override (literal `"[instrumental]"` lyrics
regardless of what the prompt contains).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def music3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "MiniMax-Music3" in str(p.path)), None)
    if template is None:
        pytest.skip("MiniMax-Music3 preset not present")
    return template


def _process(music3_template, form_over: dict | None = None, positive: str = ""):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_music3_dit.safetensors",
        "text_encoder": "/models/minimax_music3_te.safetensors",
        "vae": "/models/minimax_music3_dav.safetensors",
    }
    form_data.update(form_over or {})
    return processor.process(music3_template, {
        "prompts": [{"positive": positive, "negative": ""}],
        "mode": "song",
        "form_data": form_data,
    })


def _pipe(pipes, name):
    return next(p for p in pipes if p["name"] == name)


def _flatten_fields(fields):
    """Recursively walks a resolved field tree, yielding every leaf field
    dict keyed by `name`. Reads the tab YAML directly (not through
    PresetFormSerializer's external-children resolution) since this tab has
    no `@loop`/external-`children` indirection to resolve -- see
    test_references_tab_layout.py for the case where that distinction
    matters."""
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("name"):
            yield field
        children = field.get("children")
        if isinstance(children, list):
            yield from _flatten_fields(children)


# -- the capability declaration ----------------------------------------------

def test_the_preset_declares_no_music_director_capability(music3_template):
    assert "music_director" not in (music3_template.vars or {})


def test_the_preset_declares_no_negative_prompt_support(music3_template):
    assert (music3_template.vars or {}).get("supports_negative_prompt") is False


def test_the_preset_declares_a_paragraph_segment_join(music3_template):
    assert (music3_template.vars or {}).get("prompt", {}).get("segment_join") == "paragraph"


def test_the_generation_tab_owns_caption_duration_and_instrumental():
    tab_path = Path("content/presets/marketplace/MiniMax-Music3/modes/song/tabs/generation.yml")
    fields = yaml.safe_load(tab_path.read_text())["fields"]
    by_name = {f["name"]: f for f in _flatten_fields(fields)}

    assert by_name["description"]["type"] == "string"
    assert by_name["description"]["required"] is True
    assert by_name["description"]["configuration"]["input_type"] == "textarea"

    assert by_name["duration"]["type"] == "integer"
    assert by_name["duration"]["default"] == 60

    assert by_name["instrumental"]["type"] == "boolean"
    assert by_name["instrumental"]["default"] is False


def test_the_preset_declares_a_single_mode(music3_template):
    assert set(music3_template.modes.keys()) == {"song"}


# -- the rendered pipeline --------------------------------------------------------

def test_the_form_description_field_becomes_the_caption(music3_template):
    config = _pipe(
        _process(music3_template, form_over={"description": "warm 90s boom-bap, vinyl crackle, female vocal"}),
        "generator/audio_minimax_music3",
    )["config"]
    assert config["caption"] == "warm 90s boom-bap, vinyl crackle, female vocal"


def test_lyrics_come_from_the_resolved_prompt(music3_template):
    config = _pipe(
        _process(music3_template, positive="[Verse]\nrain on the window\n\n[Chorus]\nnowhere to go"),
        "generator/audio_minimax_music3",
    )["config"]
    assert config["lyrics"] == "[Verse]\nrain on the window\n\n[Chorus]\nnowhere to go"


def test_a_field_less_submission_composes_an_empty_caption_and_lyrics(music3_template):
    """A raw API/MCP caller that submits neither the form fields nor a
    prompt still gets safe empty-string defaults, never a Jinja
    StrictUndefined error."""
    config = _pipe(_process(music3_template), "generator/audio_minimax_music3")["config"]
    assert config["caption"] == ""
    assert config["lyrics"] == ""


def test_the_form_instrumental_toggle_hardcodes_lyrics_regardless_of_the_resolved_prompt(music3_template):
    """The "Instrumental (no vocals)" toggle is `form.instrumental` -- it
    must force the literal `"[instrumental]"` lyrics string even when the
    prompt itself carries real lyrics."""
    config = _pipe(
        _process(
            music3_template,
            form_over={
                "description": "cinematic ambient, sparse piano and strings",
                "instrumental": True,
            },
            positive="[Chorus]\nnot instrumental",
        ),
        "generator/audio_minimax_music3",
    )["config"]
    assert config["caption"] == "cinematic ambient, sparse piano and strings"
    assert config["lyrics"] == "[instrumental]"


def test_the_resolved_prompt_passes_through_when_instrumental_is_off(music3_template):
    config = _pipe(
        _process(music3_template, form_over={"instrumental": False}, positive="[Chorus]\nnot instrumental"),
        "generator/audio_minimax_music3",
    )["config"]
    assert config["lyrics"] == "[Chorus]\nnot instrumental"


def test_duration_comes_from_the_form_field(music3_template):
    config = _pipe(_process(music3_template, form_over={"duration": 120}), "generator/audio_minimax_music3")["config"]
    assert config["duration"] == 120


def test_a_field_less_submission_falls_back_to_60s_duration(music3_template):
    config = _pipe(_process(music3_template), "generator/audio_minimax_music3")["config"]
    assert config["duration"] == 60


def test_duration_and_sampling_knobs_reach_the_generator(music3_template):
    config = _pipe(_process(music3_template, form_over={
        "steps": 20, "seed": 777, "ar_cfg_scale": 1.7, "cfg_scale": 2.0, "top_k": 40,
    }), "generator/audio_minimax_music3")["config"]

    assert config["steps"] == 20
    assert config["seed"] == 777
    assert config["ar_cfg_scale"] == 1.7
    assert config["cfg_scale"] == 2.0
    assert config["top_k"] == 40


def test_device_is_not_a_form_field_the_pipe_supplies_its_own_default(music3_template):
    """No `device` knob anywhere in the form (Generation/Advanced) -- the
    generator pipe's own `PipeConfigSpec("device", str, "cuda", ...)` default
    applies, same idiom as every other native preset (H3, Krea-2): a form
    field for an internal engine knob was leaked scope, not a real setting."""
    config = _pipe(_process(music3_template), "generator/audio_minimax_music3")["config"]
    assert "device" not in config


def test_the_models_reach_the_loader(music3_template):
    loader = _pipe(_process(music3_template), "model_loader/minimax_music3")["config"]
    assert loader["model"]["file_path"] == "/models/minimax_music3_dit.safetensors"
    assert loader["text_encoder"]["file_path"] == "/models/minimax_music3_te.safetensors"
    assert loader["vae"]["file_path"] == "/models/minimax_music3_dav.safetensors"
