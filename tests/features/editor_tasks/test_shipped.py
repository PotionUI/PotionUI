from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.presets.editor_mode_lint import lint_editor_mode
from src.features.presets.loader import PresetTemplateLoader

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


def test_the_set_of_shipped_editor_modes_is_exactly_the_nine():
    found = {
        (path.parents[2].name, path.parent.name)
        for path in MARKETPLACE.glob("*/editor_modes/*/editor_mode.yml")
        if "region: range" not in path.read_text()
    }

    assert found == set(EXPECTED)


def test_no_editor_yml_is_left_in_a_shipped_preset():
    assert sorted(MARKETPLACE.glob("*/modes/*/editor.yml")) == []


def test_every_shipped_editor_mode_is_offered_by_the_manager(templates):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )

    offered = {
        (Path(entry.template.path).name, entry.name) for entry in manager.editor_entries() if entry.spec.region != "range"
    }

    assert offered == set(EXPECTED)


@pytest.mark.parametrize("folder, name", sorted(EXPECTED))
def test_the_editor_mode_carries_the_client_facing_fields(templates, folder, name):
    manager = EditorTaskManager(
        REGISTRY, lambda: list(templates.values()), lambda user: {t.id for t in templates.values()}, lambda engine: True
    )
    template = templates[folder]

    offer = next(
        offer
        for entry in manager.offers(type("User", (), {"id": "u"})())
        if entry["id"] == "edit_region"
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
        assert offer["mode_icon"] == {"edit": "pencil", "inpaint": "brush"}[offer["from_mode"]]
