"""Tests for the generator/z_image pipe — resolution snapping + basic contract.

`NativeGenerator` / `make_device_plan` are patched with light fakes; Z-Image uses
the Flux-style 2D AE (16ch, //8) so its latent is 4D and its granularity is 16px.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from src.pipelines.outputs import GalleryGenerationOutput, ImageGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.z_image.main import GeneratorZImagePipe


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "z_image"
    variant: str = "z_image"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 3.0, "guidance": "cfg"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)  # Z-Image granularity 16px

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


def _cond_model():
    return SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})


def _bundle():
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=12.0), te_encoder=object(), vae=object(),
    )


def _make_pipe(**over):
    cfg = GeneratorZImagePipe.get_default_config()
    cfg.update(over)
    return GeneratorZImagePipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,)):
    inp = {
        "model": _bundle(),
        "conditioning": [_cond_model() for _ in range(quantity)],
        "seed": list(seeds),
    }
    return PipeInput(input=inp)


def setup_function(_):
    _FakeGenerator.instances.clear()


def test_metadata():
    assert GeneratorZImagePipe.name == "generator"
    assert GeneratorZImagePipe.outputs()[0].io_type == IOType.IMAGE


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_resolution_snapped_to_granularity():
    pipe = _make_pipe(resolution="1920x1080")   # 1080 not a multiple of 16
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1072 // 8, 1920 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_emits_image_gallery():
    pipe = _make_pipe(quantity=1)
    emitted = []
    pipe.process(_pipe_input(seeds=(9,)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert isinstance(gallery[0].images[0], ImageGenerationOutput)
    assert gallery[0].images[0].seed == 9


# -- step_cache / spectral_progressive config -------------------------------


def test_step_cache_declared_in_configuration():
    spec = next(s for s in GeneratorZImagePipe.configuration() if s.name == "step_cache")
    assert spec.param_type is dict


def test_spectral_progressive_declared_in_configuration():
    spec = next(s for s in GeneratorZImagePipe.configuration() if s.name == "spectral_progressive")
    assert spec.param_type is dict


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_step_cache_options():
    step_cache = {"rel_threshold": 0.12, "warmup_steps": 2}
    pipe = _make_pipe(step_cache=step_cache)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] == step_cache


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_step_cache_defaults_to_none():
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_spectral_progressive():
    sp = {"enabled": True, "scales": [0.5, 1.0]}
    pipe = _make_pipe(spectral_progressive=sp)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["spectral_progressive"] == sp


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_spectral_progressive_defaults_to_none():
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["spectral_progressive"] is None


# -- schedule / schedule_options (flat sigma-schedule knobs) ---------------
#
# schedule_settings_config_specs()/schedule_settings_overrides()
# (guidance_options.py) are the same flat knobs the Wan/LTX video pipes
# expose; apply_schedule_settings merges their resolved values into
# ctx.extra["schedule_settings"], which NativeGenerator.sample whitelists
# through to build_sigmas.

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_schedule_beta_with_options_lands_in_schedule_settings():
    pipe = _make_pipe(schedule="beta", schedule_options={"alpha": 0.5, "beta": 0.7})
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["schedule_settings"] == {
        "schedule": "beta",
        "schedule_options": {"alpha": 0.5, "beta": 0.7},
    }


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_schedule_shift_reaches_schedule_settings_verbatim():
    # "shift" is the Schedule field's default and a real registry key, so it
    # travels as a value rather than being dropped; build_sigmas resolves it to
    # the model's own ramp (see test_guidance_options.py).
    pipe = _make_pipe(schedule="shift")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["schedule_settings"] == {"schedule": "shift"}


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_empty_schedule_leaves_schedule_settings_untouched():
    # The pipe's own default "" is a byte-identical no-op --
    # schedule_settings_overrides() returns {} for every knob at its default,
    # so apply_schedule_settings never assigns.
    pipe = _make_pipe()
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra.get("schedule_settings") is None


def test_release_idle_te_removed():
    """Unconditional TE eviction was removed -- the residency coordinator now
    co-resides/evicts the TE under measured VRAM pressure instead of the pipe
    unconditionally dropping it from the MODELS cache every generation (see
    memory/residency.py's run_text_encode + NativeGenerator._own_models)."""
    assert not hasattr(GeneratorZImagePipe, "_release_idle_te")
    assert "MODELS" not in {s.name for s in GeneratorZImagePipe.inputs()}
