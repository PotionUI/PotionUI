from types import SimpleNamespace

import pytest

from src.features.editor_tasks.manager import NO_BACKEND, EditorTaskManager
from src.features.editor_tasks.registry import REMOVE_OBJECT_PROMPT, TaskDefinition, TaskRegistry, register_core_tasks
from tests.features.editor_tasks.support import CROP, MASK, declaration, load, task, write_preset

USER = SimpleNamespace(id="u1")
OTHER = SimpleNamespace(id="u2")
SEEN = {"u1": {"A-CROP", "B-MASK"}, "u2": {"B-MASK"}}


def build(tmp_path, presets, tasks=None, visible=None, engine_ok=lambda engine: True):
    root = tmp_path / "presets"
    root.mkdir()
    for preset_id, editor in presets.items():
        write_preset(root, editor, preset_id=preset_id)
    loader = load(root)
    registry = tasks or register_core_tasks(TaskRegistry())
    manager = EditorTaskManager(
        registry,
        lambda: loader.all(),
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
        ("A-CROP", "editor:edit", "crop", True, None),
        ("B-MASK", "editor:edit", "mask", True, None),
    ]
    assert offers[0]["preset_name"] == "Preset A-CROP"
    assert offers[0]["mode_label"]
    assert offers[0]["from_mode"] == "edit"
    assert offers[0]["order"] == 0
    assert "variant" not in offers[0] and "owned" not in offers[0]
    assert "reason" not in offers[0]
    assert offers[0]["quantity_field"] == "quantity"


def test_an_offer_carries_the_mode_icon_beside_the_label(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": {**MASK, "icon": "brush"}})

    offers = entry(manager.offers(USER), "edit_region")["offers"]

    assert [(o["mode_icon"], o["mode_label"]) for o in offers] == [("pencil", "Edit"), ("brush", "Edit")]


def test_an_offer_carries_the_short_description_and_the_order_and_sorts_by_them(tmp_path):
    manager = build(
        tmp_path,
        {"A-CROP": [task(order=5, short_description="Second"), task(order=1, short_description="First", label="Quick")]},
    )

    offers = entry(manager.offers(USER), "edit_region")["offers"]

    assert [(o["mode"], o["order"], o["short_description"], o["label"]) for o in offers] == [
        ("editor:edit2", 1, "First", "Quick"),
        ("editor:edit", 5, "Second", offers[1]["mode_label"]),
    ]


def test_a_preset_the_user_may_not_use_is_absent(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": MASK})

    assert [o["preset_id"] for o in entry(manager.offers(OTHER), "edit_region")["offers"]] == ["B-MASK"]
    assert [o["preset_id"] for o in entry(manager.offers(USER), "edit_region")["offers"]] == ["A-CROP", "B-MASK"]


def test_a_user_who_may_use_nothing_gets_empty_offers_but_the_tasks(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP}, visible=lambda user: set())

    entries = manager.offers(USER)

    assert {"edit_region", "remove_object"} <= {item["id"] for item in entries}
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


REMOVE_MASK = task(task="remove_object", region="mask", inputs={"source": "source_image", "mask": "source_image_inpaint_mask"})


def test_the_fallback_applies_per_preset_a_preset_with_its_own_mode_keeps_it_and_the_others_use_the_fallback(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": REMOVE_MASK})

    remove = entry(manager.offers(USER), "remove_object")

    assert [(o["preset_id"], o["mode"], o["via"]) for o in remove["offers"]] == [
        ("A-CROP", "editor:edit", "edit_region"),
        ("B-MASK", "editor:edit", None),
    ]
    assert remove["fallback"]["task"] == "edit_region"


def test_a_preset_that_ships_its_own_mode_does_not_also_offer_its_edit_mode_as_the_fallback(tmp_path):
    manager = build(tmp_path, {"B-MASK": [REMOVE_MASK, task()]})

    remove = entry(manager.offers(USER), "remove_object")

    assert [(o["mode"], o["via"]) for o in remove["offers"]] == [("editor:edit", None)]
    assert [o["mode"] for o in entry(manager.offers(USER), "edit_region")["offers"]] == ["editor:edit2"]


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


def test_owned_fields_are_the_inputs_the_fixed_values_and_the_hidden_fields(tmp_path):
    manager = build(
        tmp_path,
        {"A-CROP": task(fixed={"output_size": "match"}, fields={"hide": ["resolution"]})},
    )

    expected = {"source_image", "output_size", "resolution"}
    assert manager.owned_fields("A-CROP", "editor:edit") == expected
    assert manager.owned_fields("A-CROP", "edit") == set()
    assert manager.owned_fields("A-CROP", "editor:missing") == set()
    assert manager.owned_fields("NOPE", "editor:edit") == set()


def test_the_mode_status_tells_unknown_from_not_offered(tmp_path):
    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": task(task="ghost.relight")})

    assert manager.mode_status("A-CROP", "editor:edit") == "ok"
    assert manager.mode_status("A-CROP", "edit") == "unknown"
    assert manager.mode_status("A-CROP", "editor:nope") == "unknown"
    assert manager.mode_status("NOPE", "editor:edit") == "unknown"
    assert manager.mode_status("B-MASK", "editor:edit") == "not_offered"


def test_a_generation_mode_is_not_an_editor_mode(tmp_path):
    manager = build(tmp_path, {"A-CROP": None})

    assert manager.editor_entries() == []
    assert manager.mode_status("A-CROP", "editor:edit") == "unknown"
    assert all(item["offers"] == [] for item in manager.offers(USER))


def test_a_mode_name_without_the_prefix_is_a_plain_422(tmp_path):
    from src.platform.http.errors import DomainError

    manager = build(tmp_path, {"A-CROP": CROP})

    for name in ("missing", "edit", "edit_region"):
        with pytest.raises(DomainError) as caught:
            manager.resolve_offer(USER, "edit_region", "A-CROP", name)
        assert caught.value.status_code == 422 and caught.value.code == "task_not_offered"
    assert manager.mode_status("A-CROP", "edit") == "unknown"
    assert manager.owned_fields("A-CROP", "edit") == set()


def test_the_fallback_resolves_for_a_preset_without_its_own_mode_only(tmp_path):
    from src.platform.http.errors import DomainError

    manager = build(tmp_path, {"A-CROP": CROP, "B-MASK": [REMOVE_MASK, task()]})

    assert manager.resolve_offer(USER, "remove_object", "A-CROP", "editor:edit").via == "edit_region"
    assert manager.resolve_offer(USER, "remove_object", "B-MASK", "editor:edit").via is None
    with pytest.raises(DomainError):
        manager.resolve_offer(USER, "remove_object", "B-MASK", "editor:edit2")
