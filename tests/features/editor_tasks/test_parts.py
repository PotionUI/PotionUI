import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks import parts
from tests.features.editor_tasks.parts_support import (
    FIX_FACES_MASK,
    build_parts_manager,
    regions_mask,
    settle_part,
    structured,
    until,
)
from tests.features.editor_tasks.run_support import USER, body, make_edit, make_users, until_terminal


def test_separate_areas_are_separate_parts_in_reading_order():
    mask = np.zeros((100, 200), dtype=np.uint8)
    mask[60:80, 150:190] = 255
    mask[10:30, 100:120] = 255
    mask[12:40, 10:50] = 255

    found = parts.Components(mask)

    assert found.count == 3
    boxes = [found.box(index) for index in range(3)]
    assert boxes == [(10, 30, 100, 120), (12, 40, 10, 50), (60, 80, 150, 190)]
    assert all(found.mask(index).sum() // 255 == (b[1] - b[0]) * (b[3] - b[2]) for index, b in enumerate(boxes))


def test_areas_that_touch_are_one_part_and_below_the_landing_level_nothing_counts():
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[10:20, 10:20] = 255
    mask[20:30, 15:25] = 255
    mask[40:45, 40:45] = 100

    found = parts.Components(mask)

    assert found.count == 1 and found.box(0) == (10, 30, 10, 25)


def test_a_part_mask_holds_only_its_own_area():
    mask = np.zeros((40, 80), dtype=np.uint8)
    mask[5:15, 5:15] = 255
    mask[20:30, 50:70] = 255

    found = parts.Components(mask)

    assert found.mask(0)[20:30, 50:70].max() == 0 and found.mask(1)[5:15, 5:15].max() == 0
    assert found.mask(0).dtype == np.uint8


def test_a_later_patch_is_laid_over_an_earlier_one_by_its_alpha():
    first = np.zeros((10, 10, 4), dtype=np.uint8)
    first[..., :3] = (200, 0, 0)
    first[..., 3] = 255
    second = np.zeros((10, 10, 4), dtype=np.uint8)
    second[..., :3] = (0, 0, 200)
    second[..., 3] = 128

    out = parts.compose_patches([((0, 10, 0, 10), first), ((5, 15, 5, 15), second)], (0, 15, 0, 15))

    assert out.shape == (15, 15, 4)
    assert tuple(out[0, 0]) == (200, 0, 0, 255) and tuple(out[12, 12]) == (0, 0, 200, 128)
    assert tuple(out[7, 7]) == (100, 0, 100, 255)
    assert out[0, 14, 3] == 0


def test_a_transparent_corner_stays_transparent():
    patch = np.zeros((4, 4, 4), dtype=np.uint8)
    patch[1:3, 1:3] = (9, 9, 9, 255)

    out = parts.compose_patches([((2, 6, 2, 6), patch)], (0, 8, 0, 8))

    assert out[..., 3].sum() == 4 * 255 and tuple(out[3, 3]) == (9, 9, 9, 255)


@pytest.mark.asyncio
async def test_a_mask_strategy_fix_faces_sends_each_part_the_whole_picture_and_only_its_own_mask(mock_db, tmp_path):
    make_users()
    manager, fakes, tasks = build_parts_manager(tmp_path, {"MASK-FIXES": FIX_FACES_MASK})
    boxes = [(60, 140, 60, 160), (240, 340, 300, 420)]
    regions_mask((500, 400), boxes).save(fakes.files["region"])

    run = await manager.start(USER, "fix_faces", body(make_edit().id, preset="MASK-FIXES", prompt=None))

    assert run["parts"]["total"] == 2
    first_form = fakes.requests[0].form_data
    assert Image.open(fakes.files[first_form["source_image"].split("/")[-1].split(".")[0]]).size == (500, 400)
    first_mask = np.asarray(Image.open(fakes.files[first_form["source_image_inpaint_mask"].split("/")[-1].split(".")[0]]))
    assert first_mask[60:140, 60:160].min() == 255 and first_mask[240:340, 300:420].max() == 0
    await settle_part(manager, fakes, "g1", results=[structured(500, 400, 1)], size=(500, 400))
    await until(lambda: len(fakes.requests) == 2)
    second_form = fakes.requests[1].form_data
    second_mask = np.asarray(Image.open(fakes.files[second_form["source_image_inpaint_mask"].split("/")[-1].split(".")[0]]))
    assert second_mask[240:340, 300:420].min() == 255 and second_mask[60:140, 60:160].max() == 0
    await settle_part(manager, fakes, "g2", results=[structured(500, 400, 2)], size=(500, 400))

    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "done" and len(done["candidates"]) == 1
    patch = np.asarray(Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]]))
    assert patch.shape[2] == 4 and patch[..., 3].max() == 255
