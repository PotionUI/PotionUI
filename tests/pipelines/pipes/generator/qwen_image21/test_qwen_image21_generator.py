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
from src.pipelines.pipes.generator.qwen_image21.main import GeneratorQwenImage21Pipe


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "qwen_image21"
    variant: str = "qwen_image21"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 64, "format": "qwen_image21"})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 0.69, "guidance": "cfg"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        self.sample_calls = []
        self._decode_channels = 4
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 16, 1)  # Qwen-Image-2.1 granularity 16px (no patchify)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 64, 1, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, self._decode_channels), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encode_image_calls = getattr(self, "encode_image_calls", [])
        self.encode_image_calls.append(pixels)
        h, w = pixels.shape[0], pixels.shape[1]
        return torch.zeros(1, 64, 1, h // 16, w // 16)


def _cond_model(with_negative=True, image_slots=None):
    embeds = {"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)}
    n_embeds = {"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)} if with_negative else {}
    if image_slots is not None:
        embeds["image_slots"] = image_slots
        if with_negative:
            n_embeds["image_slots"] = image_slots
    return SimpleNamespace(embeds=embeds, n_embeds=n_embeds)


def _bundle():
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=33.0),
        te_encoder=object(),
        vae=object(),
    )


def _make_pipe(**over):
    cfg = GeneratorQwenImage21Pipe.get_default_config()
    cfg.update(over)
    return GeneratorQwenImage21Pipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), with_negative=True):
    inp = {
        "model": _bundle(),
        "conditioning": [_cond_model(with_negative) for _ in range(quantity)],
        "seed": list(seeds),
    }
    return PipeInput(input=inp)


def _edit_pipe_input(quantity=1, seeds=(1,), image=None, images=None, with_negative=True, image_slots=None):
    inp = {
        "model": _bundle(),
        "conditioning": [_cond_model(with_negative, image_slots=image_slots) for _ in range(quantity)],
        "seed": list(seeds),
    }
    if images is not None:
        inp["image"] = list(images)
    elif image is not None:
        inp["image"] = [image]
    return PipeInput(input=inp)


def setup_function(_):
    _FakeGenerator.instances.clear()


def test_name_inputs_outputs():
    assert GeneratorQwenImage21Pipe.name == "generator"
    inputs = {i.name: i for i in GeneratorQwenImage21Pipe.inputs()}
    assert inputs["conditioning"].io_type == IOType.CONDITIONING
    assert inputs["model"].io_type == IOType.MODEL
    assert GeneratorQwenImage21Pipe.outputs()[0].io_type == IOType.IMAGE


def test_default_steps_and_guidance():
    cfg = GeneratorQwenImage21Pipe.get_default_config()
    assert cfg["steps"] == 40
    assert cfg["guidance"] == 4.0
    assert cfg["sampler"] == "euler"


def test_mode_choices_are_txt2img_and_edit():
    mode_spec = next(s for s in GeneratorQwenImage21Pipe.configuration() if s.name == "mode")
    assert mode_spec.choices == ["txt2img", "edit"]


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
    pipe = _make_pipe(shift="1.5")
    ctx = pipe.build_context(_pipe_input())
    gen = ctx.extra["generator"]
    assert gen.spec.sampling_settings["shift"] == 1.5


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_blank_shift_leaves_spec_default():
    pipe = _make_pipe(shift="")
    ctx = pipe.build_context(_pipe_input())
    assert ctx.extra["generator"].spec.sampling_settings["shift"] == 0.69


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_latent_shape_from_engine_helper():
    pipe = _make_pipe(resolution="512x256")
    pipe.process(_pipe_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert gen.sample_calls[0]["latents_shape"] == (1, 64, 1, 256 // 16, 512 // 16)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_conditioning_carries_uncond_for_true_cfg():
    pipe = _make_pipe()
    pipe.process(_pipe_input(with_negative=True), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    conditioning = gen.sample_calls[0]["conditioning"]
    assert "context" in conditioning.cond and "attention_mask" in conditioning.cond
    assert conditioning.uncond is not None


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


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_rgba_decode_is_alpha_dropped_to_rgb():
    pipe = _make_pipe()
    result = pipe.process(_pipe_input(), lambda o: None)
    image = result.output["image"][0]
    assert image.mode == "RGB"
    assert image.getbands() == ("R", "G", "B")


class _RgbGenerator(_FakeGenerator):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._decode_channels = 3


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _RgbGenerator)
def test_rgb_decode_stays_rgb():
    pipe = _make_pipe()
    result = pipe.process(_pipe_input(), lambda o: None)
    image = result.output["image"][0]
    assert image.mode == "RGB"


# -- edit mode ---------------------------------------------


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
    assert len(gen.encode_image_calls) == 1

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
    assert conditioning.cond["ref_latents"][0] is conditioning.uncond["ref_latents"][0]


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_image_slots_forced_identical_on_cond_and_uncond():
    src = Image.new("RGB", (64, 64), color=(1, 2, 3))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(image=src, with_negative=True, image_slots=[2]), lambda o: None)

    conditioning = _FakeGenerator.instances[-1].sample_calls[0]["conditioning"]
    assert conditioning.cond["image_slots"] == [2]
    assert conditioning.uncond["image_slots"] == [2]


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
    assert w < 2048 and h < 1024
    assert abs((w / h) - 2.0) < 0.05
    assert w % 16 == 0 and h % 16 == 0

    call = gen.sample_calls[0]
    assert call["latents_shape"] == (1, 64, 1, h // 16, w // 16)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_target_size_ignores_the_shared_resolution_config():
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="edit", resolution="1920x1080")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    encoded = gen.encode_image_calls[0]
    assert (encoded.shape[1], encoded.shape[0]) == (1024, 1024)  # (w, h)
    assert gen.sample_calls[0]["latents_shape"] == (1, 64, 1, 1024 // 16, 1024 // 16)


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_multi_reference_encodes_each_in_upload_order_anchored_to_first():
    primary = Image.new("RGB", (64, 64), color=(10, 20, 30))
    second = Image.new("RGB", (32, 96), color=(40, 50, 60))
    third = Image.new("RGB", (96, 32), color=(70, 80, 90))
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(images=[primary, second, third]), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 3

    call = gen.sample_calls[0]
    ref_latents = call["conditioning"].cond["ref_latents"]
    assert len(ref_latents) == 3
    assert call["latents_shape"] == (1, 64, 1, 1024 // 16, 1024 // 16)

    primary_px, second_px, third_px = gen.encode_image_calls
    assert (primary_px.shape[1], primary_px.shape[0]) == (1024, 1024)
    assert (second_px.shape[1], second_px.shape[0]) != (1024, 1024)
    assert (third_px.shape[1], third_px.shape[0]) != (1024, 1024)
    assert abs((second_px.shape[1] / second_px.shape[0]) - (32 / 96)) < 0.05
    assert abs((third_px.shape[1] / third_px.shape[0]) - (96 / 32)) < 0.05
    assert second_px.shape[0] % 16 == 0 and second_px.shape[1] % 16 == 0
    assert third_px.shape[0] % 16 == 0 and third_px.shape[1] % 16 == 0


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_reference_count_is_capped_at_ten():
    imgs = [Image.new("RGB", (16, 16), color=(i, i, i)) for i in range(14)]
    pipe = _make_pipe(mode="edit")
    pipe.process(_edit_pipe_input(images=imgs), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 10
    assert len(gen.sample_calls[0]["conditioning"].cond["ref_latents"]) == 10


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_multi_reference_shared_across_batch_not_indexed():
    imgs = [Image.new("RGB", (64, 64), color=(i * 10, i * 10, i * 10)) for i in range(3)]
    pipe = _make_pipe(mode="edit", quantity=2)
    pipe.process(_edit_pipe_input(quantity=2, seeds=(1, 2), images=imgs), lambda o: None)

    gen = _FakeGenerator.instances[-1]
    assert len(gen.encode_image_calls) == 6

    assert len(gen.sample_calls[0]["conditioning"].cond["ref_latents"]) == 3
    assert len(gen.sample_calls[1]["conditioning"].cond["ref_latents"]) == 3

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
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="txt2img")
    pipe.process(_edit_pipe_input(image=src), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert getattr(gen, "encode_image_calls", []) == []


@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None)
@patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator)
def test_edit_mode_output_is_rgb():
    src = Image.new("RGB", (64, 64), color=(1, 1, 1))
    pipe = _make_pipe(mode="edit")
    result = pipe.process(_edit_pipe_input(image=src), lambda o: None)
    image = result.output["image"][0]
    assert image.mode == "RGB"
