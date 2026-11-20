import numpy as np
from PIL import Image

from src.features.editor_tasks import regions
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.inpaint_region_crop.main import InpaintRegionCropPipe
from src.pipelines.pipes.inpaint_region_restore.main import InpaintRegionRestorePipe
from src.platform.imaging.regions import alpha_over

AREA = 1048576


def scene(h=1500, w=2000, box=(600, 700, 900, 1050)):
    rng = np.random.default_rng(5)
    picture = rng.integers(70, 180, (h, w, 3), dtype=np.uint8)
    mask = np.zeros((h, w), dtype=np.uint8)
    top, bottom, left, right = box
    mask[top:bottom, left:right] = 255
    return picture, mask


def geometry_for(picture, mask, strategy="crop", **extra):
    values = dict(context=0.5, area=AREA, multiple=16)
    values.update(extra)
    return regions.plan(picture.shape, mask, strategy, **values)


def test_the_crop_box_and_working_size_equal_the_inpaint_crop_pipes():
    picture, mask = scene()
    config = {"strategy": "scaled", "context": 0.5, "min_side": 256, "working_area": AREA, "multiple": 16}
    out = InpaintRegionCropPipe(config).process(
        PipeInput(input={"image": Image.fromarray(picture), "mask": Image.fromarray(mask)}), None
    ).output

    geometry = geometry_for(picture, mask)

    region = out["crop_region"]
    assert geometry.box == (region["top"], region["bottom"], region["left"], region["right"])
    assert geometry.working == out["image"].size
    assert not geometry.whole and geometry.has_region


def test_a_small_region_gets_the_smallest_crop_and_the_box_clamps_at_the_picture_edge():
    picture, mask = scene(box=(0, 20, 0, 30))

    geometry = geometry_for(picture, mask)

    top, bottom, left, right = geometry.box
    assert (top, left) == (0, 0)
    assert bottom - top >= 256 and right - left >= 256
    assert bottom <= 1500 and right <= 2000


def test_the_feather_is_six_percent_of_the_crop_with_a_floor_of_eight():
    picture, mask = scene()
    geometry = geometry_for(picture, mask)
    top, bottom, left, right = geometry.box
    assert geometry.feather_px == max(8, round(0.06 * max(bottom - top, right - left)))
    assert regions.feather_for((0, 40, 0, 40)) == 8


def test_a_crop_covering_most_of_the_picture_becomes_the_whole_picture_keeping_the_region_as_alpha():
    picture, mask = scene(box=(100, 1400, 100, 1900))

    geometry = geometry_for(picture, mask)

    assert geometry.whole and geometry.box == (0, 1500, 0, 2000) and geometry.has_region


def test_no_region_means_the_whole_picture_with_opaque_alpha():
    picture, _ = scene()

    geometry = geometry_for(picture, None)
    cut = regions.prepare(picture, None, geometry)
    patch = regions.stitch(cut.base, cut.region, Image.fromarray(picture), geometry)

    assert geometry.whole and not geometry.has_region and geometry.box == (0, 1500, 0, 2000)
    assert cut.region is None and (patch[:, :, 3] == 255).all()


def test_the_whole_strategy_ignores_the_box_rules_even_with_a_region():
    picture, mask = scene()

    assert geometry_for(picture, mask, "whole").whole


def test_buckets_pick_the_closest_aspect_and_grow_the_box():
    picture, mask = scene()
    buckets = [[1024, 1024], [1152, 896], [896, 1152]]

    geometry = geometry_for(picture, mask, buckets=buckets)

    assert list(geometry.working) in buckets
    width, height = geometry.crop_size
    assert abs(width / height - geometry.working[0] / geometry.working[1]) < 0.05


def test_the_mask_strategy_sends_the_whole_picture_and_the_full_mask():
    picture, mask = scene()

    geometry = geometry_for(picture, mask, "mask")
    cut = regions.prepare(picture, mask, geometry)

    assert cut.image.size == (2000, 1500) and cut.mask.size == (2000, 1500)
    assert geometry.working[0] % 16 == 0 and geometry.working[1] % 16 == 0
    assert not geometry.whole


def test_the_mask_strategy_without_a_region_sends_a_white_mask():
    picture, _ = scene()

    cut = regions.prepare(picture, None, geometry_for(picture, None, "mask"))

    assert np.asarray(cut.mask).min() == 255


def test_the_crop_strategy_sends_no_mask_unless_one_is_asked_for():
    picture, mask = scene()
    geometry = geometry_for(picture, mask, "crop")

    assert regions.prepare(picture, mask, geometry).mask is None


def test_the_crop_strategy_can_hand_over_the_region_cut_and_resampled_like_the_picture():
    picture, mask = scene()
    geometry = geometry_for(picture, mask, "crop")

    cut = regions.prepare(picture, mask, geometry, with_mask=True)

    assert cut.mask.size == cut.image.size == geometry.working
    levels = np.asarray(cut.mask)
    assert set(np.unique(levels)) == {0, 255}
    top, bottom, left, right = geometry.box
    expected_share = float((mask[top:bottom, left:right] > 0).mean())
    assert abs(float((levels > 0).mean()) - expected_share) < 0.02


def test_the_crop_strategy_without_a_region_hands_over_an_empty_mask():
    picture, _ = scene()

    cut = regions.prepare(picture, None, geometry_for(picture, None, "crop"), with_mask=True)

    assert cut.mask.size == cut.image.size and not np.asarray(cut.mask).any()


def test_the_mask_strategy_ignores_the_with_mask_flag():
    picture, mask = scene()
    geometry = geometry_for(picture, mask, "mask")

    cut = regions.prepare(picture, mask, geometry, with_mask=True)

    assert cut.mask.size == (2000, 1500)


def test_a_patch_inside_the_region_matches_the_restore_composite():
    picture, mask = scene(h=400, w=500, box=(150, 250, 200, 330))
    geometry = geometry_for(picture, mask, area=262144)
    cut = regions.prepare(picture, mask, geometry)
    rng = np.random.default_rng(9)
    result = Image.fromarray(rng.integers(70, 180, (geometry.working[1], geometry.working[0], 3), dtype=np.uint8))
    top, bottom, left, right = geometry.box
    size = max(250 - 150, 330 - 200)
    restore = InpaintRegionRestorePipe(
        {"strategy": "composite", "feather": geometry.feather_px / size, "color_match": True}
    ).process(
        PipeInput(input={
            "inpainted_image": [result],
            "crop_region": {"top": top, "bottom": bottom, "left": left, "right": right, "original_width": 500, "original_height": 400},
            "original_image": Image.fromarray(picture),
            "original_mask": Image.fromarray(mask),
        }),
        None,
    ).output["image"][0]

    patch = regions.stitch(cut.base, cut.region, result, geometry)
    placed = alpha_over(cut.base, patch)

    expected = np.asarray(restore)[top:bottom, left:right]
    assert np.abs(placed.astype(int) - expected.astype(int)).max() <= 1


def test_the_mask_patch_is_fully_opaque_inside_the_region_and_clear_far_outside():
    picture, mask = scene(h=400, w=500, box=(150, 250, 200, 330))
    geometry = geometry_for(picture, mask, "mask", area=262144)
    cut = regions.prepare(picture, mask, geometry)

    patch = regions.stitch(cut.base, cut.region, Image.fromarray(picture), geometry)

    top, _, left, _ = geometry.box
    assert (patch[150 - top:250 - top, 200 - left:330 - left, 3] == 255).all()
    assert patch[0, 0, 3] == 0


def test_a_landed_mask_uses_the_half_level_cutoff_and_resizes_to_the_picture():
    soft = Image.new("L", (10, 10), 127)
    firm = Image.new("L", (10, 10), 128)

    assert regions.land_mask(soft, (20, 20)) is None
    assert regions.land_mask(firm, (20, 20)).shape == (20, 20)


def test_the_picture_is_capped_at_eight_thousand_pixels_on_the_long_side():
    capped = regions.cap_picture(Image.new("RGB", (9000, 3000)))

    assert capped.size == (8192, 2731)
    assert regions.cap_picture(Image.new("RGB", (100, 50))).size == (100, 50)


def test_the_surroundings_field_wins_over_the_offer():
    assert regions.context_from({"region_context": "1.0"}, 0.5) == 1.0
    assert regions.context_from({}, 0.5) == 0.5
    assert regions.context_from({"region_context": "wide"}, 0.5) == 0.5
