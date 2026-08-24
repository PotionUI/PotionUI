"""Tests for the generator/qwen pipe.

`NativeGenerator` / `make_device_plan` are patched with light fakes so the
build_context/generate_one contract, the ConditioningModel->Conditioning
adaptation, the 5D latent-shape delegation, the shift override, and the gallery
emission are exercised without loading weights or running inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.outputs import GalleryGenerationOutput, GenerationExecutionError, ImageGenerationOutput, ParamGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "qwen_image"
    variant: str = "qwen_image"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21"})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 1.15, "guidance": "cfg"})


class _FakeGenerator:
    """Stand-in for NativeGenerator: records sample() args, returns a fake image."""

    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)  # Qwen granularity 16px

    def latent_shape_for(self, width, height, batch=1):
        # Mimics the engine helper for the Qwen/Wan causal-3D VAE: 5D latent.
        return (batch, 16, 1, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encode_image_calls = getattr(self, "encode_image_calls", [])
        self.encode_image_calls.append(pixels)
        h, w = pixels.shape[0], pixels.shape[1]
        return torch.zeros(1, 16, 1, h // 8, w // 8)


def _cond_model(with_negative=True):
    return SimpleNamespace(
        embeds={"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)},
        n_embeds={"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)} if with_negative else {},
    )


def _bundle(te_cache_key=None):
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=20.0),
        te_encoder=object(),
        vae=object(),
        te_cache_key=te_cache_key,
    )


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


def _make_pipe(**over):
    cfg = GeneratorQwenPipe.get_default_config()
    cfg.update(over)
    return GeneratorQwenPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), with_negative=True, te_cache_key=None, models=None):
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
    assert GeneratorQwenPipe.name == "generator"
    inputs = {i.name: i for i in GeneratorQwenPipe.inputs()}
    assert inputs["conditioning"].io_type == IOType.CONDITIONING
    assert inputs["model"].io_type == IOType.MODEL
    assert GeneratorQwenPipe.outputs()[0].io_type == IOType.IMAGE


def test_default_steps_and_guidance():
    cfg = GeneratorQwenPipe.get_default_config()
    assert cfg["steps"] == 20
    assert cfg["guidance"] == 4.0
    assert cfg["sampler"] == "euler"


# -- build_context ---------------------------------------------------------

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_build_context_parses_resolution_and_params():
    pipe = _make_pipe(resolution="768x1024", steps=25, guidance=5.0, sampler="unipc")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["width"] == 768 and ctx.extra["height"] == 1024
    assert ctx.extra["steps"] == 25
    assert ctx.extra["guidance"] == 5.0
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
    assert ctx.extra["generator"].spec.sampling_settings["shift"] == 1.15


# -- generate_one / sample contract ---------------------------------------

@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_latent_shape_is_5d_from_engine_helper():
    pipe = _make_pipe(resolution="512x256")
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    # Comes from gen.latent_shape_for(width, height) — the single owner of the
    # math; Qwen's causal-3D latent is 5D (B, 16, 1, H//8, W//8).
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1, 256 // 8, 512 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_resolution_snapped_to_granularity():
    # 1080 -> 1072 (multiple of 16) before it reaches sample().
    pipe = _make_pipe(resolution="1920x1080")
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1, 1072 // 8, 1920 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_conditioning_carries_uncond_for_true_cfg():
    pipe = _make_pipe()
    pipe.process(_pipe_input(with_negative=True), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    conditioning = gen.sample_calls[0]["conditioning"]
    assert "context" in conditioning.cond and "attention_mask" in conditioning.cond
    assert conditioning.uncond is not None  # true CFG -> uncond present


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_empty_negative_yields_no_uncond():
    pipe = _make_pipe()
    pipe.process(_pipe_input(with_negative=False), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["conditioning"].uncond is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_cfg_and_sampler():
    pipe = _make_pipe(guidance=4.0, sampler="dpmpp_2m", steps=12)
    pipe.process(_pipe_input(), lambda o: None)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["cfg_scale"] == 4.0
    assert call["sampler"] == "dpmpp_2m"
    assert call["steps"] == 12
    assert call["seed"] == 1


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_sample_receives_the_managers_is_cancelled_probe():
    pipe = _make_pipe()
    probe = lambda: False
    pipe.process(_pipe_input(), lambda o: None, is_cancelled=probe)
    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["is_cancelled"] is probe


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


# -- edit mode ---------------------------------------------


def test_edit_is_a_mode_choice():
    mode_spec = next(s for s in GeneratorQwenPipe.configuration() if s.name == "mode")
    assert "edit" in mode_spec.choices


def _edit_pipe_input(quantity=1, seeds=(1,), image=None, images=None, with_negative=True):
    inp = _pipe_input(quantity=quantity, seeds=seeds, with_negative=with_negative)
    if images is not None:
        inp.input["image"] = list(images)
    elif image is not None:
        inp.input["image"] = [image]
    return inp


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_without_source_image_raises():
    pipe = _make_pipe(mode="edit")
    with pytest.raises(GenerationExecutionError):
        pipe.process(_edit_pipe_input(image=None), lambda o: None)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_encodes_the_source_and_builds_ref_latents():
    src = Image.new("RGB", (64, 64), color=(10, 20, 30))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 1  # VAE-encoded exactly once

    call = gen.sample_calls[0]
    ref_latents = call["conditioning"].cond["ref_latents"]
    assert isinstance(ref_latents, list)
    assert len(ref_latents) == 1
    assert isinstance(ref_latents[0], torch.Tensor)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_puts_ref_latents_on_both_cond_and_uncond():
    src = Image.new("RGB", (64, 64), color=(1, 2, 3))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(image=src, with_negative=True), lambda o: None)

    conditioning = _FakeGenerator.instances[-1].sample_calls[0]["conditioning"]
    assert "ref_latents" in conditioning.cond
    assert "ref_latents" in conditioning.uncond
    # Same underlying latent tensor on both sides (one VAE-encode, not two).
    assert conditioning.cond["ref_latents"][0] is conditioning.uncond["ref_latents"][0]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_sample_receives_the_managers_is_cancelled_probe():
    src = Image.new("RGB", (64, 64), color=(1, 2, 3))
    pipe = _make_pipe(mode="edit")
    probe = lambda: False
    pipe.process(_edit_pipe_input(image=src), lambda o: None, is_cancelled=probe)

    call = _FakeGenerator.instances[-1].sample_calls[0]
    assert call["is_cancelled"] is probe


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_no_uncond_when_cfg_off_still_has_cond_ref_latents():
    src = Image.new("RGB", (64, 64), color=(1, 2, 3))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(image=src, with_negative=False), lambda o: None)

    conditioning = _FakeGenerator.instances[-1].sample_calls[0]["conditioning"]
    assert "ref_latents" in conditioning.cond
    assert conditioning.uncond is None


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_resizes_source_to_area_budget_and_snaps():
    # A 2048x1024 source (2MP) should be scaled down toward the ~1MP budget,
    # aspect preserved, then snapped to the 16px granularity.
    src = Image.new("RGB", (2048, 1024), color=(5, 5, 5))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    encoded = gen.encode_image_calls[0]
    h, w = encoded.shape[0], encoded.shape[1]
    assert w < 2048 and h < 1024                 # downscaled
    assert abs((w / h) - 2.0) < 0.05              # aspect preserved (2:1)
    assert w % 16 == 0 and h % 16 == 0            # snapped to granularity

    call = gen.sample_calls[0]
    assert call["latents_shape"] == (1, 16, 1, h // 8, w // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_target_size_ignores_the_shared_resolution_config():
    # A tiny (64x64, well under the 1MP target) square source gets scaled UP to
    # exactly hit EDIT_AREA_TARGET (64*16=1024 on each axis, already a multiple
    # of 16) -- regardless of the unrelated 1920x1080 "resolution" form value,
    # which a naive reuse of the img2img target-size helper would have honored
    # instead (scaling to a 1920x1080-area target, not the fixed edit budget).
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="edit", resolution="1920x1080")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    encoded = gen.encode_image_calls[0]
    assert (encoded.shape[1], encoded.shape[0]) == (1024, 1024)  # (w, h)
    assert gen.sample_calls[0]["latents_shape"] == (1, 16, 1, 1024 // 8, 1024 // 8)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_multi_reference_encodes_each_in_upload_order_anchored_to_first():
    primary = Image.new("RGB", (64, 64), color=(10, 20, 30))       # 1:1, upscaled to the 1MP budget
    second = Image.new("RGB", (32, 96), color=(40, 50, 60))        # 1:3, own aspect
    third = Image.new("RGB", (96, 32), color=(70, 80, 90))         # 3:1, own aspect
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(images=[primary, second, third]), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 3  # one VAE-encode per reference

    call = gen.sample_calls[0]
    ref_latents = call["conditioning"].cond["ref_latents"]
    assert len(ref_latents) == 3

    # Output sizing (latents_shape) is anchored to the FIRST image only, at
    # the fixed 1MP edit budget -- same as the single-image path.
    assert call["latents_shape"] == (1, 16, 1, 1024 // 8, 1024 // 8)

    primary_px, second_px, third_px = gen.encode_image_calls
    assert (primary_px.shape[1], primary_px.shape[0]) == (1024, 1024)

    # Additional references keep THEIR OWN aspect (not forced onto the
    # primary's 1024x1024 canvas): each gets its own area-target resize.
    assert (second_px.shape[1], second_px.shape[0]) != (1024, 1024)
    assert (third_px.shape[1], third_px.shape[0]) != (1024, 1024)
    assert abs((second_px.shape[1] / second_px.shape[0]) - (32 / 96)) < 0.05
    assert abs((third_px.shape[1] / third_px.shape[0]) - (96 / 32)) < 0.05
    assert second_px.shape[0] % 16 == 0 and second_px.shape[1] % 16 == 0
    assert third_px.shape[0] % 16 == 0 and third_px.shape[1] % 16 == 0


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_multi_reference_shared_across_batch_not_indexed():
    """Every output in a batch is conditioned on the FULL reference set --
    the retired per-index pick would instead give output 0 only image 0,
    output 1 only image 1, etc."""
    imgs = [Image.new("RGB", (64, 64), color=(i * 10, i * 10, i * 10)) for i in range(3)]
    pipe = _make_pipe(mode="edit", quantity=2)
    pipe.process(_edit_pipe_input(quantity=2, seeds=(1, 2), images=imgs), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 6  # 3 references x 2 outputs, not 2 (one per output)

    assert len(gen.sample_calls[0]["conditioning"].cond["ref_latents"]) == 3
    assert len(gen.sample_calls[1]["conditioning"].cond["ref_latents"]) == 3

    # Both outputs encoded the identical 3-image set, in the identical order.
    first_output_shapes = [px.shape for px in gen.encode_image_calls[:3]]
    second_output_shapes = [px.shape for px in gen.encode_image_calls[3:]]
    assert first_output_shapes == second_output_shapes


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_txt2img_mode_never_calls_encode_image():
    pipe = _make_pipe(mode="txt2img")
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert getattr(gen, "encode_image_calls", []) == []


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_with_source_present_but_mode_txt2img_ignores_it():
    """A stray `image` input must not accidentally trigger the edit path when
    the mode is txt2img (e.g. an upstream pipe still wired for another mode)."""
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="txt2img")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert getattr(gen, "encode_image_calls", []) == []


# -- TE eviction after prompt_encoder ------------------------------


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_te_evicted_when_cache_key_and_models_present():
    models = _FakeModelsService()
    pipe = _make_pipe(mode="txt2img")
    pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_te_eviction_fires_in_every_mode_not_just_edit():
    """TE is dead weight by generator time regardless of mode -- txt2img and
    img2img carry the exact same waste edit hit first; this must not be
    scoped to edit only."""
    for mode in ("txt2img", "img2img", "edit"):
        models = _FakeModelsService()
        pipe = _make_pipe(mode=mode)
        src = Image.new("RGB", (64, 64), color=(1, 1, 1)) if mode != "txt2img" else None
        inp = _pipe_input(te_cache_key="native/te/x.safetensors", models=models)
        if src is not None:
            inp.input["image"] = [src]
        pipe.process(inp, lambda o: None)
        assert models.evict_calls == ["native/te/x.safetensors"], f"mode={mode}"


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_cache_key():
    models = _FakeModelsService()
    pipe = _make_pipe(mode="txt2img")
    pipe.process(_pipe_input(te_cache_key=None, models=models), lambda o: None)
    assert models.evict_calls == []


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_no_eviction_without_a_models_service():
    pipe = _make_pipe(mode="txt2img")
    # No "MODELS" key in pipe_input at all -- must not raise.
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=None), lambda o: None)
    assert result.output["image"]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_failure_does_not_fail_the_generation():
    models = _FakeModelsService(raise_on_evict=True)
    pipe = _make_pipe(mode="txt2img")
    result = pipe.process(_pipe_input(te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert result.output["image"]           # generation still completed
    assert models.evict_calls == ["native/te/x.safetensors"]  # eviction was attempted


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_eviction_happens_once_per_generation_call_not_per_seed():
    models = _FakeModelsService()
    pipe = _make_pipe(mode="txt2img", quantity=3)
    pipe.process(_pipe_input(quantity=3, seeds=(1, 2, 3), te_cache_key="native/te/x.safetensors", models=models), lambda o: None)
    assert models.evict_calls == ["native/te/x.safetensors"]  # not 3x
