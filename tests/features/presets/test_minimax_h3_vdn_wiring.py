"""Tests for the experimental MiniMax-H3 VDN preset's `video` mode.

Three things separate it from the base MiniMax-H3 preset and all three are
silent when wrong: the VDN branch file and the AdaLN sidecar have to reach
`model_loader/minimax_h3` under the loader's own key names, and the generator
must carry no sparse-attention configuration at all (sparse attention decides
what the VDN window already decides, so the two cannot both be in charge).

The preset is selected by ID rather than by a path substring: the loader walks
`rglob("preset.yml")` in filesystem order, so a substring match over a family
with more than one preset directory picks an arbitrary member of it.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_VDN_PRESET_ID = "01KX47H3VDNMINIMAX000000VA"

_SPARSE_KEYS = (
    "sparse_attn",
    "sol_attn_tau",
    "sla_sparsity",
    "sla_block_size",
    "sparse_attn_dense_last_steps",
)


@pytest.fixture(scope="module")
def vdn_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if p.id == _VDN_PRESET_ID), None)
    if template is None:
        pytest.skip("MiniMax-H3-VDN preset not present")
    return template


def _process(vdn_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_h3.safetensors",
        "vdn_module": "/models/minimax_h3_vdn_linear_branch.safetensors",
        "text_encoder": "/models/qwen3_vl.safetensors",
        "video_vae": "/models/h3_video_vae.safetensors",
        "audio_vae": "/models/h3_audio_vae.safetensors",
        "resolution": "1344x768",
        "prompt": "a dragon",
    }
    if form_over:
        form_data.update(form_over)
    bound = bind_form(
        vdn_template, "video", form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": "video",
        "form_data": dict(bound.values),
    }
    return processor.process(vdn_template, generation_data)


def _loader(pipes):
    return next(p for p in pipes if p["name"] == "model_loader/minimax_h3")


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/video_minimax_h3")


def test_the_vdn_branch_reaches_the_loader(vdn_template):
    cfg = _loader(_process(vdn_template))["config"]
    assert cfg["vdn_module"] == {
        "file_path": "/models/minimax_h3_vdn_linear_branch.safetensors",
        "name": "/models/minimax_h3_vdn_linear_branch.safetensors",
    }


def test_the_adaln_sidecar_reaches_the_loader_when_picked(vdn_template):
    cfg = _loader(
        _process(vdn_template, {"dense_time_embedder": "/models/h3_time_embedder.safetensors"})
    )["config"]
    assert cfg["dense_time_embedder"] == {
        "file_path": "/models/h3_time_embedder.safetensors",
        "name": "/models/h3_time_embedder.safetensors",
    }


def test_an_unpicked_sidecar_renders_blank_rather_than_failing(vdn_template):
    """The sidecar is required only for the turbo tier over a pruned checkpoint,
    so the 50-step path submits no value for it at all. A bare `{{ form.x }}`
    would be a build error under strict evaluation.

    `dense_time_embedder` has no field `default:`, so `bind_form` (the real
    request path) resolves an unpicked model picker to `None`, not an absent
    key -- and Jinja's `| default('')` only rescues a genuinely Undefined
    value, not an explicit `None`, so the guard does not fire here. `None` is
    the real, current rendered value for this case."""
    cfg = _loader(_process(vdn_template))["config"]
    assert cfg["dense_time_embedder"] == {"file_path": None, "name": None}


def test_the_generator_carries_no_sparse_attention_configuration(vdn_template):
    cfg = _generator(_process(vdn_template))["config"]
    assert [key for key in _SPARSE_KEYS if key in cfg] == []


def test_the_two_trained_step_tiers_are_the_ones_offered(vdn_template):
    profiles = vdn_template.speed_profiles
    assert profiles["turbo"]["steps"] == 8
    assert profiles["quality"]["steps"] == 50


def test_the_profile_supplies_the_step_count_when_the_form_omits_it(vdn_template):
    cfg = _generator(_process(vdn_template, {"speed_profile": "quality"}))["config"]
    assert cfg["steps"] == 50
