from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.presets.editor_mode_lint import lint_editor_mode
from src.features.presets.loader import PresetTemplateLoader
from tests.features.editor_tasks.parts_support import (
    SCALE_FIELD,
    UPSCALE,
    build_parts_manager,
    gradient,
    until,
    upscale_body,
)
from tests.features.editor_tasks.run_support import USER, make_edit, make_users, until_terminal
from tests.features.editor_tasks.support import BASE_FIELDS, load, write_preset

MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "presets" / "marketplace"
REGISTRY = register_core_tasks(TaskRegistry())


def need(task_id):
    found = REGISTRY.get(task_id)
    return found.prompt if found else None


def lint(tmp_path, editor):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, editor, fields=[*BASE_FIELDS, SCALE_FIELD], preset_id="LINT-MODE")
    template = load(root).all()[0]
    mode = template.editor_modes["edit"]
    return [message for level, message in lint_editor_mode(template, "edit", mode, need, lambda task: ("image",), None) if level == "error"]


def upscale(**changes):
    return {**UPSCALE, **changes}


def test_an_upscale_mode_with_a_scale_field_is_clean(tmp_path):
    assert lint(tmp_path, upscale()) == []


def test_an_upscale_mode_with_a_resolution_field_instead_is_clean(tmp_path):
    editor = upscale(inputs={"source": "source_image"}, size={"area": 262144, "field": "resolution"})

    assert lint(tmp_path, editor) == []


def test_an_upscale_mode_that_receives_no_size_is_an_error(tmp_path):
    problems = lint(tmp_path, upscale(inputs={"source": "source_image"}))

    assert len(problems) == 1 and "inputs.scale or size.field" in problems[0]


def test_a_scale_field_that_does_not_exist_is_an_error(tmp_path):
    problems = lint(tmp_path, upscale(inputs={"source": "source_image", "scale": "nope"}))

    assert any("inputs.scale names 'nope'" in problem for problem in problems)


def test_a_scale_field_that_is_not_a_number_is_an_error(tmp_path):
    problems = lint(tmp_path, upscale(inputs={"source": "source_image", "scale": "match_source"}))

    assert any("inputs.scale 'match_source' is a checkbox field" in problem for problem in problems)


def test_the_scale_field_is_owned_by_the_editor_so_settings_hides_it(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, UPSCALE, fields=[*BASE_FIELDS, SCALE_FIELD], preset_id="OWN-SCALE")
    loader = load(root)
    manager = EditorTaskManager(REGISTRY, lambda: loader.all(), lambda user: {"OWN-SCALE"}, lambda engine: True)

    assert "upscale_factor" in manager.owned_fields("OWN-SCALE", "editor:edit")


@pytest.fixture(scope="module")
def shipped():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    return EditorTaskManager(
        REGISTRY, lambda: loader.all(), lambda user: {t.id for t in loader.all()}, lambda engine: True
    )


def test_the_shipped_upscalers_offer_the_factors_their_forms_accept(shipped):
    entry = next(item for item in shipped.offers(SimpleNamespace(id="u")) if item["id"] == "upscale")

    scales = {offer["preset_name"]: offer["scale"] for offer in entry["offers"]}

    assert scales == {
        "SDXL": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Anima": {"field": "upscale_factor", "min": 2.0, "max": 4.0},
        "Flux1": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Flux2": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Krea-2": {"field": "upscale_factor", "min": 2.0, "max": 4.0},
        "MOSS-Image": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Qwen-Image": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Qwen-Image-2.1": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "Z-Image": {"field": "upscale_factor", "min": 1.5, "max": 4.0},
        "SeedVR2": {"field": "scale", "min": 1.0, "max": 4.0},
    }
    assert entry["plan"] == "tiles" and entry["region"] == "none" and entry["prompt"] == "none"


def test_the_shipped_upscale_modes_pin_the_size_the_server_sends(shipped):
    entries = {entry.template.name: entry for entry in shipped.editor_entries() if entry.spec.task == "upscale"}

    assert entries["SeedVR2"].spec.fixed == {"target_short_side": 0}
    assert "resolution_target" in entries["SeedVR2"].spec.hide
    assert entries["SeedVR2"].spec.inputs["scale"] == "scale"
    assert entries["SDXL"].spec.inputs["scale"] == "upscale_factor"
    assert {entry.spec.region for entry in entries.values()} == {"whole"}


@pytest.mark.asyncio
async def test_a_mode_with_a_resolution_field_gets_each_tiles_output_size(mock_db, tmp_path):
    make_users()
    editor = upscale(inputs={"source": "source_image"}, size={"area": 262144, "field": "resolution"})
    manager, fakes, tasks = build_parts_manager(tmp_path, {"SIZE-MODE": editor})
    gradient(1024, 1024).save(fakes.files["picture"])

    run = await manager.start(USER, "upscale", upscale_body(make_edit().id, preset="SIZE-MODE", scale=2))
    await until(lambda: len(fakes.requests) == 1)

    assert run["parts"]["total"] == 9
    source = Image.open(fakes.files[fakes.requests[0].form_data["source_image"].split("/")[-1].split(".")[0]])
    assert fakes.requests[0].form_data["resolution"] == f"{source.width * 2}x{source.height * 2}"
    assert "upscale_factor" not in fakes.requests[0].form_data
    await manager.cancel(USER, run["id"])
    assert (await until_terminal(manager, USER, run["id"]))["status"] == "cancelled"
