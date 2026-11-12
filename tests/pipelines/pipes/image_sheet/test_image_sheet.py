from __future__ import annotations

import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.imaging.sheet import SHEET_FILL, compose_sheet, fit_sheet
from src.pipelines.pipes.image_sheet.main import ImageSheetPipe


def run(images, **config):
    cfg = ImageSheetPipe.get_default_config()
    cfg.update(config)
    return ImageSheetPipe(config=cfg).process(PipeInput(input={"image": images}), lambda _o: None).output["image"]


def pictures(count):
    return [Image.new("RGB", (400, 300), (40 * i + 20, 90, 200 - 30 * i)) for i in range(count)]


def test_pictures_become_one_sheet_at_the_snapped_size():
    out = run(pictures(3), pictures=3, resolution="768x448")

    assert len(out) == 1
    assert out[0].size == (768, 448)
    assert out[0].tobytes() == compose_sheet(pictures(3), (768, 448)).tobytes()


def test_the_size_snaps_to_the_generator_grid():
    assert run(pictures(1), pictures=1, resolution="770x450")[0].size == (768, 448)


def test_a_ready_made_sheet_is_only_fitted():
    ready = Image.new("RGB", (1000, 1000), (9, 99, 199))
    out = run([ready], pictures=0, resolution="768x448")

    assert out[0].tobytes() == fit_sheet(ready, (768, 448)).tobytes()
    assert out[0].getpixel((2, 224)) == SHEET_FILL


def test_the_pictures_after_the_panels_are_the_ready_sheet():
    ready = Image.new("RGB", (768, 448), (1, 2, 3))
    out = run([ready], pictures=0)

    assert out[0].getpixel((5, 5)) == (1, 2, 3)


def test_no_pictures_and_no_sheet_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="Add pictures of your characters and props, or one ready-made reference sheet"):
        run([], pictures=0)


def test_more_pictures_than_panels_is_refused_in_plain_words():
    with pytest.raises(ValueError, match="holds up to 6 pictures and you added 7. Remove 1"):
        run(pictures(7), pictures=7)


def test_pictures_together_with_a_sheet_is_refused():
    with pytest.raises(ValueError, match="either pictures or one ready-made reference sheet, not both"):
        run(pictures(3), pictures=2)


def test_the_inputs_are_not_modified():
    source = pictures(2)
    before = [image.tobytes() for image in source]
    run(source, pictures=2)

    assert [image.tobytes() for image in source] == before
