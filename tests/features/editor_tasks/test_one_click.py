import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks import regions
from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.regions import Geometry
from src.features.editor_tasks.registry import (
    CORE_TASKS,
    REPLACE_BACKGROUND_PROMPT,
    TaskDefinition,
    TaskRegistry,
    register_core_tasks,
)
from src.platform.http.errors import DomainError
from src.platform.imaging.regions import alpha_over, dilate_box, resample_image
from tests.features.editor_tasks.run_support import (
    USER,
    body,
    build_manager,
    make_edit,
    make_users,
    until_terminal,
)
from tests.features.editor_tasks.support import CROP, task

AREA = 1048576
HEIGHT, WIDTH = 400, 600
OBJECT = (150, 250, 250, 350)
SHADOW = (250, 275, 240, 370)


def clean_picture():
    rows = np.arange(HEIGHT, dtype=np.float32)[:, None]
    cols = np.arange(WIDTH, dtype=np.float32)[None, :]
    red = 40 + cols * 0.2 + rows * 0.0
    green = 60 + rows * 0.3 + cols * 0.0
    blue = np.full((HEIGHT, WIDTH), 120, dtype=np.float32)
    return np.rint(np.dstack([red, green, blue])).astype(np.uint8)


def scene():
    clean = clean_picture()
    picture = clean.copy()
    top, bottom, left, right = OBJECT
    picture[top:bottom, left:right] = (200, 30, 30)
    s_top, s_bottom, s_left, s_right = SHADOW
    picture[s_top:s_bottom, s_left:s_right] = (picture[s_top:s_bottom, s_left:s_right] * 0.55).astype(np.uint8)
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    mask[top:bottom, left:right] = 255
    return clean, picture, mask


def edit_with_fake_output(clean, picture, mask, strategy="crop", shadow=False, task_id="remove_object"):
    core = {item.id: item for item in CORE_TASKS}[task_id]
    region = regions.grow_region(mask, core.grow, shadow)
    geometry = regions.plan(
        picture.shape, region, strategy, 0.5, AREA, 16, feather=core.feather, matte=core.matte
    )
    cut = regions.prepare(picture, region, geometry)
    top, bottom, left, right = geometry.box
    work_w, work_h = geometry.working
    fake = Image.fromarray(resample_image(clean[top:bottom, left:right], work_w, work_h))
    if strategy == "mask":
        fake = Image.fromarray(resample_image(clean, work_w, work_h))
    patch = regions.stitch(cut.base, cut.region, fake, geometry)
    return region, geometry, cut, alpha_over(cut.base, patch)


def test_the_grown_mask_covers_the_object_with_a_margin_and_no_more():
    _, _, mask = scene()

    grown = regions.grow_region(mask, 0.04)

    assert (grown[mask > 0] == 255).all()
    assert grown[145, 300] == 255 and grown[255, 300] == 255 and grown[200, 245] == 255
    assert grown[142, 300] == 0 and grown[200, 242] == 0
    assert set(np.unique(grown).tolist()) == {0, 255}


def test_a_stitched_remove_is_the_fill_inside_the_original_far_away_and_has_no_seam():
    clean, picture, mask = scene()

    region, geometry, cut, result = edit_with_fake_output(clean, picture, mask)

    top, bottom, left, right = geometry.box
    target = clean[top:bottom, left:right].astype(int)
    object_rows = slice(OBJECT[0] - top, OBJECT[1] - top)
    object_cols = slice(OBJECT[2] - left, OBJECT[3] - left)
    assert np.abs(result.astype(int) - target)[object_rows, object_cols].max() <= 3

    grown_crop = cut.region > 0
    far = ~dilate_box(grown_crop, geometry.feather_px + 1)
    assert far.any()
    assert (result[far] == cut.base[far]).all()

    row = 200 - top
    stitched = result[row].astype(int)
    ideal = target[row]
    assert np.abs(np.diff(stitched, axis=0)).max() <= np.abs(np.diff(ideal, axis=0)).max() + 2
    assert np.abs(stitched - ideal).max() <= 3


def test_the_feather_of_a_remove_is_wider_than_the_default_one():
    _, _, mask = scene()
    box = (100, 300, 200, 400)

    plain = regions.plan((HEIGHT, WIDTH, 3), mask, "crop", 0.5, AREA, 16)
    wide = regions.plan((HEIGHT, WIDTH, 3), mask, "crop", 0.5, AREA, 16, feather=1.6)

    assert wide.feather_px == round(plain.feather_px * 1.6)
    assert regions.feather_for(box) == 12


def test_the_shadow_option_grows_the_mask_more_and_further_down():
    clean, picture, mask = scene()

    plain = regions.grow_region(mask, 0.04)
    shadowed = regions.grow_region(mask, 0.04, True)

    assert shadowed.sum() > plain.sum() * 1.25
    assert (plain <= shadowed).all()
    assert shadowed[255 + 25, 300] == 255 and plain[262, 300] == 0
    assert shadowed[145 - 3, 300] == 255 and plain[142, 300] == 0

    _, geometry, _, kept = edit_with_fake_output(clean, picture, mask)
    _, shadow_geometry, _, removed = edit_with_fake_output(clean, picture, mask, shadow=True)
    probe = (265, 300)
    left_over = abs(int(kept[probe[0] - geometry.box[0], probe[1] - geometry.box[2], 1]) - int(clean[probe][1]))
    gone = abs(int(removed[probe[0] - shadow_geometry.box[0], probe[1] - shadow_geometry.box[2], 1]) - int(clean[probe][1]))
    assert left_over > 10
    assert gone <= 4


def test_the_growth_is_capped_and_a_missing_region_is_left_alone():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:90, 10:90] = 255

    grown = regions.grow_region(mask, 0.04, True)

    assert grown[0, 50] == 0 or grown[0, 50] == 255
    assert regions.grow_region(None, 0.04) is None
    assert regions.grow_region(mask, 0.0) is mask
    assert regions.grow_region(np.zeros((10, 10), dtype=np.uint8), 0.04).sum() == 0


def test_the_mask_strategy_hands_the_grown_mask_to_the_mode():
    clean, picture, mask = scene()

    region, geometry, cut, result = edit_with_fake_output(clean, picture, mask, "mask")

    given = np.asarray(cut.mask)
    top, bottom, left, right = geometry.box
    assert (given == region).all()
    assert given.sum() > mask.sum()
    target = clean[top:bottom, left:right].astype(int)
    inside = (slice(OBJECT[0] - top, OBJECT[1] - top), slice(OBJECT[2] - left, OBJECT[3] - left))
    assert np.abs(result.astype(int) - target)[inside].max() <= 3


def soft_matte():
    matte = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    matte[100:300, 200:400] = 255
    ramp = np.linspace(255, 0, 40).astype(np.uint8)
    matte[100:300, 160:200] = ramp[::-1]
    matte[100:300, 400:440] = ramp
    return matte


def test_a_matte_stays_grayscale_and_its_stitch_alpha_is_the_matte():
    clean, picture, _ = scene()
    matte = soft_matte()
    image = Image.fromarray(matte)

    landed = regions.land_matte(image, (WIDTH, HEIGHT))
    geometry = regions.plan(picture.shape, landed, "crop", 0.5, AREA, 16, matte=True)
    cut = regions.prepare(picture, landed, geometry, with_mask=True)
    patch = regions.stitch(cut.base, cut.region, Image.fromarray(np.full((256, 256, 3), 90, dtype=np.uint8)), geometry)

    assert geometry.matte is True
    assert (np.unique(landed) > 0).sum() > 30
    assert (patch[..., 3] == cut.region).all()
    assert len(np.unique(patch[..., 3])) > 30
    assert set(np.unique(np.asarray(cut.mask)).tolist()) <= {0, 255}
    assert (np.asarray(cut.mask) > 0).any()


def test_a_matte_stitch_under_the_mask_strategy_keeps_the_soft_alpha_too():
    _, picture, _ = scene()
    landed = regions.land_matte(Image.fromarray(soft_matte()), (WIDTH, HEIGHT))
    geometry = regions.plan(picture.shape, landed, "mask", 0.5, AREA, 16, matte=True)
    cut = regions.prepare(picture, landed, geometry)

    patch = regions.stitch(cut.base, cut.region, Image.fromarray(np.full((256, 256, 3), 90, dtype=np.uint8)), geometry)

    assert (np.asarray(cut.mask) == np.where(landed >= regions.LANDED, 255, 0)).all()
    assert len(np.unique(patch[..., 3])) > 30


def test_a_matte_that_never_reaches_the_cutoff_lands_as_nothing():
    faint = Image.fromarray(np.full((20, 20), 100, dtype=np.uint8))

    assert regions.land_matte(faint, (20, 20)) is None
    assert regions.land_matte(None, (20, 20)) is None


def test_a_cutout_is_rgba_at_the_pictures_size_with_its_alpha_kept():
    alpha = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (32, 1))
    rgba = Image.fromarray(np.dstack([np.full((32, 64, 3), 90, dtype=np.uint8), alpha]), "RGBA")

    result = regions.cutout(rgba, (128, 64))

    assert result.shape == (64, 128, 4)
    assert result[..., 3].min() == 0 and result[..., 3].max() >= 250
    assert len(np.unique(result[..., 3])) > 30
    assert regions.cutout(Image.new("RGB", (8, 8), (1, 2, 3)), (8, 8))[..., 3].min() == 255


def test_a_saved_run_from_before_these_fields_still_loads():
    saved = {
        "strategy": "crop",
        "box": [10, 200, 20, 300],
        "working": [512, 512],
        "feather_px": 12,
        "whole": False,
        "has_region": True,
        "picture": [800, 600],
    }

    geometry = Geometry.from_dict(saved)

    assert geometry.matte is False and geometry.soft is False
    assert geometry.feather_px == 12 and geometry.box == (10, 200, 20, 300)
    assert Geometry.from_dict(geometry.to_dict()) == geometry
    assert Geometry.from_dict({**saved, "matte": True}).matte is True


def test_the_core_one_click_tasks_have_their_declared_shape():
    registry = register_core_tasks(TaskRegistry())

    remove = registry.get("remove_object")
    replace = registry.get("replace_background")
    find = registry.get("find_subject")

    assert (remove.grow, remove.feather, remove.matte, remove.internal) == (0.04, 1.6, False, False)
    assert (remove.region, remove.prompt) == ("required", "none")
    assert (replace.label, replace.icon, replace.region, replace.prompt) == (
        "Replace the background", "image", "required", "required",
    )
    assert (replace.matte, replace.grow, replace.feather) == (True, 0.0, 1.0)
    assert replace.fallback == ("edit_region", REPLACE_BACKGROUND_PROMPT)
    assert "{prompt}" in REPLACE_BACKGROUND_PROMPT
    assert (find.label, find.region, find.prompt, find.produces, find.internal) == (
        "Find the subject", "none", "none", "matte", True,
    )
    assert registry.get("edit_region").grow == 0.0 and registry.get("edit_region").internal is False


def test_a_manifest_entry_accepts_the_new_keys_and_defaults_the_old_ones():
    registry = TaskRegistry()
    registry.register_manifest_entry(
        "shiny",
        {"id": "shiny.mat", "label": "Mat", "produces": "matte", "grow": 0.1, "feather": 2, "matte": True, "internal": True},
    )
    registry.register_manifest_entry("shiny", {"id": "shiny.old", "label": "Old"})

    mat, old = registry.get("shiny.mat"), registry.get("shiny.old")

    assert (mat.produces, mat.grow, mat.feather, mat.matte, mat.internal) == ("matte", 0.1, 2.0, True, True)
    assert (old.grow, old.feather, old.matte, old.internal) == (0.0, 1.0, False, False)


def test_a_nonsense_manifest_value_falls_back_to_the_default():
    registry = TaskRegistry()
    registry.register_manifest_entry("shiny", {"id": "shiny.bad", "label": "Bad", "grow": "lots", "feather": -3})

    assert (registry.get("shiny.bad").grow, registry.get("shiny.bad").feather) == (0.0, 1.0)
    assert isinstance(TaskDefinition(id="x", label="X").grow, float)


@pytest.fixture
def world(mock_db, tmp_path):
    make_users()
    return tmp_path


def removing(**extra):
    return task(task="remove_object", instruction="Take it away.", **extra)


async def complete(manager, fakes, run, count=1, make=None):
    fakes.generation.status = "completed"
    for index in range(count):
        file_id = f"f{index + 1}"
        if make is None:
            fakes.add_result(file_id)
        else:
            path = fakes.root / f"{file_id}.png"
            make().save(path)
            fakes.files[file_id] = path
            fakes.generation_files.append(type("File", (), {"id": file_id})())
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": "completed"})
    return await until_terminal(manager, USER, run["id"])


def stored_region(fakes):
    upload = next(item for item in fakes.uploads if item["filename"] == "editor-region.png")
    return np.asarray(Image.open(fakes.files[upload["id"]]))


@pytest.mark.asyncio
async def test_a_promptless_task_runs_the_instruction_of_the_mode(world):
    manager, fakes = build_manager(world, {"A-CROP": removing()})
    edit_id = make_edit().id

    await manager.start(USER, "remove_object", body(edit_id, prompt=None))

    assert fakes.requests[0].prompts[0].positive == "Take it away."


@pytest.mark.asyncio
async def test_a_promptless_task_without_an_instruction_sends_no_prompt(world):
    manager, fakes = build_manager(world, {"A-CROP": task(task="remove_object")})
    edit_id = make_edit().id

    await manager.start(USER, "remove_object", body(edit_id, prompt=None))

    assert fakes.requests[0].prompts[0].positive == ""


@pytest.mark.asyncio
async def test_the_fallback_prompt_wins_over_the_instruction_of_another_mode(world):
    manager, fakes = build_manager(world, {"A-CROP": CROP})
    edit_id = make_edit().id

    await manager.start(USER, "remove_object", body(edit_id, prompt=None))

    assert fakes.requests[0].prompts[0].positive.startswith("Remove the marked object")


@pytest.mark.asyncio
async def test_the_fallback_of_replace_background_carries_the_users_prompt(world):
    manager, fakes = build_manager(world, {"A-CROP": CROP})
    edit_id = make_edit().id

    await manager.start(USER, "replace_background", body(edit_id, prompt="  a quiet beach at dusk  "))

    sent = fakes.requests[0].prompts[0].positive
    assert sent == REPLACE_BACKGROUND_PROMPT.replace("{prompt}", "a quiet beach at dusk")
    assert "{prompt}" not in sent


@pytest.mark.asyncio
async def test_replace_background_needs_the_new_background_described(world):
    manager, fakes = build_manager(world, {"A-CROP": CROP})
    edit_id = make_edit().id

    for missing in (None, "   "):
        with pytest.raises(DomainError) as caught:
            await manager.start(USER, "replace_background", body(edit_id, prompt=missing))
        assert caught.value.code == "prompt_required"
    assert fakes.requests == []


@pytest.mark.asyncio
async def test_a_fallback_without_the_placeholder_keeps_todays_behaviour(world):
    registry = register_core_tasks(TaskRegistry())
    registry.register_manifest_entry(
        "shiny", {"id": "shiny.tint", "label": "Tint", "fallback": {"task": "edit_region", "prompt": "Tint it blue."}}
    )
    manager, fakes = build_manager(world, {"A-CROP": CROP})
    manager.tasks.registry = registry
    edit_id = make_edit().id

    await manager.start(USER, "shiny.tint", body(edit_id, prompt=None))

    assert fakes.requests[0].prompts[0].positive == "Tint it blue."


@pytest.mark.asyncio
async def test_include_shadow_grows_the_region_the_mode_and_the_stitch_use(world):
    manager, fakes = build_manager(world, {"A-CROP": removing()})
    edit_id = make_edit().id

    plain = await manager.start(USER, "remove_object", body(edit_id, prompt=None))
    plain_region = stored_region(fakes)
    fakes.uploads.clear()
    fakes.by_hash.clear()
    shadowed = await manager.start(
        USER, "remove_object", body(edit_id, prompt=None, options={"include_shadow": True})
    )
    shadow_region = stored_region(fakes)

    original = 100 * 130
    assert (plain_region > 0).sum() > original
    assert (shadow_region > 0).sum() > (plain_region > 0).sum() * 1.25
    assert plain["id"] != shadowed["id"]


@pytest.mark.asyncio
async def test_options_default_to_none_and_other_values_do_not_enable_the_shadow(world):
    assert StartEditorRunRequest.model_validate(body(make_edit().id).model_dump()).options == {}
    manager, fakes = build_manager(world, {"A-CROP": removing()})
    edit_id = make_edit().id

    await manager.start(USER, "remove_object", body(edit_id, prompt=None))
    plain = (stored_region(fakes) > 0).sum()
    fakes.uploads.clear()
    fakes.by_hash.clear()
    await manager.start(USER, "remove_object", body(edit_id, prompt=None, options={"include_shadow": "yes"}))

    assert (stored_region(fakes) > 0).sum() == plain


@pytest.mark.asyncio
async def test_a_remove_run_stitches_a_patch_the_size_of_the_grown_crop(world):
    manager, fakes = build_manager(world, {"A-CROP": removing()})
    edit_id = make_edit().id

    run = await manager.start(USER, "remove_object", body(edit_id, prompt=None))
    done = await complete(manager, fakes, run)

    patch = np.asarray(Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]]))
    assert done["status"] == "done" and patch.shape[2] == 4
    assert manager.repository.find(run["id"]).crop["feather_px"] == regions.plan(
        (400, 500, 3), stored_region(fakes), "crop", 0.5, AREA, 16, feather=1.6
    ).feather_px


@pytest.mark.asyncio
async def test_a_replace_background_run_keeps_the_soft_matte_as_the_patch_alpha(world):
    manager, fakes = build_manager(world, {"A-CROP": task(task="replace_background")})
    edit_id = make_edit().id
    matte = np.zeros((400, 500), dtype=np.uint8)
    matte[100:300, 100:300] = 255
    matte[100:300, 300:360] = np.linspace(255, 0, 60).astype(np.uint8)
    Image.fromarray(matte).save(fakes.files["region"])

    run = await manager.start(USER, "replace_background", body(edit_id, prompt="a beach"))
    done = await complete(manager, fakes, run)

    crop_matte = stored_region(fakes)
    patch = np.asarray(Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]]))
    assert manager.repository.find(run["id"]).crop["matte"] is True
    assert len(np.unique(crop_matte)) > 30
    assert patch.shape[:2] == crop_matte.shape
    assert (patch[..., 3] == crop_matte).all()
    assert fakes.requests[0].prompts[0].positive == "a beach"


@pytest.mark.asyncio
async def test_find_subject_returns_an_rgba_cutout_at_the_pictures_size(world):
    manager, fakes = build_manager(world, {"A-CROP": task(task="find_subject", region="whole")})
    edit_id = make_edit().id
    alpha = np.tile(np.linspace(0, 255, 128, dtype=np.uint8), (64, 1))

    def cutout():
        return Image.fromarray(np.dstack([np.full((64, 128, 3), 70, dtype=np.uint8), alpha]), "RGBA")

    run = await manager.start(USER, "find_subject", body(edit_id, prompt=None, region=False))
    done = await complete(manager, fakes, run, make=cutout)

    result = Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]])
    assert done["status"] == "done"
    assert result.mode == "RGBA" and result.size == (500, 400)
    levels = np.asarray(result)[..., 3]
    assert levels.min() == 0 and levels.max() >= 250
    assert len(np.unique(levels)) > 30
    assert done["rect"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


@pytest.mark.asyncio
async def test_a_task_listing_exposes_internal_matte_and_grow(world):
    manager, _ = build_manager(world, {"A-CROP": CROP})

    entries = {entry["id"]: entry for entry in manager.tasks.offers(USER)}

    assert entries["find_subject"]["internal"] is True and entries["find_subject"]["matte"] is False
    assert entries["replace_background"]["matte"] is True and entries["replace_background"]["internal"] is False
    assert entries["remove_object"]["grow"] == 0.04
    assert entries["edit_region"]["grow"] == 0.0
    assert entries["replace_background"]["fallback"]["prompt"] == REPLACE_BACKGROUND_PROMPT


def test_an_instruction_is_read_from_the_mode_file_and_lints_only_for_a_promptless_task(tmp_path):
    from src.features.presets.editor_mode_lint import lint_editor_mode
    from tests.features.editor_tasks.test_manager import build

    manager = build(
        tmp_path,
        {"A-CROP": [removing(), task(instruction="Describe it nicely.")], "B-MASK": CROP},
        visible=lambda user: {"A-CROP", "B-MASK"},
    )
    entries = {entry.name: entry for entry in manager.editor_entries() if entry.template.id == "A-CROP"}
    registry = manager.registry

    def check(entry):
        return lint_editor_mode(
            entry.template, entry.name, entry.mode,
            lambda task_id: registry.get(task_id).prompt, lambda task_id: ("image",), None,
        )

    promptless, prompted = entries["edit"], entries["edit2"]
    assert promptless.spec.instruction == "Take it away."
    assert prompted.spec.instruction == "Describe it nicely."
    assert [message for level, message in check(promptless) if level == "warning"] == []
    assert any("instruction is only used" in message for level, message in check(prompted) if level == "warning")
