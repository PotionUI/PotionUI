from unittest.mock import Mock

import pytest
from PIL import Image

from src.pipelines.contracts import IOType
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


def _run(pipe, model, images, cond, seeds, mask=None):
    pipe._generate_img2img(
        model=model, images=images, conditioning=cond, seeds=seeds,
        controlnets=[], control_images=[], mask=mask,
        generation_outputs=Mock(), using_controlnet=False,
    )


def test_each_source_gets_its_own_seed_and_conditioning(model):
    cond = [_conditioning("a"), _conditioning("b")]
    images = [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))]
    _run(_pipe(), model, images, cond, [11, 22])
    inputs = _calls(model)
    assert [i[IOType.SEED] for i in inputs] == [11, 22]
    assert [i[IOType.CONDITIONING] for i in inputs] == cond


def test_two_images_with_a_mask_and_no_seeds_get_distinct_seeds_and_the_mask(model):
    mask = Image.new("L", (64, 64))
    images = [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))]
    _run(_pipe(seed=-1), model, images, [_conditioning("a")], [], mask=mask)
    inputs = _calls(model)
    assert inputs[0][IOType.SEED] != inputs[1][IOType.SEED]
    assert all(i[IOType.MASK] is mask for i in inputs)


def test_max_pixels_resizes_the_mask_to_match_the_image(model):
    mask = Image.new("L", (200, 100), 255)
    _run(_pipe(max_pixels=64 * 64), model, [Image.new("RGB", (200, 100))], [_conditioning("a")], [5], mask=mask)
    (g_input,) = _calls(model)
    assert g_input[IOType.MASK].size == g_input[IOType.IMAGE].size
    assert g_input[IOType.MASK].size != (200, 100)


def test_max_pixels_resizes_control_images_to_match(model):
    model.img2img_controlnet = Mock(side_effect=lambda g, controls, outputs, **kw: ImageGenerationOutput(image=Image.new("RGB", (8, 8))))
    control = Image.new("RGB", (200, 100))
    _pipe(max_pixels=64 * 64)._generate_img2img(
        model=model, images=[Image.new("RGB", (200, 100))], conditioning=[_conditioning("a")], seeds=[5],
        controlnets=[object()], control_images=[control], mask=None,
        generation_outputs=Mock(), using_controlnet=True,
    )
    g_input = model.img2img_controlnet.call_args.args[0]
    (sent,) = model.img2img_controlnet.call_args.args[1]
    assert sent.size == g_input[IOType.IMAGE].size


def test_missing_conditioning_index_falls_back_to_first(model):
    pipe = _pipe()
    cond = [_conditioning("a")]
    images = [Image.new("RGB", (64, 64)), Image.new("RGB", (64, 64))]
    _run(pipe, model, images, cond, [1, 2])
    assert [i[IOType.CONDITIONING] for i in _calls(model)] == [cond[0], cond[0]]


def test_max_pixels_shrinks_large_source_to_multiple_of_eight(model):
    pipe = _pipe(max_pixels=64 * 64)
    cond = [_conditioning("a")]
    _run(pipe, model, [Image.new("RGB", (200, 100))], cond, [5])
    (g_input,) = _calls(model)
    width, height = g_input[IOType.RESOLUTION]
    assert width % 8 == 0 and height % 8 == 0
    assert width * height <= 64 * 64
    assert g_input[IOType.IMAGE].size == (width, height)
    assert width > height


def test_max_pixels_zero_keeps_source_size(model):
    pipe = _pipe()
    cond = [_conditioning("a")]
    _run(pipe, model, [Image.new("RGB", (200, 100))], cond, [5])
    (g_input,) = _calls(model)
    assert g_input[IOType.RESOLUTION] == [200, 100]


def test_max_pixels_leaves_smaller_source_untouched(model):
    pipe = _pipe(max_pixels=1_000_000)
    cond = [_conditioning("a")]
    _run(pipe, model, [Image.new("RGB", (200, 100))], cond, [5])
    (g_input,) = _calls(model)
    assert g_input[IOType.RESOLUTION] == [200, 100]
