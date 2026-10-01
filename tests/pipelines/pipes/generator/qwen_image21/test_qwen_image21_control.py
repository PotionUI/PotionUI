from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.generator.qwen_image21.control import (
    control_canvas,
    encode_control_context,
    first_image,
)

from .test_qwen_image21_generator import _FakeGenerator, _bundle, _cond_model, _make_pipe

_PATCHES = (
    patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None),
)


class _PoolingGenerator(_FakeGenerator):
    def encode_image(self, pixels, **_):
        self.encode_image_calls = getattr(self, "encode_image_calls", [])
        self.encode_image_calls.append(pixels)
        pooled = F.avg_pool2d(pixels[:, :1].float(), 16)
        return pooled.unsqueeze(2).expand(-1, 64, -1, -1, -1).clone()


def _gen():
    return _PoolingGenerator(None, None, None)


def _solid(width, height, value):
    return Image.new("RGB", (width, height), (value, value, value))


def _half_mask(width, height):
    mask = Image.new("L", (width, height), 0)
    mask.paste(255, (0, 0, width // 2, height))
    return mask


def test_a_control_image_fills_the_control_channels_and_zero_pads_the_rest():
    context = encode_control_context(_gen(), 64, 32, _solid(64, 32, 255), None, None)
    assert context.shape == (1, 129, 1, 2, 4)
    assert torch.allclose(context[:, :64], torch.ones(1, 64, 1, 2, 4))
    assert torch.equal(context[:, 64:], torch.zeros(1, 65, 1, 2, 4))


def test_inpaint_greys_the_repainted_region_and_keeps_the_rest():
    gen = _gen()
    context = encode_control_context(gen, 64, 32, None, _solid(64, 32, 255), _half_mask(64, 32))
    assert torch.equal(context[:, :64], torch.zeros(1, 64, 1, 2, 4))
    keep = context[0, 64, 0]
    assert torch.equal(keep, torch.tensor([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]]))
    inpaint = context[0, 65, 0]
    assert torch.equal(inpaint[:, :2], torch.zeros(2, 2))
    assert torch.allclose(inpaint[:, 2:], torch.ones(2, 2))
    masked_pixels = gen.encode_image_calls[0]
    assert masked_pixels.shape == (1, 3, 32, 64)
    assert torch.equal(masked_pixels[..., :32], torch.zeros(1, 3, 32, 32))


def test_inpaint_without_a_mask_repaints_everything():
    context = encode_control_context(_gen(), 32, 32, None, _solid(32, 32, 255), None)
    assert torch.equal(context[:, 64], torch.zeros(1, 1, 2, 2))
    assert torch.equal(context[:, 65:], torch.zeros(1, 64, 1, 2, 2))


def test_inputs_are_resized_to_the_canvas():
    gen = _gen()
    encode_control_context(gen, 64, 32, _solid(100, 30, 0), _solid(10, 90, 0), _half_mask(7, 7))
    assert [tuple(p.shape[-2:]) for p in gen.encode_image_calls] == [(32, 64), (32, 64)]


def test_transparent_control_images_are_flattened_to_rgb():
    gen = _gen()
    rgba = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    encode_control_context(gen, 32, 32, rgba, None, None)
    assert gen.encode_image_calls[0].shape[1] == 3
    assert torch.equal(gen.encode_image_calls[0], torch.ones(1, 3, 32, 32))


def test_the_canvas_takes_the_guide_aspect_at_the_requested_area():
    assert control_canvas(_gen(), 1024, 1024, _solid(800, 1200, 0)) == (832, 1248)
    assert control_canvas(_gen(), 1024, 1024, None) == (1024, 1024)


def test_first_image_takes_lists_and_arrays():
    image = _solid(4, 4, 0)
    assert first_image([image, _solid(2, 2, 0)]) is image
    assert first_image([]) is None
    assert first_image(None) is None
    assert first_image(np.zeros((4, 4, 3), dtype=np.uint8)).size == (4, 4)


def _controlled_bundle():
    bundle = _bundle()
    bundle.dit.module = SimpleNamespace(fun_control=object())
    return bundle


def _control_input(quantity=1, bundle=None, **images):
    inp = {
        "model": bundle or _controlled_bundle(),
        "conditioning": [_cond_model(True) for _ in range(quantity)],
        "seed": list(range(1, quantity + 1)),
    }
    inp.update({k: [v] for k, v in images.items()})
    return PipeInput(input=inp)


def _run(pipe_input, **config):
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
         patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _PoolingGenerator):
        pipe = _make_pipe(mode="control", resolution="64x64", **config)
        pipe.process(pipe_input, lambda o: None)
    return _PoolingGenerator.instances[-1]


def setup_function(_):
    _FakeGenerator.instances.clear()


def test_control_mode_sends_the_control_to_both_guidance_passes():
    gen = _run(_control_input(control_image=_solid(64, 64, 255)), control_strength=0.7)
    conditioning = gen.sample_calls[0]["conditioning"]
    assert conditioning.cond["control_context"].shape == (1, 129, 1, 4, 4)
    assert conditioning.uncond["control_context"] is conditioning.cond["control_context"]
    assert conditioning.cond["control_context_scale"] == 0.7
    assert conditioning.uncond["control_context_scale"] == 0.7


def test_the_control_is_encoded_once_for_a_batch():
    gen = _run(_control_input(quantity=3, control_image=_solid(64, 64, 255)), quantity=3)
    assert len(gen.sample_calls) == 3
    assert len(gen.encode_image_calls) == 1
    first = gen.sample_calls[0]["conditioning"].cond["control_context"]
    assert all(c["conditioning"].cond["control_context"] is first for c in gen.sample_calls)


def test_inpaint_follows_the_source_image_shape():
    gen = _run(_control_input(inpaint_image=_solid(128, 64, 255), inpaint_mask=_half_mask(128, 64)))
    assert gen.sample_calls[0]["latents_shape"] == (1, 64, 1, 3, 6)
    assert gen.sample_calls[0]["warm_start"] is False


def test_control_mode_needs_an_image():
    with pytest.raises(GenerationExecutionError, match="Choose a Guide"):
        _run(_control_input())


def test_txt2img_never_carries_a_control():
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
         patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _PoolingGenerator):
        pipe = _make_pipe(resolution="64x64")
        pipe.process(_control_input(control_image=_solid(64, 64, 255)), lambda o: None)
    conditioning = _PoolingGenerator.instances[-1].sample_calls[0]["conditioning"]
    assert set(conditioning.cond) == {"context", "attention_mask"}
    assert set(conditioning.uncond) == {"context", "attention_mask"}


def _context(gen):
    return gen.sample_calls[0]["conditioning"].cond["control_context"][0, :, 0]


def test_a_guide_alone_drives_structure_without_inpainting():
    main = _solid(64, 64, 255)
    gen = _run(_control_input(image=main, control_image=main))
    context = _context(gen)
    assert torch.allclose(context[:64], torch.ones(64, 4, 4))
    assert torch.equal(context[64:], torch.zeros(65, 4, 4))


def test_inpaint_alone_keeps_the_unmasked_area_without_a_guide():
    main = _solid(64, 64, 255)
    gen = _run(_control_input(image=main, inpaint_image=main, inpaint_mask=_half_mask(64, 64)))
    context = _context(gen)
    assert torch.equal(context[:64], torch.zeros(64, 4, 4))
    assert torch.equal(context[64], torch.tensor([[0.0, 0.0, 1.0, 1.0]] * 4))
    assert torch.equal(context[65, :, :2], torch.zeros(4, 2))
    assert torch.allclose(context[65, :, 2:], torch.ones(4, 2))


def test_inpaint_with_a_guide_from_the_same_image_fills_every_part():
    main = _solid(64, 64, 255)
    gen = _run(_control_input(image=main, control_image=main, inpaint_image=main, inpaint_mask=_half_mask(64, 64)))
    context = _context(gen)
    assert torch.allclose(context[:64], torch.ones(64, 4, 4))
    assert torch.equal(context[64], torch.tensor([[0.0, 0.0, 1.0, 1.0]] * 4))
    assert torch.allclose(context[65, :, 2:], torch.ones(4, 2))


def test_a_guide_from_a_different_image_keeps_the_main_image_canvas():
    gen = _run(_control_input(image=_solid(128, 64, 255), control_image=_solid(64, 128, 0)))
    assert gen.sample_calls[0]["latents_shape"] == (1, 64, 1, 3, 6)
    context = _context(gen)
    assert torch.allclose(context[:64], -torch.ones(64, 3, 6))
    assert torch.equal(context[64:], torch.zeros(65, 3, 6))
    assert gen.encode_image_calls[0].shape[-2:] == (48, 96)


def test_the_default_window_covers_every_step():
    gen = _run(_control_input(image=_solid(64, 64, 255), control_image=_solid(64, 64, 255)))
    conditioning = gen.sample_calls[0]["conditioning"]
    assert conditioning.cond["control_sigma_range"] == (1.0, 0.0)
    assert conditioning.uncond["control_sigma_range"] == (1.0, 0.0)


def test_the_window_is_mapped_through_the_model_schedule():
    from src.platform.runtime.native.sampling.flow_schedule import percent_to_sigma

    gen = _run(_control_input(image=_solid(64, 64, 255), control_image=_solid(64, 64, 255)),
               control_start=0.2, control_end=0.7)
    settings = gen.spec.sampling_settings
    expected = (percent_to_sigma(0.2, settings, 16), percent_to_sigma(0.7, settings, 16))
    assert gen.sample_calls[0]["conditioning"].cond["control_sigma_range"] == expected
    assert 1.0 > expected[0] > expected[1] > 0.0


def test_control_without_the_fun_controlnet_loaded_is_refused():
    with pytest.raises(GenerationExecutionError, match="The Fun ControlNet model isn't loaded"):
        _run(_control_input(bundle=_bundle(), image=_solid(64, 64, 255), control_image=_solid(64, 64, 255)))
    assert _PoolingGenerator.instances == [] or not _PoolingGenerator.instances[-1].sample_calls
