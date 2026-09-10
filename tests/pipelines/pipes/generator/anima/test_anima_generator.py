"""Tests for the generator/anima pipe.

Two concerns: (1) ``AnimaNativeGenerator._make_forward`` threads the LLMAdapter's
T5 target ids/weights into the DiT (the piece the generic engine ``model_forward``
does NOT do), passing sigma through unscaled (the DiT applies the x1000 itself);
(2) the build_context/generate_one contract, ConditioningModel->Conditioning
adaptation, 5D latent-shape delegation, and gallery emission — exercised with a
fake generator so no weights load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from src.pipelines.outputs import GalleryGenerationOutput, ImageGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.anima.main import AnimaNativeGenerator, GeneratorAnimaPipe


# -- _make_forward threads the T5 tensors ----------------------------------

def test_make_forward_threads_t5_and_passes_sigma_through():
    recorded = {}

    class _RecordingDiT:
        def __call__(self, x, sigma, context, t5xxl_ids=None, t5xxl_weights=None):
            recorded.update(x=x, sigma=sigma, context=context,
                            t5xxl_ids=t5xxl_ids, t5xxl_weights=t5xxl_weights)
            return torch.zeros_like(x)

    dit = SimpleNamespace(module=_RecordingDiT(), spec=SimpleNamespace(sampling_settings={}),
                          compute_dtype=torch.float32)
    gen = AnimaNativeGenerator(dit, None, SimpleNamespace(estimated_vram_gb=0.1), None)

    model_forward = gen._make_forward("cpu", torch.float32)
    x = torch.randn(1, 16, 1, 8, 8)
    sigma = torch.tensor([0.6])
    cond = {
        "context": torch.randn(1, 5, 16),
        "t5xxl_ids": torch.randint(0, 100, (1, 7)),
        "t5xxl_weights": torch.ones(1, 7),
    }
    out = model_forward(x, sigma, cond)
    assert out.shape == x.shape
    assert recorded["t5xxl_ids"] is cond["t5xxl_ids"]           # threaded, not dropped
    assert recorded["t5xxl_weights"] is cond["t5xxl_weights"]
    assert torch.equal(recorded["sigma"], sigma)               # DiT scales x1000 itself


# -- pipe build_context / generate_one -------------------------------------

@dataclass(frozen=True)
class _FakeSpec:
    family: str = "anima"
    variant: str = "anima"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21"})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 3.0, "guidance": "cfg"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)  # Anima granularity 16px

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, 1, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


def _cond_model(with_negative=True):
    embeds = {"context": torch.ones(1, 4, 8), "t5xxl_ids": torch.ones(1, 4, dtype=torch.long),
              "t5xxl_weights": torch.ones(1, 4)}
    return SimpleNamespace(embeds=embeds, n_embeds=dict(embeds) if with_negative else {})


def _bundle():
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=6.0), te_encoder=object(), vae=object(),
    )


def _make_pipe(**over):
    cfg = GeneratorAnimaPipe.get_default_config()
    cfg.update(over)
    return GeneratorAnimaPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), with_negative=True):
    inp = {
        "model": _bundle(),
        "conditioning": [_cond_model(with_negative) for _ in range(quantity)],
        "seed": list(seeds),
    }
    return PipeInput(input=inp)


def _outputs(pipe, pi):
    collected = []
    pipe.process(pi, lambda o: collected.append(o))
    return collected


def test_name_and_outputs():
    assert GeneratorAnimaPipe.name == "generator"
    assert {o.name: o.io_type for o in GeneratorAnimaPipe.outputs()}["image"] == IOType.IMAGE


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_generate_one_builds_conditioning_and_emits_image():
    _FakeGenerator.instances.clear()
    pipe = _make_pipe(quantity=1, steps=5, guidance=6.0, resolution="512x512")
    outs = _outputs(pipe, _pipe_input(quantity=1, seeds=(42,)))

    gallery = [o for o in outs if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    images = gallery[0].images
    assert len(images) == 1 and isinstance(images[0], ImageGenerationOutput)
    assert images[0].seed == 42

    gen = _FakeGenerator.instances[-1]
    call = gen.sample_calls[0]
    assert call["latents_shape"] == (1, 16, 1, 64, 64)          # 5D delegated shape
    assert call["steps"] == 5 and call["cfg_scale"] == 6.0
    # The Conditioning carries the four-key cond dict (incl. the T5 tensors).
    assert "t5xxl_ids" in call["conditioning"].cond
    assert call["conditioning"].uncond is not None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_resolution_snapped_to_granularity():
    _FakeGenerator.instances.clear()
    pipe = _make_pipe(resolution="1920x1080")   # 1080 not a multiple of 16
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1, 1072 // 8, 1920 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_shift_override_applied_to_spec():
    _FakeGenerator.instances.clear()
    pipe = _make_pipe(shift=2.5)
    pipe.build_context(_pipe_input())
    assert _FakeGenerator.instances[-1].spec.sampling_settings["shift"] == 2.5


# -- step_cache config -------------------------------------------


def test_step_cache_declared_in_configuration():
    spec = next(s for s in GeneratorAnimaPipe.configuration() if s.name == "step_cache")
    assert spec.param_type is dict


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_sample_receives_step_cache_options():
    _FakeGenerator.instances.clear()
    step_cache = {"rel_threshold": 0.12, "warmup_steps": 2}
    pipe = _make_pipe(step_cache=step_cache)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] == step_cache


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_step_cache_defaults_to_none():
    _FakeGenerator.instances.clear()
    pipe = _make_pipe()
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["step_cache_options"] is None



# -- schedule / schedule_options (flat sigma-schedule knobs) ---------------
#
# schedule_settings_config_specs()/schedule_settings_overrides()
# (guidance_options.py) are the same flat knobs the Wan/LTX video pipes
# expose; apply_schedule_settings merges their resolved values into
# ctx.extra["schedule_settings"], which NativeGenerator.sample whitelists
# through to build_sigmas.

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_schedule_beta_with_options_lands_in_schedule_settings():
    pipe = _make_pipe(schedule="beta", schedule_options={"alpha": 0.5, "beta": 0.7})
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["schedule_settings"] == {
        "schedule": "beta",
        "schedule_options": {"alpha": 0.5, "beta": 0.7},
    }


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
def test_schedule_shift_reaches_schedule_settings_verbatim():
    # "shift" is the Schedule field's default and a real registry key, so it
    # travels as a value rather than being dropped; build_sigmas resolves it to
    # the model's own ramp (see test_guidance_options.py).
    pipe = _make_pipe(schedule="shift")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["schedule_settings"] == {"schedule": "shift"}


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
@patch("src.pipelines.pipes.generator.anima.main.AnimaNativeGenerator", _FakeGenerator)
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
    assert not hasattr(GeneratorAnimaPipe, "_release_idle_te")
    assert "MODELS" not in {s.name for s in GeneratorAnimaPipe.inputs()}
