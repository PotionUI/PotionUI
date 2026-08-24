"""Tests for sparse-attention wiring in the native MiniMax-H3 preset's `video`
mode: the Advanced tab's method select plus its per-method knobs must reach
the one `generator/video_minimax_h3` node, and default to the pipe's own off
values so an untouched form renders exactly the pipeline it did before the
controls existed.

Mirrors tests/features/presets/test_minimax_h3_step_cache_wiring.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_SOL_ON = {
    "sparse_attn": "sol",
    "sol_attn_tau": 1.4,
    "sparse_attn_dense_last_steps": 3,
}

_SLA_ON = {
    "sparse_attn": "sla",
    "sla_sparsity": 0.85,
    "sla_block_size": 128,
    "sparse_attn_dense_last_steps": 3,
}

_KEYS = ("sparse_attn", "sol_attn_tau", "sla_sparsity", "sla_block_size", "sparse_attn_dense_last_steps")


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "MiniMax-H3" in str(p.path)), None)
    if template is None:
        pytest.skip("native/MiniMax-H3 preset not present")
    return template


def _process(h3_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
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
    if form_over:
        form_data.update(form_over)
    generation_data = {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": "video",
        "form_data": form_data,
    }
    return processor.process(h3_template, generation_data)


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/video_minimax_h3")


def test_sol_values_reach_the_generator(h3_template):
    generator = _generator(_process(h3_template, _SOL_ON))
    assert generator["enabled"] is True
    cfg = generator["config"]
    assert cfg["sparse_attn"] == "sol"
    assert cfg["sol_attn_tau"] == 1.4
    assert cfg["sparse_attn_dense_last_steps"] == 3


def test_sla_values_reach_the_generator(h3_template):
    cfg = _generator(_process(h3_template, _SLA_ON))["config"]
    assert cfg["sparse_attn"] == "sla"
    assert cfg["sla_sparsity"] == 0.85
    assert cfg["sla_block_size"] == 128
    assert cfg["sparse_attn_dense_last_steps"] == 3


def test_sparse_attn_values_arrive_as_the_declared_types(h3_template):
    cfg = _generator(_process(h3_template, _SLA_ON))["config"]
    assert isinstance(cfg["sparse_attn"], str)
    assert isinstance(cfg["sla_sparsity"], float)
    assert isinstance(cfg["sla_block_size"], int)
    assert isinstance(cfg["sparse_attn_dense_last_steps"], int)


def test_sparse_attn_defaults_to_off(h3_template):
    cfg = _generator(_process(h3_template))["config"]
    assert cfg["sparse_attn"] == "off"
    assert cfg["sol_attn_tau"] == 1.0
    assert cfg["sla_sparsity"] == 0.9
    assert cfg["sla_block_size"] == 64
    assert cfg["sparse_attn_dense_last_steps"] == 2


def test_sparse_attn_defaults_match_the_pipe_spec_defaults(h3_template):
    """The preset must not invent its own defaults: pin them to the specs the
    generator actually declares."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    spec_defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    cfg = _generator(_process(h3_template))["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_default_render_builds_no_sparse_attn_context(h3_template):
    """The end of the chain: an untouched form renders a config the pipe turns
    into no context at all, so attention is untouched."""
    import torch

    from src.pipelines.pipes.generator.video_minimax_h3.layout import build_packed_sequence
    from src.pipelines.pipes.generator.video_minimax_h3.main import build_sparse_attn_ctx

    layout = build_packed_sequence(
        torch.ones(4, dtype=torch.long), num_latent_frames=2, latent_height=4, latent_width=4,
        num_audio_latents=2, patch_size=(1, 2, 2),
    )
    assert build_sparse_attn_ctx(_generator(_process(h3_template))["config"], layout) is None


def _sparse_attn_group(h3_template) -> dict:
    tab = yaml.safe_load(
        (Path(h3_template.path) / "modes" / "video" / "tabs" / "advanced.yml").read_text()
    )
    return next(
        g for g in tab["fields"]
        if g.get("type") == "section" and "Sparse attention" in str(g.get("label", ""))
    )


def test_the_sparse_attn_fields_are_advanced_audience(h3_template):
    """The controls are an expert escape hatch, not part of the default form."""
    group = _sparse_attn_group(h3_template)
    assert group["audience"] == "advanced"
    names = {
        child.get("name")
        for entry in group["children"]
        for child in ([entry] if entry.get("name") else entry.get("children", []))
    }
    assert names == set(_KEYS)


def test_the_sparse_attn_fields_carry_no_description_key(h3_template):
    """Field guidance lives in `ai_hint` and YAML comments -- a `description:`
    key would re-introduce the label duplication the maintainer removed."""
    group = _sparse_attn_group(h3_template)

    def walk(node):
        yield node
        for child in node.get("children", []) or []:
            yield from walk(child)

    assert not [n for n in walk(group) if "description" in n]
