"""Tests for FBCache step-cache wiring in the native Flux1/Flux2 presets'
`txt2img` mode. Mirrors tests/features/presets/test_wan_step_cache_wiring.py /
test_ltx_step_cache_wiring.py, adapted for generator/flux's own mechanism:
`FlowMatchGeneratorPipe.build_context` reads a nested `step_cache` DICT
(`self.config.get("step_cache")`) directly -- there is no
sampler_step_cache_kwargs()-style flat-key resolver for this family (that's
a Wan/LTX video-pipe mechanism) -- so the Advanced tab's three flat sliders
are assembled into that dict shape by the preset itself, not by the pipe.

Step cache works identically on both Flux1 and Flux2 (unlike shift/spectral
progressive, which are Flux2-only), so every case here is parametrized across
both presets.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIRS = {
    "Flux1": Path("content/presets/marketplace/Flux1"),
    "Flux2": Path("content/presets/marketplace/Flux2"),
}

_CACHE_ON = {
    "step_cache_threshold": 0.12,
    "step_cache_warmup_steps": 6,
    "step_cache_max_skips": 2,
}

_BASE_FORM_DATA = {
    "diffusion_model": "/models/flux_dit.safetensors",
    "text_encoder": "/models/te.safetensors",
    "vae": "/models/flux_vae.safetensors",
    "resolution": "1024x1024",
}


@pytest.fixture(scope="module", params=["Flux1", "Flux2"])
def preset_name(request):
    return request.param


@pytest.fixture(scope="module")
def preset_dir(preset_name):
    return PRESET_DIRS[preset_name]


@pytest.fixture(scope="module")
def flux_template(preset_name):
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    suffix = f"marketplace/{preset_name}"
    template = next((p for p in loader.presets if suffix in str(p.path)), None)
    if template is None:
        pytest.skip(f"{suffix} preset not present")
    return template


def _process(flux_template, preset_name, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = dict(_BASE_FORM_DATA)
    if preset_name == "Flux1":
        form_data["clip_l"] = "/models/clip_l.safetensors"
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "txt2img", "form_data": form_data}
    return processor.process(flux_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: the dict lands with the declared types -----------------------

def test_step_cache_dict_reaches_the_generator(flux_template, preset_name):
    pipes = _process(flux_template, preset_name, _CACHE_ON)
    generator = _pipe(pipes, "generator/flux")
    assert generator["enabled"] is True
    step_cache = generator["config"]["step_cache"]
    assert step_cache == {"rel_threshold": 0.12, "warmup_steps": 6, "max_consecutive_skips": 2}
    assert isinstance(step_cache["rel_threshold"], float)
    assert isinstance(step_cache["warmup_steps"], int)
    assert isinstance(step_cache["max_consecutive_skips"], int)


def test_step_cache_defaults_to_off(flux_template, preset_name):
    cfg = _pipe(_process(flux_template, preset_name), "generator/flux")["config"]
    assert cfg["step_cache"] == {"rel_threshold": 0.0, "warmup_steps": 4, "max_consecutive_skips": 3}


def test_off_by_default_matches_flow_generator_pipes_own_falsy_check(flux_template, preset_name):
    # FlowMatchGeneratorPipe.build_context does `self.config.get("step_cache")
    # or None` -- a dict with rel_threshold=0.0 is still truthy (non-empty),
    # so this stays byte-identical only because denoise()'s own
    # StepCacheSet.enabled (rel_threshold > 0.0) declines to wrap the
    # guidance strategy, not because the dict itself is empty/absent.
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    cfg = _pipe(_process(flux_template, preset_name), "generator/flux")["config"]
    assert cfg["step_cache"]
    assert StepCacheSet(cfg["step_cache"]).enabled is False


def test_configured_values_enable_the_cache_set(flux_template, preset_name):
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    cfg = _pipe(_process(flux_template, preset_name, _CACHE_ON), "generator/flux")["config"]
    assert StepCacheSet(cfg["step_cache"]).enabled is True


# -- form definition --------------------------------------------------------

def _field_by_name(fields, name):
    for f in fields:
        if f.get("name") == name:
            return f
        if "children" in f:
            found = _field_by_name(f["children"], name)
            if found is not None:
                return found
    return None


@pytest.fixture(scope="module")
def advanced_tab(preset_dir):
    return yaml.safe_load((preset_dir / "modes/txt2img/tabs/advanced.yml").read_text())


@pytest.mark.parametrize("name,default,min_v,max_v,step", [
    ("step_cache_threshold", 0.0, 0.0, 0.3, 0.01),
    ("step_cache_warmup_steps", 4, 0, 12, 1),
    ("step_cache_max_skips", 3, 0, 10, 1),
])
def test_step_cache_sliders_are_present_with_their_ranges(advanced_tab, name, default, min_v, max_v, step):
    field = _field_by_name(advanced_tab["fields"], name)
    assert field is not None, f"{name} is not on the Advanced tab"
    assert field["type"] == "slider"
    assert field["default"] == default
    assert field["configuration"]["min"] == min_v
    assert field["configuration"]["max"] == max_v
    assert field["configuration"]["step"] == step


def test_step_cache_declared_on_the_generator_spec():
    # Discoverability: the knob must be typed on generator/flux itself, not
    # just an undeclared passthrough the pipe happens to read.
    from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe

    specs = {s.name: s for s in GeneratorFluxPipe.configuration()}
    assert "step_cache" in specs
    assert specs["step_cache"].param_type is dict
