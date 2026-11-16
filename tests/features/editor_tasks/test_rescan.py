import dataclasses
from types import SimpleNamespace

import pytest

from src.features.editor_tasks.declarations import DECLARATION_FILE, DeclarationCache
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskDefinition, TaskRegistry, register_core_tasks
from tests.features.editor_tasks.support import CROP, MASK, declaration, load, task, write_preset

USER = SimpleNamespace(id="u1")


class World:
    def __init__(self, tmp_path, presets):
        root = tmp_path / "presets"
        root.mkdir()
        self.dirs = {preset_id: write_preset(root, editor, preset_id=preset_id) for preset_id, editor in presets.items()}
        self.templates = list(load(root).all())
        self.tasks = register_core_tasks(TaskRegistry())
        self.manager = EditorTaskManager(
            self.tasks,
            lambda: list(self.templates),
            DeclarationCache(lambda: list(self.templates), dict),
            lambda user: {template.id for template in self.templates},
            lambda engine: True,
        )

    def offered(self, task_id="edit_region"):
        entries = self.manager.offers(USER)
        return [offer["preset_id"] for item in entries if item["id"] == task_id for offer in item["offers"]]

    def remove_preset(self, preset_id):
        self.templates = [template for template in self.templates if template.id != preset_id]

    def replace(self, preset_id, **changes):
        self.templates = [
            dataclasses.replace(template, **changes) if template.id == preset_id else template
            for template in self.templates
        ]


def test_a_removed_preset_stops_being_offered(tmp_path):
    world = World(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    assert world.offered() == ["A-CROP", "B-MASK"]

    world.remove_preset("A-CROP")

    assert world.offered() == ["B-MASK"]
    assert [result.preset_id for _, result in world.manager.mode_declarations()] == ["B-MASK"]


def test_a_removed_mode_stops_being_offered(tmp_path):
    world = World(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    world.replace("A-CROP", modes={})

    assert world.offered() == ["B-MASK"]
    assert world.manager.owned_fields("A-CROP", "edit") == set()


def test_a_reloaded_preset_replaces_the_old_declaration(tmp_path):
    world = World(tmp_path, {"A-CROP": CROP})
    assert world.offered() == ["A-CROP"]

    (world.dirs["A-CROP"] / "modes" / "edit" / DECLARATION_FILE).write_text("schema_version: 9\n", encoding="utf-8")

    assert world.offered() == []
    assert world.manager.mode_declarations()[0][1].errors


def test_a_deleted_file_stops_the_offer(tmp_path):
    world = World(tmp_path, {"A-CROP": CROP})
    assert world.offered() == ["A-CROP"]

    (world.dirs["A-CROP"] / "modes" / "edit" / DECLARATION_FILE).unlink()

    assert world.offered() == []


def test_a_disabled_plugin_takes_its_task_and_its_presets_away(tmp_path):
    world = World(tmp_path, {"A-CROP": CROP, "P-PLUG": declaration(task(task="shiny.colorize"))})
    world.tasks.register(TaskDefinition(id="shiny.colorize", label="Colorize", source="shiny"))

    assert world.offered("shiny.colorize") == ["P-PLUG"]

    world.tasks.unregister_source("shiny")
    world.remove_preset("P-PLUG")

    assert "shiny.colorize" not in {item["id"] for item in world.manager.offers(USER)}
    assert world.offered() == ["A-CROP"]
    assert world.manager.owned_fields("P-PLUG", "edit") == set()
