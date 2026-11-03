from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.imaging.fit import PAD_FILL
from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe
from src.pipelines.pipes.generator.qwen_image21.main import GeneratorQwenImage21Pipe
from src.platform.runtime.native.resolution import snap_resolution


@dataclass(frozen=True)
class _Spec:
    family: str = "qwen"
    variant: str = "qwen"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 1.0, "guidance": "cfg"})


class _Gen:
    last: "_Gen" = None

    def __init__(self, *_, **__):
        self.spec = _Spec()
        self.encoded = []
        self.shapes = []
        _Gen.last = self

    def snap_resolution(self, width, height):
        return snap_resolution(width, height, 16, 1)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, 1, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, **_):
        self.shapes.append(latents_shape)
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encoded.append(np.array(pixels))
        return torch.zeros(1, 16, 1, 1, 1)


PIPES = [GeneratorQwenImage21Pipe, GeneratorQwenPipe]


def _run(pipe_cls, images, drop_fit=False, **over):
    cfg = pipe_cls.get_default_config()
    cfg.update({"mode": "edit", **over})
    if drop_fit:
        cfg.pop("fit", None)
    cond = SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.ones(1, 4, 8)})
    bundle = SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=1.0), te_encoder=object(), vae=object())
    inp = PipeInput(input={"model": bundle, "conditioning": [cond], "seed": [1], "image": list(images)})
    outputs = []
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _Gen):
        pipe_cls(config=cfg).process(inp, outputs.append)
    return _Gen.last


def _refs():
    first = Image.new("RGB", (1200, 600), (200, 30, 30))
    first.paste((30, 200, 30), (0, 0, 300, 600))
    return [first, Image.new("RGB", (500, 700), (10, 10, 240)), Image.new("RGB", (900, 900), (90, 90, 90))]


def _size(pixels):
    return pixels.shape[1], pixels.shape[0]


def test_fit_is_a_declared_choice_defaulting_to_match():
    for pipe_cls in PIPES:
        spec = next(s for s in pipe_cls.configuration() if s.name == "fit")
        assert spec.default == "match"
        assert set(spec.choices) == {"match", "crop", "pad"}


@pytest.mark.parametrize("pipe_cls", PIPES)
def test_match_is_identical_to_the_behaviour_without_the_key(pipe_cls):
    today = _run(pipe_cls, _refs(), drop_fit=True)
    match = _run(pipe_cls, _refs(), fit="match", resolution="1664x928")
    assert match.shapes == today.shapes
    assert len(match.encoded) == len(today.encoded) == 3
    for a, b in zip(match.encoded, today.encoded):
        assert np.array_equal(a, b)
    assert _size(match.encoded[0]) == (1440, 720)


@pytest.mark.parametrize("pipe_cls", PIPES)
@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_a_target_sets_the_latent_and_the_first_image(pipe_cls, fit):
    gen = _run(pipe_cls, _refs(), fit=fit, resolution="1664x928")
    assert gen.shapes == [(1, 16, 1, 928 // 16, 1664 // 16)]
    assert _size(gen.encoded[0]) == (1664, 928)


@pytest.mark.parametrize("pipe_cls", PIPES)
@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_a_target_leaves_the_other_references_as_they_were(pipe_cls, fit):
    today = _run(pipe_cls, _refs(), drop_fit=True)
    gen = _run(pipe_cls, _refs(), fit=fit, resolution="1024x1024")
    assert len(gen.encoded) == 3
    for a, b in zip(gen.encoded[1:], today.encoded[1:]):
        assert np.array_equal(a, b)


@pytest.mark.parametrize("pipe_cls", PIPES)
def test_the_target_is_snapped_to_the_model_grid(pipe_cls):
    gen = _run(pipe_cls, _refs(), fit="crop", resolution="1000x570")
    width, height = snap_resolution(1000, 570, 16, 1)
    assert (width, height) != (1000, 570)
    assert _size(gen.encoded[0]) == (width, height)
    assert gen.shapes == [(1, 16, 1, height // 16, width // 16)]


@pytest.mark.parametrize("pipe_cls", PIPES)
def test_crop_drops_the_edges_of_a_wider_first_image(pipe_cls):
    gen = _run(pipe_cls, _refs(), fit="crop", resolution="1024x1024")
    first = gen.encoded[0]
    assert tuple(first[512, 4]) == (200, 30, 30)
    assert tuple(first[512, 1019]) == (200, 30, 30)


@pytest.mark.parametrize("pipe_cls", PIPES)
def test_pad_keeps_the_whole_first_image_on_grey(pipe_cls):
    gen = _run(pipe_cls, _refs(), fit="pad", resolution="1024x1024")
    first = gen.encoded[0]
    assert tuple(first[4, 512]) == PAD_FILL
    assert tuple(first[1019, 512]) == PAD_FILL
    assert tuple(first[512, 4]) == (30, 200, 30)
    assert tuple(first[512, 1019]) == (200, 30, 30)
