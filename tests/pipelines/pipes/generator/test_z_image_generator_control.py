from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.generator.z_image.control import (
    control_canvas,
    encode_control_context,
    first_image,
)

from .test_z_image_generator import _FakeGenerator, _cond_model

_PATCHES = (
    patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None),
    patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator),
)


class _PoolingGenerator(_FakeGenerator):
    def encode_image(self, pixels, **_):
        self.encode_image_calls = getattr(self, "encode_image_calls", [])
        self.encode_image_calls.append(pixels)
        pooled = F.avg_pool2d(pixels[:, :1].float(), 8)
        return pooled.expand(-1, 16, -1, -1).clone()


def _solid(width, height, value):
    return Image.new("RGB", (width, height), (value, value, value))


def _bundle(with_control=True):
    module = SimpleNamespace(fun_control=object() if with_control else None)
    return SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=12.0, module=module), te_encoder=object(), vae=object())


def _pipe(**over):
    from src.pipelines.pipes.generator.z_image.main import GeneratorZImagePipe

    cfg = GeneratorZImagePipe.get_default_config()
    cfg.update({"mode": "control", **over})
    return GeneratorZImagePipe(config=cfg)


def _input(guide=None, with_control=True):
    return PipeInput(input={
        "model": _bundle(with_control),
        "conditioning": [_cond_model()],
        "seed": [1],
        "control_image": [guide] if guide is not None else None,
    })


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    _FakeGenerator.instances.clear()
    for p in _PATCHES:
        p.start()
    monkeypatch.setattr(_FakeGenerator, "encode_image", _PoolingGenerator.encode_image, raising=False)
    yield
    for p in _PATCHES:
        p.stop()


def test_a_control_image_fills_the_control_channels_and_zero_pads_the_rest():
    gen = _PoolingGenerator(None, None, None)
    context = encode_control_context(gen, 64, 32, _solid(64, 32, 255))
    assert context.shape == (1, 33, 4, 8)
    assert torch.allclose(context[:, :16], torch.ones(1, 16, 4, 8))
    assert torch.equal(context[:, 16:], torch.zeros(1, 17, 4, 8))


def test_without_a_control_image_the_context_is_all_zeros():
    context = encode_control_context(_PoolingGenerator(None, None, None), 64, 32, None)
    assert torch.equal(context, torch.zeros(1, 33, 4, 8))


def test_the_control_image_is_resized_to_the_canvas():
    gen = _PoolingGenerator(None, None, None)
    encode_control_context(gen, 64, 32, _solid(100, 30, 0))
    assert [tuple(p.shape[-2:]) for p in gen.encode_image_calls] == [(32, 64)]


def test_first_image_unwraps_lists_and_arrays():
    image = _solid(4, 4, 0)
    assert first_image([image]) is image
    assert first_image([]) is None
    assert first_image(None) is None
    assert isinstance(first_image(torch.zeros(1).numpy().reshape(1, 1).astype("uint8")), Image.Image)


def test_the_canvas_follows_the_guide_aspect_at_the_requested_area():
    gen = _FakeGenerator(None, None, None)
    assert control_canvas(gen, 1024, 1024, None) == (1024, 1024)
    width, height = control_canvas(gen, 1024, 1024, _solid(2000, 1000, 0))
    assert width > height and abs(width * height - 1024 * 1024) < 0.1 * 1024 * 1024


def test_control_mode_is_declared():
    from src.pipelines.pipes.generator.z_image.main import GeneratorZImagePipe

    mode = next(s for s in GeneratorZImagePipe.configuration() if s.name == "mode")
    assert "control" in mode.choices
    assert {"control_strength", "control_start", "control_end"} <= {s.name for s in GeneratorZImagePipe.configuration()}
    assert "control_image" in {s.name for s in GeneratorZImagePipe.inputs()}


def test_control_without_the_fun_controlnet_loaded_is_refused():
    with pytest.raises(GenerationExecutionError, match="Fun ControlNet"):
        _pipe().build_context(_input(_solid(32, 32, 0), with_control=False))


def test_control_without_a_guide_is_refused():
    with pytest.raises(GenerationExecutionError, match="Guide"):
        _pipe().build_context(_input(None))


def test_control_mode_sets_the_canvas_and_the_sigma_window():
    pipe = _pipe(control_strength=0.8, control_start=0.2, control_end=0.9, resolution="1024x1024")
    ctx = pipe.build_context(_input(_solid(200, 100, 0)))
    assert ctx.extra["width"] > ctx.extra["height"]
    assert ctx.extra["control_strength"] == 0.8
    high, low = ctx.extra["control_sigma_range"]
    assert 1.0 > high > low > 0.0
    assert ctx.extra["iterate_mode"] is False


def test_the_full_window_covers_every_sigma():
    ctx = _pipe().build_context(_input(_solid(64, 64, 0)))
    assert ctx.extra["control_sigma_range"] == (1.0, 0.0)


def test_both_guidance_branches_receive_the_control_context():
    pipe = _pipe(control_strength=0.7)
    ctx = pipe.build_context(_input(_solid(64, 64, 0)))
    gen = ctx.extra["generator"]
    gen.encode_image = _PoolingGenerator.encode_image.__get__(gen)
    cond, uncond = pipe.attach_conditioning(ctx, {"context": 1}, {"context": 2})
    assert cond["control_context"] is uncond["control_context"]
    assert cond["control_context"].shape[1] == 33
    assert cond["control_context_scale"] == uncond["control_context_scale"] == 0.7
    assert cond["control_sigma_range"] == (1.0, 0.0)


def test_the_guide_is_encoded_once_per_generation():
    pipe = _pipe()
    ctx = pipe.build_context(_input(_solid(64, 64, 0)))
    gen = ctx.extra["generator"]
    gen.encode_image = _PoolingGenerator.encode_image.__get__(gen)
    pipe.attach_conditioning(ctx, {}, None)
    pipe.attach_conditioning(ctx, {}, None)
    assert len(gen.encode_image_calls) == 1


def test_other_modes_leave_the_conditioning_alone():
    pipe = _pipe(mode="txt2img")
    ctx = pipe.build_context(_input(None))
    cond, uncond = pipe.attach_conditioning(ctx, {"context": 1}, {"context": 2})
    assert cond == {"context": 1} and uncond == {"context": 2}
