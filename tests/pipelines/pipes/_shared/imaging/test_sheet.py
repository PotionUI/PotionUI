from __future__ import annotations

import pytest
from PIL import Image

from src.pipelines.pipes._shared.imaging.sheet import (
    MAX_PANELS,
    SHEET_FILL,
    compose_sheet,
    fit_sheet,
    panel_boxes,
    sheet_grid,
    sheet_spacing,
)

SIZE = (768, 448)
COLORS = [(220, 30, 30), (30, 200, 30), (30, 30, 220), (230, 230, 20), (20, 220, 220), (220, 20, 220)]


def solid(index, size=(300, 300)):
    return Image.new("RGB", size, COLORS[index])


@pytest.mark.parametrize("count, grid", [(1, (1, 1)), (2, (2, 1)), (3, (2, 2)), (4, (2, 2)), (5, (3, 2)), (6, (3, 2))])
def test_the_grid_grows_with_the_number_of_pictures(count, grid):
    assert sheet_grid(count) == grid


@pytest.mark.parametrize("count", [0, 7, -1])
def test_a_sheet_holds_one_to_six_pictures(count):
    assert MAX_PANELS == 6
    with pytest.raises(ValueError, match="1 to 6"):
        sheet_grid(count)


@pytest.mark.parametrize("count", range(1, 7))
def test_every_panel_lies_inside_the_sheet_and_no_two_overlap(count):
    boxes = panel_boxes(count, SIZE)

    assert len(boxes) == count
    for left, top, width, height in boxes:
        assert left >= 0 and top >= 0 and width > 0 and height > 0
        assert left + width <= SIZE[0] and top + height <= SIZE[1]
    for i, (l1, t1, w1, h1) in enumerate(boxes):
        for l2, t2, w2, h2 in boxes[i + 1:]:
            assert l1 + w1 <= l2 or l2 + w2 <= l1 or t1 + h1 <= t2 or t2 + h2 <= t1


def test_one_picture_takes_the_whole_sheet_but_its_margin():
    (left, top, width, height), = panel_boxes(1, SIZE)

    assert left == top == sheet_spacing(SIZE) == 7
    assert (width, height) == (SIZE[0] - 14, SIZE[1] - 14)


def test_two_pictures_sit_side_by_side_on_one_row():
    first, second = panel_boxes(2, SIZE)

    assert first[1] == second[1] and first[3] == second[3]
    assert first[0] + first[2] < second[0]


def test_three_and_four_pictures_use_two_rows_of_two():
    for count in (3, 4):
        boxes = panel_boxes(count, SIZE)
        assert sorted({box[1] for box in boxes}) == [boxes[0][1], boxes[-1][1]]
        assert boxes[0][1] < boxes[-1][1]


def test_five_and_six_pictures_use_three_columns_in_two_rows():
    boxes = panel_boxes(6, SIZE)

    assert len({box[0] for box in boxes}) == 3
    assert len({box[1] for box in boxes}) == 2
    assert panel_boxes(5, SIZE) == boxes[:5]


def test_fewer_pictures_get_bigger_panels():
    areas = [panel_boxes(count, SIZE)[0][2] * panel_boxes(count, SIZE)[0][3] for count in (1, 2, 4, 6)]

    assert areas == sorted(areas, reverse=True)
    assert len(set(areas)) == len(areas)


def test_the_boxes_are_the_same_every_time():
    assert panel_boxes(5, SIZE) == panel_boxes(5, SIZE)


@pytest.mark.parametrize("size", [(768, 448), (832, 480), (512, 512), (480, 832), (64, 64)])
@pytest.mark.parametrize("count", range(1, 7))
def test_the_sheet_is_exactly_the_requested_size(count, size):
    sheet = compose_sheet([solid(i) for i in range(count)], size)

    assert sheet.size == size
    assert sheet.mode == "RGB"


def test_each_picture_lands_in_its_own_panel_in_order():
    sheet = compose_sheet([solid(i) for i in range(4)], SIZE)

    for index, (left, top, width, height) in enumerate(panel_boxes(4, SIZE)):
        assert sheet.getpixel((left + width // 2, top + height // 2)) == COLORS[index]


def test_the_background_and_unused_panels_stay_neutral():
    sheet = compose_sheet([solid(i) for i in range(3)], SIZE)
    left, top, width, height = panel_boxes(4, SIZE)[3]

    assert sheet.getpixel((0, 0)) == SHEET_FILL
    assert sheet.getpixel((left + width // 2, top + height // 2)) == SHEET_FILL


def test_a_picture_is_fitted_without_stretching():
    wide = Image.new("RGB", (800, 200), COLORS[0])
    sheet = compose_sheet([wide], SIZE)
    left, top, width, height = panel_boxes(1, SIZE)[0]

    covered = [(x, y) for y in range(top, top + height) for x in range(left, left + width, 4)
               if sheet.getpixel((x, y)) == COLORS[0]]
    xs = {x for x, _ in covered}
    ys = {y for _, y in covered}
    assert max(xs) - min(xs) + 4 >= width - 8
    assert (max(ys) - min(ys) + 1) / (max(xs) - min(xs) + 4) == pytest.approx(200 / 800, abs=0.02)


def test_a_tall_picture_is_centred_with_bars_either_side():
    tall = Image.new("RGB", (200, 800), COLORS[1])
    sheet = compose_sheet([tall], SIZE)
    left, top, width, height = panel_boxes(1, SIZE)[0]

    assert sheet.getpixel((left + 2, top + height // 2)) == SHEET_FILL
    assert sheet.getpixel((left + width // 2, top + height // 2)) == COLORS[1]
    assert sheet.getpixel((left + width - 3, top + height // 2)) == SHEET_FILL


def test_a_transparent_picture_shows_the_neutral_background_not_black():
    clear = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    sheet = compose_sheet([clear], SIZE)

    assert sheet.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == SHEET_FILL


def test_a_ready_made_sheet_is_only_fitted_to_the_size():
    ready = Image.new("RGB", (1536, 896), COLORS[2])
    out = fit_sheet(ready, SIZE)

    assert out.size == SIZE
    assert out.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == COLORS[2]
    assert out.getpixel((2, 2)) == COLORS[2]


def test_a_ready_made_sheet_of_another_shape_is_padded_not_stretched():
    square = Image.new("RGB", (400, 400), COLORS[3])
    out = fit_sheet(square, SIZE)

    assert out.getpixel((2, SIZE[1] // 2)) == SHEET_FILL
    assert out.getpixel((SIZE[0] // 2, 2)) == COLORS[3]
    assert out.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == COLORS[3]
