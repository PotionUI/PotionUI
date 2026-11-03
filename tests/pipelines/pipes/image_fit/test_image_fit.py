from __future__ import annotations

import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.imaging.fit import PAD_FILL
from src.pipelines.pipes.image_fit.main import ImageFitPipe


def _run(images, **config):
    cfg = ImageFitPipe.get_default_config()
    cfg.update(config)
    return ImageFitPipe(config=cfg).process(PipeInput(input={"image": images}), lambda _o: None).output["image"]


def _refs():
    first = Image.new("RGB", (1200, 600), (200, 30, 30))
    first.paste((30, 200, 30), (0, 0, 300, 600))
    return [first, Image.new("RGB", (500, 700), (10, 10, 240))]


def test_match_passes_the_same_pictures_through():
    refs = _refs()
    out = _run(refs, fit="match", resolution="1664x928")
    assert len(out) == 2 and out[0] is refs[0] and out[1] is refs[1]


def test_default_is_match():
    refs = _refs()
    assert _run(refs)[0] is refs[0]


@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_only_the_first_picture_is_fitted_to_the_target(fit):
    refs = _refs()
    out = _run(refs, fit=fit, resolution="1664x928")
    assert out[0].size == (1664, 928)
    assert out[1] is refs[1]
    assert refs[0].size == (1200, 600)


@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_the_target_snaps_to_the_grid(fit):
    assert _run(_refs(), fit=fit, resolution="1000x570")[0].size == (992, 576)
    assert _run(_refs(), fit=fit, resolution="1000x570", granularity=8)[0].size == (1000, 568)


def test_crop_drops_the_edges_of_a_wider_picture():
    first = _run(_refs(), fit="crop", resolution="1024x1024")[0]
    assert first.getpixel((4, 512)) == (200, 30, 30)
    assert first.getpixel((1019, 512)) == (200, 30, 30)


def test_pad_keeps_the_whole_picture_on_grey():
    first = _run(_refs(), fit="pad", resolution="1024x1024")[0]
    assert first.getpixel((512, 4)) == PAD_FILL
    assert first.getpixel((512, 1019)) == PAD_FILL
    assert first.getpixel((4, 512)) == (30, 200, 30)
    assert first.getpixel((1019, 512)) == (200, 30, 30)


def test_no_pictures_pass_through_empty():
    assert _run([], fit="crop", resolution="1024x1024") == []
