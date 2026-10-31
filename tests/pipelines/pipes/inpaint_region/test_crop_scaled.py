import numpy as np
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.inpaint_region_crop.main import InpaintRegionCropPipe


def _scene(h=1500, w=2000, box=(600, 700, 900, 1050)):
    rng = np.random.default_rng(5)
    image = Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
    mask = np.zeros((h, w), dtype=np.uint8)
    top, bottom, left, right = box
    mask[top:bottom, left:right] = 255
    return image, Image.fromarray(mask)


def _run(config, image, mask):
    return InpaintRegionCropPipe(config).process(PipeInput(input={"image": image, "mask": mask}), None).output


SCALED = {"strategy": "scaled", "multiple": 16}


def test_scaled_crop_resamples_to_the_working_area_on_the_model_grid():
    out = _run(SCALED, *_scene())
    w, h = out["image"].size
    assert w % 16 == 0 and h % 16 == 0
    assert abs(w * h - 1024 * 1024) / (1024 * 1024) < 0.08
    assert out["mask"].size == out["image"].size
    assert out["images"][0] is out["image"]


def test_small_region_is_upscaled():
    out = _run(SCALED, *_scene(box=(700, 720, 900, 930)))
    region = out["crop_region"]
    source_w = region["right"] - region["left"]
    assert source_w >= 256
    assert out["image"].size[0] > source_w * 2


def test_context_tracks_region_size():
    small = _run(SCALED, *_scene(box=(700, 800, 900, 1000)))["crop_region"]
    large = _run(SCALED, *_scene(box=(300, 1100, 500, 1300)))["crop_region"]
    assert (small["bottom"] - small["top"]) < (large["bottom"] - large["top"]) / 3


def test_crop_region_contains_the_masked_area_and_reports_full_size():
    out = _run(SCALED, *_scene())
    region = out["crop_region"]
    assert region["top"] <= 600 and region["bottom"] >= 700
    assert region["left"] <= 900 and region["right"] >= 1050
    assert (region["original_width"], region["original_height"]) == (2000, 1500)
    assert out["original_image"].size == (2000, 1500)
    assert out["original_mask"].size == (2000, 1500)


def test_empty_mask_falls_back_to_the_whole_image():
    image, _ = _scene(h=600, w=800)
    out = _run(SCALED, image, Image.fromarray(np.zeros((600, 800), dtype=np.uint8)))
    region = out["crop_region"]
    assert (region["top"], region["bottom"], region["left"], region["right"]) == (0, 600, 0, 800)


def test_mismatched_mask_size_is_resized_to_the_image():
    image, mask = _scene(h=600, w=800, box=(200, 300, 300, 450))
    out = _run(SCALED, image, mask.resize((400, 300)))
    assert out["original_mask"].size == (800, 600)


def test_default_strategy_keeps_the_fixed_fraction_crop():
    image, mask = _scene(h=1000, w=1000, box=(480, 520, 480, 520))
    out = _run({}, image, mask)
    region = out["crop_region"]
    assert region["bottom"] - region["top"] >= 600
    assert out["image"].size == (region["right"] - region["left"], region["bottom"] - region["top"])


def test_default_strategy_keeps_the_exposed_contract():
    names = {c.name: c.default for c in InpaintRegionCropPipe.configuration()}
    assert names["strategy"] == "fooocus"
    assert names["k"] == 0.618
    assert names["min_size"] == 1024 and names["max_size"] == 1024


def test_faint_stray_mask_pixels_do_not_widen_the_region_or_reach_the_restore():
    image, mask = _scene()
    arr = np.array(mask)
    arr[10, 20] = 3
    arr[1490, 1980] = 40
    out = _run(SCALED, image, Image.fromarray(arr))
    region = out["crop_region"]
    assert region["top"] > 0 and region["left"] > 0
    assert region["bottom"] < 1500 and region["right"] < 2000
    assert np.array(out["original_mask"]).max() == 255
    assert np.array(out["original_mask"])[10, 20] == 0


def test_default_strategy_ignores_faint_stray_mask_pixels():
    image, mask = _scene(h=1000, w=1000, box=(480, 520, 480, 520))
    arr = np.array(mask)
    arr[5, 5] = 3
    arr[990, 990] = 40
    out = _run({}, image, Image.fromarray(arr))
    region = out["crop_region"]
    assert region["bottom"] - region["top"] < 1000
    assert np.array(out["original_mask"])[5, 5] == 0


def test_composite_region_ignores_faint_stray_pixels():
    from src.pipelines.pipes._shared.inpainting.mask_utils import composite_region

    original = np.zeros((200, 200, 3), dtype=np.uint8)
    content = np.full((200, 200, 3), 200, dtype=np.uint8)
    mask = np.zeros((200, 200), dtype=np.uint8)
    mask[90:110, 90:110] = 255
    mask[2, 2] = 30
    out = composite_region(original, content, mask, 0.08, False)
    assert (out[:20, :20] == 0).all()
    assert (out[95:105, 95:105] == 200).all()


def test_crop_emits_the_mask_as_a_one_item_list_like_the_image():
    out = _run(SCALED, *_scene())
    assert out["masks"] == [out["mask"]]
