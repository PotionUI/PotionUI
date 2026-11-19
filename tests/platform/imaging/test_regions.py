import numpy as np
from PIL import Image

from src.pipelines.pipes._shared.inpainting import mask_utils, region_utils
from src.platform.imaging import regions
from src.platform.media_tools.ops import place_patch
from src.platform.media_tools.types import ToolMedia


def scene(h=120, w=160, box=(40, 80, 50, 110)):
    rng = np.random.default_rng(3)
    original = rng.integers(70, 180, (h, w, 3), dtype=np.uint8)
    content = rng.integers(70, 180, (h, w, 3), dtype=np.uint8)
    mask = np.zeros((h, w), dtype=np.uint8)
    top, bottom, left, right = box
    mask[top:bottom, left:right] = 255
    return original, content, mask


def test_old_import_paths_are_the_same_objects():
    for name in ("scaled_crop_box", "working_size", "mask_bbox", "resample_image", "compute_initial_abcd", "solve_abcd"):
        assert getattr(region_utils, name) is getattr(regions, name)
    for name in ("composite_region", "composite_inpaint", "composite_outpaint", "color_correction", "feather_alpha"):
        assert getattr(mask_utils, name) is getattr(regions, name)


def test_a_patch_over_the_original_matches_the_restore_composite_within_one_level():
    original, content, mask = scene()
    feather_px = 12
    size = 60
    expected = regions.composite_region(original, content, mask, feather_px / size, True)
    ys, xs = np.nonzero(mask)
    assert max(ys.max() - ys.min(), xs.max() - xs.min()) + 1 == 60

    patch = regions.region_patch(original, content, mask, feather_px, True)
    placed = regions.alpha_over(original, patch)

    assert np.abs(placed.astype(int) - expected.astype(int)).max() <= 1
    outside = regions.dilate_box(mask >= 128, feather_px) == 0
    assert (placed[outside] == original[outside]).all()


def test_without_a_region_the_patch_is_opaque_content():
    original, content, _ = scene()

    patch = regions.region_patch(original, content, np.zeros(original.shape[:2], np.uint8), 8, True)

    assert (patch[:, :, 3] == 255).all()
    assert (patch[:, :, :3] == content).all()


def test_place_patch_at_one_to_one_equals_the_alpha_over_composite(tmp_path):
    original, content, mask = scene()
    patch = regions.region_patch(original, content, mask, 12, True)
    path = tmp_path / "patch.png"
    Image.fromarray(patch).save(path)
    media = ToolMedia(path=path, kind="image", width=160, height=120)
    params = {"patch": media, "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}

    out = place_patch.PlacePatch().image(Image.fromarray(original), params)

    assert np.abs(np.asarray(out).astype(int) - regions.alpha_over(original, patch).astype(int)).max() <= 1


def test_place_patch_pastes_a_smaller_patch_at_its_rect(tmp_path):
    base = Image.new("RGB", (100, 80), (10, 10, 10))
    path = tmp_path / "p.png"
    Image.new("RGBA", (20, 10), (200, 0, 0, 255)).save(path)
    media = ToolMedia(path=path, kind="image", width=20, height=10)

    out = place_patch.PlacePatch().image(base, {"patch": media, "x": 0.1, "y": 0.25, "width": 0.2, "height": 0.125})

    assert out.getpixel((10, 20)) == (200, 0, 0) and out.getpixel((29, 29)) == (200, 0, 0)
    assert out.getpixel((9, 20)) == (10, 10, 10) and out.getpixel((30, 30)) == (10, 10, 10)


def test_place_patch_runs_inside_a_chain(tmp_path):
    from src.platform.media_tools import ToolContext, run_chain

    original, content, mask = scene()
    patch = regions.region_patch(original, content, mask, 12, True)
    Image.fromarray(patch).save(tmp_path / "patch.png")
    Image.fromarray(original).save(tmp_path / "base.png")
    ctx = ToolContext(tmp_path)
    patch_media = ctx.probe(tmp_path / "patch.png", "image")
    base_media = ctx.probe(tmp_path / "base.png", "image")

    (out,) = run_chain([(place_patch.spec(), {"patch": patch_media})], [base_media], ctx)

    placed = np.asarray(Image.open(out.path).convert("RGB"))
    assert np.abs(placed.astype(int) - regions.alpha_over(original, patch).astype(int)).max() <= 1


def test_round_dilation_is_a_disc_and_sits_inside_the_box_dilation():
    region = np.zeros((41, 41), dtype=bool)
    region[20, 20] = True

    disc = regions.dilate_round(region, 8)
    box = regions.dilate_box(region, 8)

    assert disc[20, 28] and disc[12, 20] and not disc[12, 12] and not disc[20, 29]
    assert box[12, 12] and (disc <= box).all()
    assert 180 < disc.sum() < 230
    assert regions.dilate_round(region, 0).sum() == 1 and regions.dilate_round(region, 0) is not region


def test_extend_down_grows_only_downward_and_stops_at_the_picture():
    region = np.zeros((20, 6), dtype=bool)
    region[5:8, 2:4] = True

    taller = regions.extend_down(region, 4)

    assert taller[5:12, 2:4].all() and not taller[12:, :].any() and not taller[:5, :].any()
    assert not taller[:, :2].any() and not taller[:, 4:].any()
    assert regions.extend_down(region, 50)[19, 2] and regions.extend_down(region, 0).sum() == region.sum()
    assert not regions.extend_down(np.zeros((5, 5), dtype=bool), 3).any()


def test_a_matte_patch_takes_the_matte_as_its_alpha_unchanged():
    content = np.full((4, 6, 3), 77.6, dtype=np.float32)
    matte = np.tile(np.array([0, 30, 90, 128, 200, 255], dtype=np.uint8), (4, 1))

    patch = regions.matte_patch(content, matte)

    assert patch.shape == (4, 6, 4) and patch.dtype == np.uint8
    assert (patch[..., 3] == matte).all()
    assert (patch[..., :3] == 78).all()
