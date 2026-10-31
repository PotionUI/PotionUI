from unittest.mock import Mock

import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import ImageGenerationOutput
from src.pipelines.pipes.generator.sdxl.main import GeneratorSDXLPipe


def _pipe(**config):
    return GeneratorSDXLPipe({**GeneratorSDXLPipe.get_default_config(), **config})


def _conditioning(tag):
    conditioning = Mock()
    conditioning.tag = tag
    return conditioning


@pytest.fixture
def model():
    mock = Mock()
    mock.img2img = Mock(side_effect=lambda g_input, outputs, **kw: ImageGenerationOutput(image=Image.new("RGB", (8, 8))))
    return mock


def _calls(model):
    return [call.args[0] for call in model.img2img.call_args_list]


def test_each_source_gets_its_own_seed_and_conditioning(model):
    pipe = _pipe()
    cond = [_conditioning("a"), _conditioning("b")]
    pipe_input = PipeInput(input={
        "model": model,
        "conditioning": cond,
        "seed": [11, 22],
        "image": [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))],
    })
    pipe._generate_img2img(model, pipe_input.input["image"], cond, [11, 22], [], [], None, Mock(), False)
    inputs = _calls(model)
    assert [i[IOType.SEED] for i in inputs] == [11, 22]
    assert [i[IOType.CONDITIONING] for i in inputs] == cond


def test_missing_conditioning_index_falls_back_to_first(model):
    pipe = _pipe()
    cond = [_conditioning("a")]
    images = [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))]
    pipe._generate_img2img(model, images, cond, [1, 2], [], [], None, Mock(), False)
    assert [i[IOType.CONDITIONING] for i in _calls(model)] == [cond[0], cond[0]]


def test_max_pixels_shrinks_large_source_to_multiple_of_eight(model):
    pipe = _pipe(max_pixels=64 * 64)
    cond = [_conditioning("a")]
    pipe._generate_img2img(model, [Image.new("RGB", (200, 100))], cond, [5], [], [], None, Mock(), False)
    (g_input,) = _calls(model)
    width, height = g_input[IOType.RESOLUTION]
    assert width % 8 == 0 and height % 8 == 0
    assert width * height <= 64 * 64
    assert g_input[IOType.IMAGE].size == (width, height)
    assert width > height


def test_max_pixels_zero_keeps_source_size(model):
    pipe = _pipe()
    cond = [_conditioning("a")]
    pipe._generate_img2img(model, [Image.new("RGB", (200, 100))], cond, [5], [], [], None, Mock(), False)
    (g_input,) = _calls(model)
    assert g_input[IOType.RESOLUTION] == [200, 100]


def test_max_pixels_leaves_smaller_source_untouched(model):
    pipe = _pipe(max_pixels=1_000_000)
    cond = [_conditioning("a")]
    pipe._generate_img2img(model, [Image.new("RGB", (200, 100))], cond, [5], [], [], None, Mock(), False)
    (g_input,) = _calls(model)
    assert g_input[IOType.RESOLUTION] == [200, 100]
