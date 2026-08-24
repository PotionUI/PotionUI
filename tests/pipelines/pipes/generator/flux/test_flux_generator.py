"""Tests for the generator/flux pipe.

`NativeGenerator` / `make_device_plan` are patched with light fakes so the
build_context/generate_one contract, the ConditioningModel->Conditioning
adaptation, the latent-shape math, the shift override, and the gallery emission
are exercised without loading weights or running inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from src.pipelines.outputs import GalleryGenerationOutput, ImageGenerationOutput, ParamGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe


class _FakeModelsService:
    """Records evict_dead_weight(key) calls; returns True (evicted) by default."""

    def __init__(self, evict_result=True, raise_on_evict=False):
        self.evict_calls: list[str] = []
        self._evict_result = evict_result
        self._raise = raise_on_evict

    def evict_dead_weight(self, key: str) -> bool:
        self.evict_calls.append(key)
        if self._raise:
            raise RuntimeError("boom")
        return self._evict_result


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "flux"
    variant: str = "flux2"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 2.02, "guidance": "embedded"})


class _FakeGenerator:
    """Stand-in for NativeGenerator: records sample() args, returns a fake image."""

    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)  # Flux1 granularity 16px

    def latent_shape_for(self, width, height, batch=1):
        # Mimics the engine helper (Flux1 8x downscale, 16 channels).
        return (batch, 16, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


def _cond_model(with_negative=False):
    return SimpleNamespace(
        embeds={"context": torch.ones(1, 4, 8), "pooled": torch.ones(1, 8)},
        n_embeds={"context": torch.ones(1, 4, 8)} if with_negative else {},
    )


def _bundle(te_cache_key=None):
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=9.0),
        te_encoder=object(),
        vae=object(),
        te_cache_key=te_cache_key,
    )


def _make_pipe(**over):
    cfg = GeneratorFluxPipe.get_default_config()
    cfg.update(over)
    return GeneratorFluxPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), with_negative=False, te_cache_key=None, models=None):
    inp = {
        "model": _bundle(te_cache_key=te_cache_key),
        "conditioning": [_cond_model(with_negative) for _ in range(quantity)],
        "seed": list(seeds),
    }
    if models is not None:
        inp["MODELS"] = models
    return PipeInput(input=inp)


def setup_function(_):
    _FakeGenerator.instances.clear()


# -- metadata --------------------------------------------------------------

def test_name_inputs_outputs():
    assert GeneratorFluxPipe.name == "generator"
    inputs = {i.name: i for i in GeneratorFluxPipe.inputs()}
    assert inputs["conditioning"].io_type == IOType.CONDITIONING
    assert inputs["model"].io_type == IOType.MODEL
    assert GeneratorFluxPipe.outputs()[0].io_type == IOType.IMAGE


def test_sampler_choices():
    spec = next(s for s in GeneratorFluxPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {
        "euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
    }


# -- build_context ---------------------------------------------------------

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_build_context_parses_resolution_and_params():
    pipe = _make_pipe(resolution="768x1024", steps=25, guidance=4.0, sampler="unipc")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["width"] == 768 and ctx.extra["height"] == 1024
    assert ctx.extra["steps"] == 25
    assert ctx.extra["guidance"] == 4.0
    assert ctx.extra["sampler"] == "unipc"


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_shift_override_applied_to_spec():
    pipe = _make_pipe(shift="3.5")
    ctx = pipe.build_context(_pipe_input())
    gen = ctx.extra["generator"]
    assert gen.spec.sampling_settings["shift"] == 3.5


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_blank_shift_leaves_spec_default():
    pipe = _make_pipe(shift="")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["generator"].spec.sampling_settings["shift"] == 2.02


# -- generate_one / sample contract ---------------------------------------

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_latent_shape_from_engine_helper():
    pipe = _make_pipe(resolution="512x256")
    emitted = []
    pipe.process(_pipe_input(), lambda o: emitted.append(o))
    gen = _FakeGenerator.instances[-1]
    # Comes from gen.latent_shape_for(width, height) — the single owner of the math.
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 256 // 8, 512 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_resolution_snapped_to_granularity():
    # 1080 is not a multiple of 16 (latent 135, odd) -> snapped to 1072 before it
    # reaches sample(), so the patchify never sees a non-divisible axis.
    pipe = _make_pipe(resolution="1920x1080")
    emitted = []
    pipe.process(_pipe_input(), lambda o: emitted.append(o))
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1072 // 8, 1920 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_conditioning_adapted_from_conditioning_model():
    pipe = _make_pipe()
    pipe.process(_pipe_input(with_negative=True), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    conditioning = gen.sample_calls[0]["conditioning"]
    assert "context" in conditioning.cond and "pooled" in conditioning.cond
    assert conditioning.uncond is not None  # negative present


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_empty_negative_yields_no_uncond():
    pipe = _make_pipe()
    pipe.process(_pipe_input(with_negative=False), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["conditioning"].uncond is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_guidance_and_sampler():
    pipe = _make_pipe(guidance=3.5, sampler="dpmpp_2m", steps=12)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["cfg_scale"] == 3.5
    assert call["sampler"] == "dpmpp_2m"
    assert call["steps"] == 12
    assert call["seed"] == 1


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_sampler_options_and_step_cache_options():
    sampler_options = {"eta": 0.5}
    step_cache = {"rel_threshold": 0.12, "warmup_steps": 2}
    pipe = _make_pipe(sampler_options=sampler_options, step_cache=step_cache)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["sampler_options"] == sampler_options
    # preset-facing key is "step_cache"; denoise()/sample() expect "step_cache_options".
    assert call["step_cache_options"] == step_cache


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_sampler_options_and_step_cache_default_to_none():
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["sampler_options"] is None
    assert call["step_cache_options"] is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_schedule_settings():
    schedule_settings = {"schedule": "beta", "schedule_options": {"alpha": 0.6}, "detail_strength": 0.1}
    pipe = _make_pipe(schedule_settings=schedule_settings)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["schedule_settings"] == schedule_settings


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_schedule_settings_defaults_to_none():
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["schedule_settings"] is None


# -- gallery emission ------------------------------------------------------

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_gallery_and_seed_param_emitted():
    pipe = _make_pipe(quantity=2)
    emitted = []
    pipe.process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))

    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert len(gallery[0].images) == 2
    assert all(isinstance(i, ImageGenerationOutput) and i.temporary for i in gallery[0].images)

    seed_param = next(o for o in emitted if isinstance(o, ParamGenerationOutput) and o.name == "seed")
    assert seed_param.values == [5, 6]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_output_image_list_matches_quantity():
    pipe = _make_pipe(quantity=3)
    result = pipe.process(_pipe_input(quantity=3, seeds=(1, 2, 3)), lambda o: None)
    assert len(result.output["image"]) == 3


# -- TE eviction before sampling ----------------------------------
#
# By generator time prompt_encoder has already produced the conditioning, so the
# resident T5-XXL/CLIP-L (or Qwen3) TE is dead weight through sampling+decode.
# The generator releases it via bundle.te_cache_key + MODELS.evict_dead_weight --
# mirrors the qwen / krea2 idle-TE pattern.

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_te_evicted_when_cache_key_and_models_present():
    models = _FakeModelsService()
    pipe = _make_pipe()
    pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_cache_key():
    models = _FakeModelsService()
    pipe = _make_pipe()
    pipe.process(_pipe_input(te_cache_key=None, models=models), lambda o: None)
    assert models.evict_calls == []


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_models_service():
    pipe = _make_pipe()
    # No "MODELS" key in pipe_input at all -- must not raise.
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=None), lambda o: None)
    assert result.output["image"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_failure_does_not_fail_the_generation():
    models = _FakeModelsService(raise_on_evict=True)
    pipe = _make_pipe()
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert result.output["image"]                             # generation still completed
    assert models.evict_calls == ["native/te/x.safetensors"]  # eviction was attempted


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_happens_once_per_generation_call_not_per_seed():
    models = _FakeModelsService()
    pipe = _make_pipe(quantity=3)
    pipe.process(_pipe_input(quantity=3, seeds=(1, 2, 3), te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]  # not 3x


def test_generator_declares_the_models_service_input():
    names = {s.name for s in GeneratorFluxPipe.inputs()}
    assert "MODELS" in names
