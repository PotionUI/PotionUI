import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.inpainting.mask_utils import color_correction
from src.pipelines.pipes._shared.inpainting.region_utils import resample_image
from src.pipelines.pipes.inpaint_region_crop.main import InpaintRegionCropPipe
from src.pipelines.pipes.inpaint_region_restore.main import InpaintRegionRestorePipe


def _scene(h=900, w=1200, box=(400, 500, 500, 640)):
    rng = np.random.default_rng(11)
    image = rng.integers(70, 190, (h, w, 3), dtype=np.uint8)
    mask = np.zeros((h, w), dtype=np.uint8)
    top, bottom, left, right = box
    mask[top:bottom, left:right] = 255
    return image, mask


def _crop(image, mask):
    pipe = InpaintRegionCropPipe({"strategy": "scaled", "multiple": 16})
    return pipe.process(PipeInput(input={"image": Image.fromarray(image), "mask": Image.fromarray(mask)}), None).output


def _restore(config, crop, generated):
    pipe = InpaintRegionRestorePipe(config)
    return pipe.process(
        PipeInput(input={
            "inpainted_image": generated,
            "crop_region": crop["crop_region"],
            "original_image": crop["original_image"],
            "original_mask": crop["original_mask"],
        }),
        None,
    ).output["image"]


COMPOSITE = {"strategy": "composite"}


def test_identity_edit_round_trips_close_to_the_input():
    image, mask = _scene()
    crop = _crop(image, mask)
    out = np.array(_restore(COMPOSITE, crop, [crop["image"]])[0]).astype(int)
    assert np.abs(out - image.astype(int)).mean() < 6


def test_pixels_outside_the_crop_window_and_feather_are_identical():
    image, mask = _scene()
    crop = _crop(image, mask)
    fresh = Image.new("RGB", crop["image"].size, (255, 0, 255))
    out = np.array(_restore(COMPOSITE, crop, [fresh])[0])
    region = crop["crop_region"]
    outside = np.ones(mask.shape, dtype=bool)
    outside[region["top"]:region["bottom"], region["left"]:region["right"]] = False
    np.testing.assert_array_equal(out[outside], image[outside])
    changed = (out != image).any(axis=2)
    ys, xs = np.nonzero(changed)
    feather = max(2, round(0.08 * 140))
    assert ys.min() >= 400 - feather and ys.max() < 500 + feather
    assert xs.min() >= 500 - feather and xs.max() < 640 + feather


def test_region_takes_the_generated_pixels():
    image, mask = _scene()
    crop = _crop(image, mask)
    fresh = Image.new("RGB", crop["image"].size, (255, 0, 255))
    out = np.array(_restore({"strategy": "composite", "color_match": False}, crop, [fresh])[0])
    assert (out[420:480, 520:620] == (255, 0, 255)).all()


def test_colour_match_pulls_a_shifted_edit_back_toward_the_surroundings():
    image, mask = _scene()
    crop = _crop(image, mask)
    shifted = Image.fromarray(np.clip(np.array(crop["image"]).astype(int) + 45, 0, 255).astype(np.uint8))
    plain = np.array(_restore({"strategy": "composite", "color_match": False}, crop, [shifted])[0]).astype(float)
    matched = np.array(_restore(COMPOSITE, crop, [shifted])[0]).astype(float)
    reference = image[420:480, 520:620].astype(float).mean()
    assert abs(matched[420:480, 520:620].mean() - reference) < abs(plain[420:480, 520:620].mean() - reference) / 3


def test_every_generated_image_is_restored():
    image, mask = _scene()
    crop = _crop(image, mask)
    outs = _restore(COMPOSITE, crop, [crop["image"], Image.new("RGB", crop["image"].size, (0, 0, 0))])
    assert len(outs) == 2
    assert np.array(outs[0]).mean() != np.array(outs[1]).mean()


def test_fooocus_strategy_still_pastes_then_blends_by_the_mask():
    image, mask = _scene()
    crop = InpaintRegionCropPipe({}).process(
        PipeInput(input={"image": Image.fromarray(image), "mask": Image.fromarray(mask)}), None
    ).output
    generated = Image.new("RGB", crop["image"].size, (10, 200, 30))
    out = np.array(_restore({}, crop, generated)[0])
    region = crop["crop_region"]
    pasted = image.copy()
    pasted[region["top"]:region["bottom"], region["left"]:region["right"]] = resample_image(
        np.array(generated), region["right"] - region["left"], region["bottom"] - region["top"]
    )
    np.testing.assert_array_equal(out, color_correction(pasted, image, mask))


def test_fooocus_strategy_only_restores_the_first_image():
    image, mask = _scene()
    crop = InpaintRegionCropPipe({}).process(
        PipeInput(input={"image": Image.fromarray(image), "mask": Image.fromarray(mask)}), None
    ).output
    outs = _restore({}, crop, [crop["image"], crop["image"]])
    assert len(outs) == 1


def test_composite_without_the_original_mask_raises():
    image, mask = _scene()
    crop = _crop(image, mask)
    pipe = InpaintRegionRestorePipe(COMPOSITE)
    with pytest.raises(ValueError, match="original_mask"):
        pipe.process(
            PipeInput(input={
                "inpainted_image": [crop["image"]],
                "crop_region": crop["crop_region"],
                "original_image": crop["original_image"],
            }),
            None,
        )


def test_inpaint_strategy_feathers_inward_and_removes_the_tone_step():
    from src.pipelines.pipes._shared.inpainting.mask_utils import composite_inpaint

    original = np.full((200, 300, 3), 200, dtype=np.uint8)
    content = np.full((200, 300, 3), 218, dtype=np.uint8)
    mask = np.zeros((200, 300), dtype=np.uint8)
    mask[50:150, 100:200] = 255
    row = composite_inpaint(original, content, mask, 0.12, True).astype(int)[100, :, 0]
    assert np.abs(np.diff(row)).max() <= 2
    assert (row[:100] == 200).all() and (row[200:] == 200).all()
    assert row[150] >= 215
    assert np.abs(np.diff(composite_inpaint(original, content, mask, 0.12, False).astype(int)[100, :, 0])).max() <= 3


def test_inpaint_strategy_pixel_feather_overrides_the_fraction():
    from src.pipelines.pipes._shared.inpainting.mask_utils import composite_inpaint

    original = np.zeros((100, 100, 3), dtype=np.uint8)
    content = np.full((100, 100, 3), 200, dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[20:80, 20:80] = 255
    narrow = composite_inpaint(original, content, mask, 0.5, False, feather_px=4)[50, :, 0].astype(int)
    wide = composite_inpaint(original, content, mask, 0.5, False, feather_px=24)[50, :, 0].astype(int)
    assert narrow[28] > wide[28] + 50
