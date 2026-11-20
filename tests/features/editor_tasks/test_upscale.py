import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks import upscale
from src.platform.http.errors import DomainError


def refusal(**arguments):
    with pytest.raises(DomainError) as refused:
        upscale.output_size(**arguments)
    assert refused.value.status_code == 422
    return refused.value.code


def test_a_scale_gives_the_rounded_output_size():
    assert upscale.output_size((1024, 768), scale=2) == ((2048, 1536), 2.0)
    assert upscale.output_size((1001, 500), scale=1.5)[0] == (1502, 750)


def test_a_target_fits_inside_and_keeps_the_aspect():
    size, factor = upscale.output_size((1000, 500), target=(3000, 3000))

    assert size == (3000, 1500) and factor == pytest.approx(3.0)
    assert upscale.output_size((500, 1000), target=(3000, 2000))[0] == (1000, 2000)


def test_exactly_one_of_scale_and_target_is_needed():
    assert refusal(size=(100, 100)) == "upscale_factor_required"
    assert refusal(size=(100, 100), scale=2, target=(400, 400)) == "upscale_factor_ambiguous"


@pytest.mark.parametrize("arguments", [
    {"scale": 1.0},
    {"scale": 1.0001},
    {"target": (100, 100)},
    {"target": (80, 5000)},
])
def test_a_result_that_is_not_larger_is_refused(arguments):
    assert refusal(size=(100, 100), **arguments) == "upscale_not_larger"


@pytest.mark.parametrize("size, scale", [((8192, 8192), 4), ((9000, 9000), 1.8), ((8193, 100), 2), ((100, 9000), 2)])
def test_a_result_over_the_caps_is_refused(size, scale):
    assert refusal(size=size, scale=scale) == "upscale_too_large"


def test_the_caps_allow_the_largest_square_and_the_longest_strip():
    assert upscale.output_size((4096, 4096), scale=2)[0] == (8192, 8192)
    assert upscale.output_size((8192, 1024), scale=2)[0] == (16384, 2048)


def test_a_picture_that_fits_one_tile_is_one_tile():
    plan = upscale.plan_tiles((1024, 1024), scale=4, tile_area=1048576)

    assert len(plan.tiles) == 1
    tile = plan.tiles[0]
    assert tile.source == (0, 1024, 0, 1024) and tile.output == (0, 4096, 0, 4096)
    assert (tile.feather_left, tile.feather_top) == (0, 0)


def test_a_1024_picture_at_4x_with_a_quarter_megapixel_tile_plans_nine_tiles():
    plan = upscale.plan_tiles((1024, 1024), scale=4, tile_area=262144)

    assert plan.output == (4096, 4096) and plan.scale == 4.0
    assert len(plan.tiles) == 9
    assert {(tile.column, tile.row) for tile in plan.tiles} == {(c, r) for c in range(3) for r in range(3)}
    assert all(tile.source_size[0] * tile.source_size[1] <= 262144 * 1.1 for tile in plan.tiles)


@pytest.mark.parametrize("size, scale, area", [
    ((1024, 1024), 4, 262144),
    ((1000, 777), 2, 262144),
    ((4000, 300), 3, 262144),
    ((300, 4000), 2.5, 400000),
    ((2049, 2047), 1.5, 1048576),
    ((513, 512), 2, 262144),
])
def test_every_output_pixel_is_covered_and_every_overlap_is_blended(size, scale, area):
    plan = upscale.plan_tiles(size, scale=scale, tile_area=area)
    width, height = plan.output
    covered = np.zeros((height, width), dtype=np.uint8)
    for tile in plan.tiles:
        top, bottom, left, right = tile.output
        covered[top:bottom, left:right] += 1
        s_top, s_bottom, s_left, s_right = tile.source
        assert 0 <= s_top < s_bottom <= size[1] and 0 <= s_left < s_right <= size[0]
    assert covered.min() >= 1
    if len(plan.tiles) > 1:
        assert covered.max() >= 2
    canvas = upscale.Canvas(plan.output)
    for tile in plan.tiles:
        canvas.place(tile, Image.new("RGB", tile.output_size, (255, 255, 255)))
    assert canvas.pixels.min() == 255


def test_neighbouring_tiles_overlap_by_at_least_the_overlap():
    plan = upscale.plan_tiles((1024, 1024), scale=2, tile_area=262144, overlap=64)
    by_cell = {(tile.column, tile.row): tile for tile in plan.tiles}

    for (column, row), tile in by_cell.items():
        if column:
            assert by_cell[(column - 1, row)].source[3] - tile.source[2] >= 64
            assert tile.feather_left >= 128
        if row:
            assert by_cell[(column, row - 1)].source[1] - tile.source[0] >= 64
            assert tile.feather_top >= 128


def test_a_wide_strip_gets_wider_tiles_than_a_square_side():
    plan = upscale.plan_tiles((4000, 300), scale=2, tile_area=262144)

    assert {tile.row for tile in plan.tiles} == {0}
    assert all(tile.source_size[1] == 300 for tile in plan.tiles)
    assert all(tile.source_size[0] > 512 for tile in plan.tiles)


def test_a_plan_survives_being_stored():
    plan = upscale.plan_tiles((1024, 700), scale=3, tile_area=262144)

    assert [upscale.Tile.from_dict(tile.to_dict()) for tile in plan.tiles] == list(plan.tiles)


def test_a_seam_between_two_tiles_ramps_across_the_overlap():
    plan = upscale.plan_tiles((600, 200), scale=2, tile_area=100000, overlap=40)
    assert len(plan.tiles) == 2
    first, second = plan.tiles
    canvas = upscale.Canvas(plan.output)

    canvas.place(first, Image.new("RGB", first.output_size, (0, 0, 0)))
    canvas.place(second, Image.new("RGB", second.output_size, (200, 200, 200)))

    row = canvas.pixels[100, :, 0].astype(int)
    left, right = second.output[2], first.output[3]
    assert right - left == second.feather_left
    assert row[left - 1] == 0 and row[right] == 200
    ramp = row[left:right]
    assert all(b >= a for a, b in zip(ramp, ramp[1:])) and 0 < ramp[0] < ramp[-1] < 200


def test_a_result_of_the_wrong_size_is_resampled_to_its_place():
    plan = upscale.plan_tiles((300, 200), scale=2, tile_area=1048576)
    canvas = upscale.Canvas(plan.output)

    canvas.place(plan.tiles[0], Image.new("RGB", (592, 400), (10, 20, 30)))

    assert canvas.pixels.shape == (400, 600, 3) and tuple(canvas.pixels[0, 0]) == (10, 20, 30)


def test_compose_needs_a_result_for_every_tile():
    plan = upscale.plan_tiles((1024, 1024), scale=2, tile_area=262144)

    with pytest.raises(ValueError):
        upscale.compose(plan, [Image.new("RGB", (8, 8))])


def test_compose_gives_an_opaque_rgba_picture_of_the_output_size():
    plan = upscale.plan_tiles((100, 80), scale=2)

    out = upscale.compose(plan, [Image.new("RGB", (200, 160), (5, 6, 7))])

    assert out.shape == (160, 200, 4) and out[..., 3].min() == 255 and tuple(out[3, 3]) == (5, 6, 7, 255)
