from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.presets.editor_mode_lint import lint_editor_mode
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.mode_labels import mode_display_icon

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"

EXPECTED = {
    ("Flux3", "edit"): "crop",
    ("QwenImage", "edit"): "crop",
    ("MiniMax-H3-Image", "edit"): "crop",
    ("Krea2", "edit"): "crop",
    ("QwenImage-2.1", "edit"): "crop",
    ("QwenImage-2.1", "inpaint"): "mask",
    ("ZImage", "inpaint"): "mask",
    ("MOSS-Image", "inpaint"): "mask",
    ("SDXL", "inpaint"): "mask",
    ("QwenImage-2.1", "expand"): "mask",
    ("ZImage", "expand"): "mask",
    ("MOSS-Image", "expand"): "mask",
    ("SDXL", "expand"): "mask",
    ("SDXL", "fix_faces"): "crop",
    ("SDXL", "upscale"): "whole",
    ("SeedVR2", "upscale"): "whole",
}

ONE_CLICK = {
    ("ZImage", "remove_object"): ("remove_object", "mask", "inpaint"),
    ("QwenImage-2.1", "remove_object"): ("remove_object", "mask", "inpaint"),
    ("QwenImage", "remove_object"): ("remove_object", "crop", "edit"),
    ("Krea2", "remove_object"): ("remove_object", "crop", "edit"),
    ("ZImage", "replace_background"): ("replace_background", "mask", "inpaint"),
    ("QwenImage-2.1", "replace_background"): ("replace_background", "mask", "inpaint"),
    ("QwenImage", "replace_background"): ("replace_background", "crop", "edit"),
    ("Krea2", "replace_background"): ("replace_background", "crop", "edit"),
    ("ImageTools", "find_subject"): ("find_subject", "whole", "remove_background"),
}

REGISTRY = register_core_tasks(TaskRegistry())


def need(task_id):
    found = REGISTRY.get(task_id)
    return found.prompt if found else None


def editors(task_id):
    return ("image",)


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader([str(MARKETPLACE)])
    loader.load_presets()
    return {Path(template.path).name: template for template in loader.all()}


@pytest.mark.parametrize("folder, name", sorted(EXPECTED))
def test_every_named_field_exists_and_nothing_is_reported(templates, folder, name):
    template = templates[folder]
    mode = template.editor_modes[name]

    issues = lint_editor_mode(template, name, mode, need, editors, None)

    assert [message for level, message in issues if level != "info"] == []
    assert mode.editor.region == EXPECTED[(folder, name)]
    assert mode.editor.from_mode in template.modes


def test_the_set_of_shipped_editor_modes_is_exactly_the_nine_and_the_one_click_ones():
    found = {
        (path.parents[2].name, path.parent.name)
        for path in MARKETPLACE.glob("*/editor_modes/*/editor_mode.yml")
        if "region: range" not in path.read_text()
    }

    assert found == set(EXPECTED) | set(ONE_CLICK)


@pytest.mark.parametrize("folder, name", sorted(ONE_CLICK))
def test_every_one_click_mode_names_its_task_and_lints_clean(templates, folder, name):
    task, region, from_mode = ONE_CLICK[(folder, name)]
    template = templates[folder]
    mode = template.editor_modes[name]

    issues = lint_editor_mode(template, name, mode, need, editors, None)

    assert [message for level, message in issues if level != "info"] == []
    assert (mode.editor.task, mode.editor.region, mode.editor.from_mode) == (task, region, from_mode)
    assert bool(mode.editor.instruction) == (task == "remove_object")


def test_no_editor_yml_is_left_in_a_shipped_preset():
    assert sorted(MARKETPLACE.glob("*/modes/*/editor.yml")) == []


def test_every_shipped_editor_mode_is_offered_by_the_manager(templates):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )

    offered = {
        (Path(entry.template.path).name, entry.name) for entry in manager.editor_entries() if entry.spec.region != "range"
    }

    assert offered == set(EXPECTED) | set(ONE_CLICK)


@pytest.mark.parametrize("folder, name", sorted(EXPECTED))
def test_the_editor_mode_carries_the_client_facing_fields(templates, folder, name):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )
    template = templates[folder]

    offer = next(
        offer
        for entry in manager.offers(type("User", (), {"id": "u"})())
        if entry["id"] == template.editor_modes[name].editor.task
        for offer in entry["offers"]
        if offer["preset_id"] == template.id and offer["mode"] == f"editor:{name}"
    )

    assert "variant" not in offer and "owned" not in offer
    assert offer["from_mode"] == template.editor_modes[name].editor.from_mode


def test_every_shipped_offer_carries_its_from_modes_icon_and_a_label(templates):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )
    offers = [offer for item in manager.offers(SimpleNamespace(id="u")) for offer in item["offers"]]

    assert len(offers) >= len(EXPECTED)
    for offer in offers:
        assert offer["mode_label"]
        assert offer["mode_icon"] == {"edit": "pencil", "inpaint": "brush"}.get(
            offer["from_mode"], mode_display_icon(offer["from_mode"])
        )


def test_the_one_click_tasks_are_offered_by_exactly_the_declaring_presets(templates):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )
    by_id = {entry["id"]: entry for entry in manager.offers(SimpleNamespace(id="u"))}
    names = {template.id: Path(template.path).name for template in templates.values()}

    for task in ("remove_object", "replace_background", "find_subject"):
        direct = {names[offer["preset_id"]] for offer in by_id[task]["offers"] if offer["via"] is None}
        assert direct == {folder for (folder, _), (wanted, _, _) in ONE_CLICK.items() if wanted == task}
    assert by_id["find_subject"]["internal"] is True
    assert by_id["replace_background"]["matte"] is True
    assert by_id["remove_object"]["grow"] == 0.04
