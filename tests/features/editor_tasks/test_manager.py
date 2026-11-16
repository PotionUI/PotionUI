from types import SimpleNamespace

import pytest

from src.features.editor_tasks.declarations import DeclarationCache
from src.features.editor_tasks.manager import NO_BACKEND, EditorTaskManager
from src.features.editor_tasks.registry import REMOVE_OBJECT_PROMPT, TaskDefinition, TaskRegistry, register_core_tasks
from tests.features.editor_tasks.support import CROP, MASK, declaration, load, task, write_preset

USER = SimpleNamespace(id="u1")
OTHER = SimpleNamespace(id="u2")
SEEN = {"u1": {"A-CROP", "B-MASK"}, "u2": {"B-MASK"}}


def build(tmp_path, presets, tasks=None, visible=None, engine_ok=lambda engine: True, contributions=dict):
    root = tmp_path / "presets"
    root.mkdir()
    for preset_id, editor in presets.items():
        write_preset(root, editor, preset_id=preset_id)
    loader = load(root)
    registry = tasks or register_core_tasks(TaskRegistry())
    manager = EditorTaskManager(
        registry,
        lambda: loader.all(),
        DeclarationCache(lambda: loader.all(), contributions),
        visible or (lambda user: SEEN[user.id]),
        engine_ok,
    )
    return manager


def entry(entries, task_id):
    return next(item for item in entries if item["id"] == task_id)


def test_offers_name_the_modes_and_their_strategy(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    offers = entry(manager.offers(USER), "edit_region")["offers"]

    assert [(o["preset_id"], o["mode"], o["region_strategy"], o["qualifies"], o["via"]) for o in offers] == [
        ("A-CROP", "edit", "crop", True, None),
        ("B-MASK", "edit", "mask", True, None),
    ]
    assert offers[0]["preset_name"] == "Preset A-CROP"
    assert offers[0]["mode_label"]
    assert offers[0]["variant"]
    assert "reason" not in offers[0]


def test_a_preset_the_user_may_not_use_is_absent(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    assert [o["preset_id"] for o in entry(manager.offers(OTHER), "edit_region")["offers"]] == ["B-MASK"]
    assert [o["preset_id"] for o in entry(manager.offers(USER), "edit_region")["offers"]] == ["A-CROP", "B-MASK"]


def test_a_user_who_may_use_nothing_gets_empty_offers_but_the_tasks(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP}, visible=lambda user: set())

    entries = manager.offers(USER)

    assert {item["id"] for item in entries} == {"edit_region", "remove_object"}
    assert all(item["offers"] == [] for item in entries)


def test_default_is_null(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP})

    assert all(item["default"] is None for item in manager.offers(USER))


def test_remove_object_falls_back_to_edit_region_offers(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    remove = entry(manager.offers(USER), "remove_object")

    assert remove["fallback"] == {"task": "edit_region", "prompt": REMOVE_OBJECT_PROMPT}
    assert remove["prompt"] == "none"
    assert [(o["preset_id"], o["region_strategy"], o["via"]) for o in remove["offers"]] == [
        ("A-CROP", "crop", "edit_region"),
        ("B-MASK", "mask", "edit_region"),
    ]


def test_the_fallback_does_not_apply_when_a_mode_declares_the_task(tmp_path):
    manager = build(
        tmp_path,
        {"A-CROP": CROP, "B-MASK": declaration(task(task="remove_object", region="mask", inputs={"image": "source_image", "mask": "source_image_inpaint_mask"}))},
    )

    remove = entry(manager.offers(USER), "remove_object")

    assert [(o["preset_id"], o["via"]) for o in remove["offers"]] == [("B-MASK", None)]
    assert remove["fallback"]["task"] == "edit_region"


def test_the_fallback_follows_visibility(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    assert [o["preset_id"] for o in entry(manager.offers(OTHER), "remove_object")["offers"]] == ["B-MASK"]


def test_a_mode_without_a_backend_does_not_qualify(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP}, engine_ok=lambda engine: False)

    offer = entry(manager.offers(USER), "edit_region")["offers"][0]

    assert offer["qualifies"] is False
    assert offer["reason"] == NO_BACKEND


def test_an_invalid_file_offers_nothing(tmp_path):
    manager = build(tmp_path, {"A-CROP": "schema_version: 3\n", "B-MASK": MASK})

    assert [o["preset_id"] for o in entry(manager.offers(USER), "edit_region")["offers"]] == ["B-MASK"]


def test_a_plugin_task_is_offered_only_while_registered(tmp_path):
    tasks = register_core_tasks(TaskRegistry())
    tasks.register(TaskDefinition(id="shiny.colorize", label="Colorize", source="shiny", region="required"))
    manager = build(
        tmp_path,
        {"A-CROP": declaration(task(task="shiny.colorize"))},
        tasks=tasks,
    )

    assert [o["preset_id"] for o in entry(manager.offers(USER), "shiny.colorize")["offers"]] == ["A-CROP"]

    tasks.unregister_source("shiny")

    assert "shiny.colorize" not in {item["id"] for item in manager.offers(USER)}


def test_owned_fields_are_the_union_over_the_offers(tmp_path):
    manager = build(
        tmp_path,
        {
            "A-CROP": declaration(
                task(set={"output_size": "match"}),
                task(task="remove_object", set={"match_source": True}),
            )
        },
    )

    assert manager.owned_fields("A-CROP", "edit") == {"source_image", "output_size", "match_source", "quantity"}
    assert manager.owned_fields("A-CROP", "missing") == set()


def test_modes_without_a_file_are_ignored(tmp_path):
    manager = build(tmp_path, {"A-CROP": None})

    assert manager.mode_declarations() == []
    assert all(item["offers"] == [] for item in manager.offers(USER))
