import asyncio

import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks import expand
from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.formats import BUILT_IN, ExpandFormats, house_format, parse_ratio
from src.features.editor_tasks.registry import EXPAND_PROMPT
from tests.features.editor_tasks.run_support import USER, body, build_manager, make_edit, make_users, until_terminal
from tests.features.editor_tasks.support import CROP, MASK

EXPAND_MASK = {**MASK, "task": "expand"}
SIDE = 256


def truth_canvas(width, height):
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    red = 128 + 90 * np.sin(xs / 41.0 + ys / 97.0)
    green = 128 + 90 * np.sin(ys / 53.0 - xs / 131.0)
    blue = 128 + 90 * np.sin((xs + ys) / 71.0)
    return np.clip(np.dstack([red, green, blue]), 0, 255).astype(np.uint8)


def shifted(array, amount):
    return np.clip(array.astype(np.int16) + amount, 0, 255).astype(np.uint8)


def source_in(truth, canvas):
    left, top, right, bottom = canvas.known
    return truth[top:bottom, left:right].copy()


def join_steps(array, canvas):
    left, top, right, bottom = canvas.known
    steps = []
    if left > 0:
        steps.append(np.abs(array[:, left].astype(int) - array[:, left - 1].astype(int)).max())
    if right < canvas.width:
        steps.append(np.abs(array[:, right].astype(int) - array[:, right - 1].astype(int)).max())
    if top > 0:
        steps.append(np.abs(array[top, :].astype(int) - array[top - 1, :].astype(int)).max())
    if bottom < canvas.height:
        steps.append(np.abs(array[bottom, :].astype(int) - array[bottom - 1, :].astype(int)).max())
    return steps


def run_passes(truth, canvas, offset):
    source = source_in(truth, canvas)
    canvas_rgb = expand.blank_canvas(source, canvas)
    for step in expand.plan_passes(canvas):
        base = expand.pass_input(canvas_rgb, step)
        left, top, right, bottom = step.window
        content = Image.fromarray(shifted(truth[top:bottom, left:right], offset))
        expand.place(canvas_rgb, expand.stitch_pass(base, content, step), step)
    return source, canvas_rgb


def test_the_smallest_canvas_of_a_shape_contains_the_picture():
    assert expand.contain_size((1024, 1024), (9, 16)) == (1024, 1821)
    assert expand.contain_size((1024, 1024), (16, 9)) == (1821, 1024)
    assert expand.contain_size((1024, 1024), (1, 1)) == (1024, 1024)
    assert expand.contain_size((1000, 500), (3, 1)) == (1500, 500)
    assert expand.contain_size((500, 1000), (3, 1)) == (3000, 1000)


def test_a_canvas_must_grow_stay_in_bounds_and_keeps_the_picture_inside():
    with pytest.raises(expand.ExpandError) as smaller:
        expand.make_canvas((400, 400), 300, 600, 0, 0)
    assert smaller.value.code == "expand_smaller"
    with pytest.raises(expand.ExpandError) as same:
        expand.make_canvas((400, 400), 400, 400, 0, 0)
    assert same.value.code == "expand_same"
    with pytest.raises(expand.ExpandError) as large:
        expand.make_canvas((400, 400), 9000, 400, 0, 0)
    assert large.value.code == "expand_too_large"
    canvas = expand.make_canvas((400, 400), 400, 900, 50, 700)
    assert (canvas.x, canvas.y) == (0, 500)
    assert canvas.known == (0, 500, 400, 900)


def test_a_square_becoming_story_or_landscape_takes_one_pass():
    for ratio in ((9, 16), (16, 9)):
        width, height = expand.contain_size((1024, 1024), ratio)
        canvas = expand.make_canvas((1024, 1024), width, height, 0, 0)
        passes = expand.plan_passes(canvas)
        assert len(passes) == 1
        assert passes[0].window == (0, 0, width, height)
        assert passes[0].known == canvas.known


def test_a_large_expansion_runs_in_passes_that_each_grow_within_the_limit():
    canvas = expand.make_canvas((1024, 1024), 3072, 1024, 1024, 0)
    passes = expand.plan_passes(canvas)

    assert len(passes) == 2
    assert passes[-1].window == (0, 0, 3072, 1024)
    known = canvas.known
    for step in passes:
        assert step.known == known
        left, top, right, bottom = step.window
        assert left <= known[0] and right >= known[2] and top == 0 and bottom == 1024
        assert (known[0] - left) + (right - known[2]) <= int(0.8 * (known[2] - known[0])) + 1 or step is passes[-1]
        known = step.window


def test_the_pass_planner_always_ends_on_the_full_canvas():
    for source, target, offset in (((300, 200), (2400, 1900), (700, 600)), ((64, 64), (900, 64), (0, 0)), ((500, 500), (500, 1700), (0, 600))):
        canvas = expand.make_canvas(source, *target, *offset)
        passes = expand.plan_passes(canvas)
        assert passes[-1].window == (0, 0, target[0], target[1])
        assert all(a.window == b.known for a, b in zip(passes, passes[1:]))


@pytest.mark.parametrize("ratio", [(9, 16), (16, 9), (3, 1)])
def test_the_join_is_invisible_even_when_the_model_shifts_the_colours(ratio):
    width, height = expand.contain_size((SIDE, SIDE), ratio)
    canvas = expand.make_canvas((SIDE, SIDE), width, height, (width - SIDE) // 2, (height - SIDE) // 2)
    truth = truth_canvas(width, height)

    source, result = run_passes(truth, canvas, offset=18)

    left, top, right, bottom = canvas.known
    assert np.array_equal(result[top:bottom, left:right], source)
    assert max(join_steps(result, canvas)) <= max(join_steps(truth, canvas)) + 4
    assert result.shape == (height, width, 3)


def test_a_model_that_returns_a_flat_grey_still_leaves_no_step_at_the_join():
    canvas = expand.make_canvas((SIDE, SIDE), 144 * 2, SIDE * 2, 0, 128)
    truth = truth_canvas(canvas.width, canvas.height)
    source = source_in(truth, canvas)
    canvas_rgb = expand.blank_canvas(source, canvas)
    step = expand.plan_passes(canvas)[0]
    base = expand.pass_input(canvas_rgb, step)

    flat = Image.fromarray(np.full((canvas.height, canvas.width, 3), 140, dtype=np.uint8))
    expand.place(canvas_rgb, expand.stitch_pass(base, flat, step), step)

    assert max(join_steps(canvas_rgb, canvas)) <= max(join_steps(truth, canvas)) + 6


def test_the_patch_is_opaque_only_over_the_new_area():
    canvas = expand.make_canvas((SIDE, SIDE), SIDE, SIDE * 2, 0, 0)
    truth = truth_canvas(canvas.width, canvas.height)

    _, result = run_passes(truth, canvas, offset=0)
    patch = expand.patch_of(result, canvas)

    left, top, right, bottom = canvas.known
    assert patch.shape == (SIDE * 2, SIDE, 4)
    assert (patch[top:bottom, left:right, 3] == 0).all()
    assert (patch[bottom:, :, 3] == 255).all()


def test_the_input_hands_the_model_a_filled_area_and_a_mask_that_overlaps_the_picture():
    canvas = expand.make_canvas((SIDE, SIDE), SIDE, SIDE * 2, 0, 0)
    truth = truth_canvas(canvas.width, canvas.height)
    source = source_in(truth, canvas)
    canvas_rgb = expand.blank_canvas(source, canvas)
    step = expand.plan_passes(canvas)[0]

    window = expand.pass_input(canvas_rgb, step)
    mask = expand.soft_mask((canvas.height, canvas.width), step.known_local(), expand.overlap_for(step.size))

    assert np.array_equal(window[:SIDE], source)
    assert window[SIDE:].std() > 0 and abs(int(window[SIDE + 5, 10, 0]) - int(source[-1, 10, 0])) < 40
    assert (mask[SIDE:] == 255).all()
    assert (mask[0] == 0).all()
    assert 0 < mask[SIDE - 5, 10] < 255


def truth_for_run(canvas):
    return truth_canvas(canvas.width, canvas.height)


async def drive(manager, fakes, run, canvas, truth, offset=12):
    steps = expand.plan_passes(canvas)
    original = manager._submit

    async def submit(request, user):
        answer = await original(request, user)
        if len(fakes.requests) > 1:
            fakes.generation.status = "running"
            fakes.generation_files.clear()
        return answer

    manager._submit = submit
    generation_id = run["generation_id"]
    for index, step in enumerate(steps):
        while len(fakes.requests) < index + 1:
            await asyncio.sleep(0.01)
        left, top, right, bottom = step.window
        path = fakes.root / f"pass{index}.png"
        Image.fromarray(shifted(truth[top:bottom, left:right], offset)).save(path)
        fakes.files[f"pass{index}"] = path
        fakes.generation_files.clear()
        fakes.generation_files.append(type("File", (), {"id": f"pass{index}"})())
        fakes.generation.status = "completed"
        manager.on_generation_complete({"generation_id": generation_id, "status": "completed"})
        if index + 1 < len(steps):
            while len(fakes.requests) < index + 2:
                await asyncio.sleep(0.01)
            generation_id = f"g{index + 2}"
    return await until_terminal(manager, USER, run["id"])


@pytest.fixture
def setup(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(
        tmp_path, {"B-MASK": EXPAND_MASK, "A-CROP": CROP}, visible=lambda user: {"B-MASK", "A-CROP"}
    )
    return manager, fakes, make_edit().id


def expand_body(edit_id, width, height, x=0, y=0, preset="B-MASK", **extra):
    return body(
        edit_id, preset=preset, region=False, expand={"width": width, "height": height, "x": x, "y": y}, **extra
    )


def stage(fakes, canvas):
    truth = truth_for_run(canvas)
    path = fakes.root / "picture.png"
    Image.fromarray(source_in(truth, canvas)).save(path)
    fakes.files["picture"] = path
    return truth


@pytest.mark.asyncio
async def test_a_square_becomes_a_story_in_one_run(setup):
    manager, fakes, edit_id = setup
    width, height = expand.contain_size((SIDE, SIDE), (9, 16))
    canvas = expand.make_canvas((SIDE, SIDE), width, height, 0, 0)
    truth = stage(fakes, canvas)

    run = await manager.start(USER, "expand", expand_body(edit_id, width, height, prompt=None))
    done = await drive(manager, fakes, run, canvas, truth)

    request = fakes.requests[0]
    assert request.prompts[0].positive == EXPAND_PROMPT
    assert request.form_data["source_image"].startswith("uploads/")
    assert request.form_data["quantity"] == 1
    assert done["status"] == "done" and len(done["candidates"]) == 1
    patch = np.asarray(Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]]))
    assert patch.shape == (height, width, 4)
    source = source_in(truth, canvas)
    flat = patch[:, :, :3].copy()
    flat[canvas.known[1]:canvas.known[3], canvas.known[0]:canvas.known[2]] = source
    assert max(join_steps(flat, canvas)) <= max(join_steps(truth, canvas)) + 4
    assert done["rect"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


@pytest.mark.asyncio
async def test_a_wide_banner_runs_in_passes_and_each_pass_is_its_own_generation(setup):
    manager, fakes, edit_id = setup
    canvas = expand.make_canvas((SIDE, SIDE), SIDE * 3, SIDE, SIDE, 0)
    truth = stage(fakes, canvas)
    assert len(expand.plan_passes(canvas)) == 2

    run = await manager.start(USER, "expand", expand_body(edit_id, SIDE * 3, SIDE, SIDE, 0, prompt="a quiet street"))
    done = await drive(manager, fakes, run, canvas, truth)

    assert len(fakes.requests) == 2
    assert [request.prompts[0].positive for request in fakes.requests] == ["a quiet street"] * 2
    assert done["status"] == "done"
    patch = np.asarray(Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]]))
    assert patch.shape == (SIDE, SIDE * 3, 4)
    assert fakes.origins["g2"]["run_id"] == run["id"]
    assert fakes.live_kinds() == ["editor-patch.png"]


@pytest.mark.asyncio
async def test_three_formats_from_one_source_are_three_independent_runs(setup):
    manager, fakes, edit_id = setup
    ratios = [(9, 16), (16, 9), (3, 1)]
    canvases = []
    for ratio in ratios:
        width, height = expand.contain_size((SIDE, SIDE), ratio)
        canvases.append(expand.make_canvas((SIDE, SIDE), width, height, (width - SIDE) // 2, (height - SIDE) // 2))
    stage(fakes, canvases[0])

    runs = [
        await manager.start(USER, "expand", expand_body(edit_id, canvas.width, canvas.height, canvas.x, canvas.y))
        for canvas in canvases
    ]

    assert len({run["id"] for run in runs}) == 3
    assert len(fakes.requests) == 3
    sizes = []
    for request in fakes.requests:
        with Image.open(fakes.files[request.form_data["source_image"].split("/")[-1].split(".")[0]]) as opened:
            sizes.append(opened.size)
    assert sizes == [expand.plan_passes(canvas)[0].size for canvas in canvases]


@pytest.mark.asyncio
async def test_expand_needs_a_size_and_other_tasks_refuse_one(setup):
    manager, fakes, edit_id = setup
    from src.platform.http.errors import DomainError

    with pytest.raises(DomainError) as missing:
        await manager.start(USER, "expand", body(edit_id, preset="B-MASK", region=False))
    assert missing.value.code == "expand_required"
    with pytest.raises(DomainError) as extra:
        await manager.start(USER, "edit_region", expand_body(edit_id, 600, 600, preset="A-CROP"))
    assert extra.value.code == "expand_not_supported"
    with pytest.raises(DomainError) as smaller:
        await manager.start(USER, "expand", expand_body(edit_id, 100, 100))
    assert smaller.value.code == "expand_smaller"


@pytest.mark.asyncio
async def test_a_mode_without_a_mask_gets_the_whole_canvas_and_a_task_the_edit_modes_serve_falls_back(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path, {"A-CROP": CROP}, visible=lambda user: {"A-CROP"})
    edit_id = make_edit().id
    width, height = expand.contain_size((500, 400), (16, 9))
    data = body(edit_id, preset="A-CROP", region=False, prompt=None, expand={"width": width, "height": height, "x": 0, "y": 0})

    run = await manager.start(USER, "expand", data)

    request = fakes.requests[0]
    assert request.prompts[0].positive == EXPAND_PROMPT
    assert "source_image_inpaint_mask" not in request.form_data
    assert run["status"] in ("queued", "running")


def test_a_start_request_takes_the_picture_position_as_optional():
    assert StartEditorRunRequest.model_validate(
        {
            "edit": {"id": "e1"},
            "input": {"item_id": "x"},
            "expand": {"width": 800, "height": 600},
            "setup": {"preset_id": "p", "mode": "editor:edit"},
        }
    ).expand.x == 0


def test_formats_offer_the_built_in_ones_then_the_house_ones_the_user_may_use():
    entries = [
        {"id": "Team", "label": "Team banner", "ratio": "5:2", "group_id": "marketing"},
        {"id": "private", "label": "Private", "ratio": "2:1", "group_id": "legal"},
        {"id": "all", "label": "Everyone", "ratio": "4:3"},
        {"id": "bad", "label": "Bad", "ratio": "100:1"},
        "nonsense",
    ]
    formats = ExpandFormats(lambda: entries, lambda user_id: ["marketing"])

    listed = formats.for_user(type("User", (), {"id": "u1"})())

    assert [entry["id"] for entry in listed] == [entry["id"] for entry in BUILT_IN] + ["house.team", "house.all"]
    assert [entry["ratio"] for entry in listed[-2:]] == ["5:2", "4:3"]
    assert [entry["label"] for entry in listed[:5]] == ["Square", "Portrait", "Story", "Landscape", "Wide banner"]


def test_formats_survive_a_missing_or_broken_setting():
    user = type("User", (), {"id": "u1"})()

    assert len(ExpandFormats(lambda: None, lambda user_id: []).for_user(user)) == len(BUILT_IN)
    assert len(ExpandFormats(lambda: "oops", lambda user_id: []).for_user(user)) == len(BUILT_IN)
    assert parse_ratio("16:9") == (16, 9) and parse_ratio("0:1") is None and parse_ratio("1.5:1") is None
    assert house_format({"id": "Bad Id", "label": "x", "ratio": "1:1"}) is None
