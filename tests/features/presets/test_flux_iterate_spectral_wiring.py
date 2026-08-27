"""Tests that the Flux2 preset's `txt2img` mode wires `iterate_mode` /
`spectral_progressive` (Advanced tab) into `generator/flux`'s config.

Both keys are declared/validated by the shared
`FlowMatchGeneratorPipe.validate_config` (see
tests/pipelines/pipes/_shared/generation/test_flow_generator_pipe.py for the
config-spec-level tests) rather than by `generator/flux`'s own
`configuration()` -- this preset is the only surface for them on Flux2 today.
`spectral_progressive` is Flux2-only (silently ignored on the Flux1
architecture) so its wiring is only exercised here, not on Flux1; `iterate_mode`
exists on both, mirrored on Flux1 by
tests/features/presets/test_flux_step_cache_wiring.py's sibling cache-wiring
coverage.

Mirrors tests/features/presets/test_flux_step_cache_wiring.py, with the
preset-path filter fixed for the marketplace/local layout (the reference
test's `"native/Flux" in str(p.path)` filter predates that split and never
matches, so all its `flux_template`-dependent cases silently skip).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/Flux2")


@pytest.fixture(scope="module")
def flux_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/Flux2" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/Flux2 preset not present")
    return template


def _process(flux_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/flux_dit.safetensors",
        "text_encoder": "/models/qwen3_te.safetensors",
        "vae": "/models/flux_vae.safetensors",
        "resolution": "1024x1024",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "txt2img", "form_data": form_data}
    return processor.process(flux_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: the values land with the declared types ---------------------

def test_iterate_mode_defaults_to_off(flux_template):
    cfg = _pipe(_process(flux_template), "generator/flux")["config"]
    assert cfg["iterate_mode"] is False


def test_iterate_mode_on_reaches_the_generator(flux_template):
    cfg = _pipe(_process(flux_template, {"iterate_mode": True}), "generator/flux")["config"]
    assert cfg["iterate_mode"] is True


def test_spectral_progressive_defaults_to_explicitly_disabled(flux_template):
    # The dict is always present (so form.spectral_progressive_start_scale
    # still has somewhere to land), but `enabled` must be explicit False --
    # see the pipeline.yml comment: NativeGenerator._spectral_progressive_config
    # treats a dict with no `enabled` key as ON by default.
    cfg = _pipe(_process(flux_template), "generator/flux")["config"]
    sp = cfg["spectral_progressive"]
    assert sp["enabled"] is False
    assert sp["scales"] == [0.5, 1.0]


def test_spectral_progressive_on_reaches_the_generator_with_custom_scale(flux_template):
    cfg = _pipe(_process(flux_template, {
        "spectral_progressive_enabled": True,
        "spectral_progressive_start_scale": 0.35,
    }), "generator/flux")["config"]
    sp = cfg["spectral_progressive"]
    assert sp["enabled"] is True
    assert sp["scales"] == [0.35, 1.0]


def test_spectral_progressive_default_off_is_rejected_by_the_engine_config(flux_template):
    # Round-trip through the same validation FlowMatchGeneratorPipe.validate_config
    # runs at generation time -- the untouched-form dict must be config-valid.
    from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe

    cfg = _pipe(_process(flux_template), "generator/flux")["config"]
    FlowMatchGeneratorPipe.validate_config(cfg)  # must not raise


# -- form definition ---------------------------------------------------------

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
def advanced_tab():
    return yaml.safe_load((PRESET_DIR / "modes/txt2img/tabs/advanced.yml").read_text())


def test_iterate_mode_checkbox_is_present(advanced_tab):
    field = _field_by_name(advanced_tab["fields"], "iterate_mode")
    assert field is not None
    assert field["type"] == "checkbox"
    assert field["default"] is False


def test_spectral_progressive_fields_are_present(advanced_tab):
    enabled = _field_by_name(advanced_tab["fields"], "spectral_progressive_enabled")
    scale = _field_by_name(advanced_tab["fields"], "spectral_progressive_start_scale")
    assert enabled is not None and enabled["type"] == "checkbox" and enabled["default"] is False
    assert scale is not None and scale["type"] == "slider" and scale["default"] == 0.5
