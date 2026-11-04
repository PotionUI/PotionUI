import pytest

from src.features.elixirs.errors import AssistantRefused
from src.features.elixirs.repository import ElixirRepository
from tests.features.elixirs.assistant_world import (
    KREA,
    build_world,
    create,
    photos_into_sharpen,
    save_session,
    shoot_and_sharpen,
    start_review_brew,
)


@pytest.fixture
def world(scratch_db, port, catalog, contracts):
    return build_world(port, catalog, contracts)


def stored(record):
    return ElixirRepository().get(record.id, "alice")


def node(definition, node_id):
    return next(item for item in definition["nodes"] if item["id"] == node_id)


def edit(world, record, *operations, revision=None):
    return world.assistant.apply("alice", record.id, revision or record.revision, list(operations))


def refused(world, record, *operations, revision=None):
    with pytest.raises(AssistantRefused) as caught:
        edit(world, record, *operations, revision=revision)
    return str(caught.value)


def test_the_summary_lists_steps_left_to_right_with_their_sessions(world):
    record = create(shoot_and_sharpen())

    summary = world.assistant.summary("alice", record.id)

    assert summary["revision"] == record.revision
    assert summary["brew_mode_label"] == "Run all branches"
    steps = {step["id"]: step for step in summary["steps"]}
    assert steps["shoot"]["column"] == 0 and steps["sharpen"]["column"] == 1
    assert steps["shoot"]["session"] == {"mode": "copy", "name": "Old shoot"}
    assert steps["sharpen"]["session"] == {"mode": "new", "edited": ["prompt"]}
    assert steps["sharpen"]["inputs"] == {"source_image": "shoot"}
    assert summary["connections"][0]["from"] == "shoot"
    assert summary["last_brew"] is None


def test_the_summary_can_list_the_usable_presets(world):
    record = create(shoot_and_sharpen())

    presets = world.assistant.summary("alice", record.id, include_presets=True)["usable_presets"]

    enhance = next(item for item in presets if item["preset_id"] == KREA and item["mode"] == "enhance")
    assert enhance["takes"] == ["image"] and enhance["makes"] == ["image"]


def test_a_copy_step_keeps_settings_on_the_step(world):
    record = create(shoot_and_sharpen())

    result = edit(world, record, {"op": "set_settings", "step": "shoot", "fields": {"seed": 42}, "prompt": "a green bottle"})

    saved = node(stored(record).definition, "shoot")
    assert saved["overrides"]["formData"] == {"seed": 42}
    assert saved["overrides"]["prompt"] == "a green bottle"
    assert saved["overrides"]["promptSegments"] == []
    assert result["revision"] == record.revision + 1
    assert world.notifier.sent[-1] == ("alice", {"type": "elixir_changed", "elixir_id": record.id, "revision": record.revision + 1})


def test_a_following_step_saves_into_its_session_with_the_version_it_saw(world):
    save_session(world)
    record = create(shoot_and_sharpen(session_mode="new", session_source=None, session_snapshot=None))
    edit(world, record, {"op": "set_session", "step": "shoot", "mode": "follow", "session": "Bottle shoot"})
    record = stored(record)
    assert node(record.definition, "shoot")["session_ref"]["version"] == 1

    result = edit(world, record, {"op": "set_settings", "step": "shoot", "fields": {"seed": 99}})

    session = world.sessions.get_by_id("s1")
    assert session.version == 2
    assert session.data["txt2img"]["formData"] == {"quantity": 1, "seed": 99}
    assert session.data["txt2img"]["leftPanelWidth"] == 300
    after = stored(record)
    assert after.revision == record.revision
    assert node(after.definition, "shoot")["session_snapshot"]["formData"]["seed"] == 99
    assert node(after.definition, "shoot").get("overrides") == {}
    assert result["sessions_written"] == [{"session": "Bottle shoot", "version": 2}]
    types = [message["type"] for _, message in world.notifier.sent]
    assert "session_changed" in types and types[-1] == "elixir_changed"


def test_a_followed_session_changed_elsewhere_is_refused(world):
    save_session(world)
    record = create(shoot_and_sharpen(session_mode="new", session_source=None, session_snapshot=None))
    edit(world, record, {"op": "set_session", "step": "shoot", "mode": "follow", "session": "s1"})
    record = stored(record)
    current = world.sessions.get_by_id("s1")
    world.sessions.update(current.model_copy(update={"data": {"txt2img": {**SHOOT_CHANGED}}}), current.version, None)

    message = refused(world, record, {"op": "set_settings", "step": "shoot", "fields": {"seed": 1}})

    assert "changed since the elixir last saw it" in message
    assert "get_step_settings again" in message


SHOOT_CHANGED = {"prompt": "elsewhere", "formData": {"quantity": 2}}


def test_an_old_revision_is_refused_with_how_to_recover(world):
    record = create(shoot_and_sharpen())
    ElixirRepository().update(record.id, "alice", record.revision, name="Renamed")

    message = refused(world, record, {"op": "set_settings", "step": "shoot", "fields": {"seed": 1}})

    assert f"revision {record.revision + 1}" in message
    assert "Call get_elixir again" in message
    assert not node(stored(record).definition, "shoot").get("overrides")


def test_a_step_is_added_after_another_and_takes_its_files(world):
    record = create(shoot_and_sharpen())

    edit(world, record, {"op": "add_step", "after": "sharpen", "preset_id": KREA, "mode": "enhance", "title": "Polish"})

    definition = stored(record).definition
    added = next(item for item in definition["nodes"] if item.get("title") == "Polish")
    edge = next(item for item in definition["edges"] if item["target"] == added["id"])
    assert (edge["source"], edge["source_handle"], edge["target_handle"]) == ("sharpen", "images", "source_image")
    assert added["session_mode"] == "new"


def test_a_step_that_cannot_take_the_files_is_refused_with_the_fix(world):
    record = create(shoot_and_sharpen())

    message = refused(world, record, {"op": "add_step", "after": "sharpen", "preset_id": KREA, "mode": "txt2img"})

    assert "takes no images" in message
    assert "include_presets" in message
    assert "No change was made" in message
    assert len(stored(record).definition["nodes"]) == 2


def test_an_unknown_preset_mode_names_the_usable_ones(world):
    record = create(shoot_and_sharpen())

    message = refused(world, record, {"op": "add_step", "after": "shoot", "preset_id": KREA, "mode": "inpaint"})

    assert "cannot be used in an elixir" in message
    assert "enhance" in message and "txt2img" in message


def test_a_step_is_inserted_between_two_steps(world):
    record = create(shoot_and_sharpen())

    edit(world, record, {"op": "insert_between", "before": "sharpen", "preset_id": KREA, "mode": "enhance", "title": "Middle"})

    definition = stored(record).definition
    middle = next(item for item in definition["nodes"] if item.get("title") == "Middle")
    pairs = {(edge["source"], edge["target"]) for edge in definition["edges"]}
    assert pairs == {("shoot", middle["id"]), (middle["id"], "sharpen")}


def test_a_step_is_added_before_the_start(world):
    record = create(shoot_and_sharpen())

    edit(world, record, {"op": "remove", "step": "shoot"}, {"op": "add_start", "before": "sharpen", "preset_id": KREA, "mode": "txt2img", "title": "New shoot"})

    definition = stored(record).definition
    first = next(item for item in definition["nodes"] if item.get("title") == "New shoot")
    assert {(edge["source"], edge["target"]) for edge in definition["edges"]} == {(first["id"], "sharpen")}


def test_removing_a_middle_step_rewires_its_files(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_step", "after": "sharpen", "preset_id": KREA, "mode": "enhance", "title": "Polish"})
    record = stored(record)

    edit(world, record, {"op": "remove", "step": "sharpen"})

    definition = stored(record).definition
    polish = next(item for item in definition["nodes"] if item.get("title") == "Polish")
    assert {(edge["source"], edge["target"]) for edge in definition["edges"]} == {("shoot", polish["id"])}


def test_a_writer_gets_an_instruction_and_a_target(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_writer", "for_step": "sharpen", "instruction": "Describe it"})
    record = stored(record)
    writer = next(item for item in record.definition["nodes"] if item["kind"] == "llm")

    edit(world, record, {"op": "set_writer", "step": writer["id"], "instruction": "Describe the {red|blue} bottle", "writes_for": "shoot"})

    saved = node(stored(record).definition, writer["id"])
    assert saved["instruction"] == "Describe the {red|blue} bottle"
    assert saved["target_node_id"] == "shoot"
    text = next(edge for edge in stored(record).definition["edges"] if edge["source"] == writer["id"] and edge["target"] == "sharpen")
    assert text["target_handle"] == "prompt"
    summary = world.assistant.summary("alice", record.id)
    assert next(step for step in summary["steps"] if step["id"] == writer["id"])["instruction"] == "Describe the {red|blue} bottle"


def writer_with_chip(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_writer", "for_step": "sharpen"})
    record = stored(record)
    definition = record.definition
    writer = next(item for item in definition["nodes"] if item["kind"] == "llm")
    writer["instruction"] = "A #colors bottle"
    writer["instruction_chips"] = {"c1": {"id": "c1", "categoryPath": "colors", "valueId": "v1", "label": "Colors", "value": "red", "shuffle": True}}
    ElixirRepository().update(record.id, "alice", record.revision, definition=definition)
    return stored(record), writer["id"]


def test_the_assistant_cannot_send_phrasebook_chips(world):
    record, writer = writer_with_chip(world)

    message = refused(world, record, {"op": "set_writer", "step": writer, "instruction": "x", "instruction_chips": {"$action": {}}})

    assert "Do not send 'instruction_chips'" in message
    assert "${name}" in message


def test_a_new_instruction_keeps_the_chips_it_still_uses_and_drops_the_rest(world):
    record, writer = writer_with_chip(world)

    edit(world, record, {"op": "set_writer", "step": writer, "instruction": "A #colors bottle on ${place}"})
    assert "c1" in node(stored(record).definition, writer)["instruction_chips"]

    edit(world, stored(record), {"op": "set_writer", "step": writer, "instruction": "A plain bottle"})
    assert node(stored(record).definition, writer)["instruction_chips"] == {}


def test_a_writer_keeps_its_own_variables(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_writer", "for_step": "sharpen"})
    record = stored(record)
    writer = next(item for item in record.definition["nodes"] if item["kind"] == "llm")
    mood = {"type": "choice", "options": ["noir", "sunlit"], "mode": "shuffle", "pinnedIndex": None}

    edit(world, record, {"op": "set_writer", "step": writer["id"], "variables": {"mood": mood}})

    assert node(stored(record).definition, writer["id"])["variables"]["mood"]["options"] == ["noir", "sunlit"]
    assert world.assistant.step_settings("alice", record.id, writer["id"])["variables"]["mood"]["type"] == "choice"
    bad = refused(world, stored(record), {"op": "set_writer", "step": writer["id"], "variables": {"mood": {"type": "choice", "options": []}}})
    assert "variables are not valid" in bad


def test_writer_variables_merge_by_name_and_take_the_tool_shape(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_writer", "for_step": "sharpen"})
    record = stored(record)
    writer = next(item for item in record.definition["nodes"] if item["kind"] == "llm")["id"]

    edit(world, record, {"op": "set_writer", "step": writer, "variables": {"place": {"type": "text", "value": "forest"}}})
    edit(world, stored(record), {"op": "set_writer", "step": writer, "variables": {"action": {"type": "choice", "options": ["running", "jumping"], "mode": "pin", "pinned_index": 1}}})

    variables = node(stored(record).definition, writer)["variables"]
    assert variables["place"]["value"] == "forest"
    assert variables["action"]["pinnedIndex"] == 1
    assert "pinned_index" not in variables["action"]

    edit(world, stored(record), {"op": "set_writer", "step": writer, "variables": {"place": None}})
    assert sorted(node(stored(record).definition, writer)["variables"]) == ["action"]


def test_a_second_writer_for_the_same_step_is_refused(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_writer", "for_step": "sharpen"})

    message = refused(world, stored(record), {"op": "add_writer", "for_step": "sharpen"})

    assert "already gets its prompt from" in message
    assert "set_writer" in message


def test_connect_refuses_a_loop(world):
    record = create(shoot_and_sharpen())
    edit(world, record, {"op": "add_step", "after": "sharpen", "preset_id": KREA, "mode": "enhance", "title": "Polish"})
    record = stored(record)
    polish = next(item for item in record.definition["nodes"] if item.get("title") == "Polish")

    message = refused(world, record, {"op": "connect", "from": polish["id"], "to": "sharpen"})

    assert "loop" in message


def test_invalid_operations_name_the_fix(world):
    record = create(shoot_and_sharpen())

    assert "Use one of: add_step" in refused(world, record, {"op": "explode"})
    assert "Steps in this elixir: shoot (Shoot), sharpen (Sharpen)" in refused(world, record, {"op": "remove", "step": "nope"})
    unknown_field = refused(world, record, {"op": "set_settings", "step": "shoot", "fields": {"cfg": 3}})
    assert "has no field 'cfg'" in unknown_field and "quantity" in unknown_field
    media = refused(world, record, {"op": "set_settings", "step": "sharpen", "fields": {"source_image": "x.png"}})
    assert "Use connect" in media
    assert "get_elixir" in refused(world, record, {"op": "set_settings", "step": "shoot", "fields": {"seed": 1}}, revision="4")


def test_session_modes_switch_between_copy_and_new(world):
    record = create(shoot_and_sharpen())

    edit(world, record, {"op": "set_session", "step": "shoot", "mode": "new"})

    saved = node(stored(record).definition, "shoot")
    assert saved["session_mode"] == "new"
    assert saved["overrides"]["prompt"] == "a bottle"
    assert "Following needs a saved session" in refused(world, stored(record), {"op": "set_session", "step": "shoot", "mode": "follow"})
    assert "No saved session 'Missing'" in refused(world, stored(record), {"op": "set_session", "step": "shoot", "mode": "follow", "session": "Missing"})


def test_step_settings_read_a_following_step_from_its_session(world):
    save_session(world)
    record = create(shoot_and_sharpen(session_mode="new", session_source=None, session_snapshot=None))
    edit(world, record, {"op": "set_session", "step": "shoot", "mode": "follow", "session": "s1"})

    settings = world.assistant.step_settings("alice", record.id, "shoot")

    assert settings["session_mode"] == "follow"
    assert settings["prompt"] == "a bottle"
    assert {row["name"]: row["value"] for row in settings["fields"]} == {"quantity": 1, "seed": 7}
    assert "followed session" in settings["edits_go_to"]


def test_outputs_are_picked_at_a_waiting_step_without_continuing(world):
    record = create(photos_into_sharpen(), run_mode="review")
    run = start_review_brew(world, record)
    assert run["status"] == "awaiting_selection"

    chosen = world.assistant.choose("alice", record.id, "photos", [2])

    assert chosen["picked"] == ["library:p2"]
    saved = world.coordinator.store.get_run(run["id"], "alice")
    assert saved["checkpoints"]["e1"]["draft"] == ["library:p2"]
    assert saved["checkpoints"]["e1"]["confirmed"] is None
    assert world.port.submits == []
    assert world.notifier.sent[-1][1]["run_id"] == run["id"]
    assert world.assistant.summary("alice", record.id)["waiting_for_choice"] == [{"step": "photos", "to": ["sharpen"], "outputs": 2, "picked": 1}]


def test_picking_at_a_step_that_is_not_waiting_is_refused(world):
    record = create(photos_into_sharpen(), run_mode="review")

    with pytest.raises(AssistantRefused) as nothing:
        world.assistant.choose("alice", record.id, "photos", [1])
    start_review_brew(world, record)
    with pytest.raises(AssistantRefused) as wrong:
        world.assistant.choose("alice", record.id, "sharpen", [1])
    with pytest.raises(AssistantRefused) as unknown:
        world.assistant.choose("alice", record.id, "photos", ["library:zz"])

    assert "No step is waiting for a choice" in str(nothing.value) and "Brew" in str(nothing.value)
    assert "Steps waiting: photos" in str(wrong.value)
    assert "numbers 1-2" in str(unknown.value)


def test_another_users_elixir_is_not_found(world):
    record = create(shoot_and_sharpen())

    with pytest.raises(AssistantRefused):
        world.assistant.summary("bob", record.id)
