import pytest

from src.features.organize.errors import OrganizeError
from tests.features.organize.conftest import collection_action, rule_body


def problems_of(excinfo):
    return [(p["path"], p["code"]) for p in excinfo.value.extra["problems"]]


def test_create_returns_the_described_rule(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")

    rule = manager.create_rule(user, rule_body(
        conditions=[{"fact": "aspect", "operator": "is", "value": "landscape"}],
        actions=[collection_action(landscapes)],
    ))

    assert rule["status"] == "active"
    assert rule["trigger"] == "generation_completed"
    assert rule["actions"][0]["config"]["collection_name"] == "Landscapes"
    assert rule["targets"]["collections"] == [{"id": landscapes, "name": "Landscapes", "exists": True}]
    assert rule["created_at"].endswith("+00:00")
    assert rule["filed_count"] == 0
    assert "user_id" not in rule


def test_naming_a_new_collection_creates_it_at_save_time_under_a_parent(seed, manager):
    user = seed.user("u1")
    parent = seed.collection("u1", "Photos")

    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="Beach", parent_id=parent)]))

    created = seed.rows("SELECT id, parent_id FROM collections WHERE name = 'Beach'")
    assert created[0]["parent_id"] == parent
    assert rule["actions"][0]["config"]["collection_id"] == created[0]["id"]


def test_naming_an_existing_collection_reuses_it(seed, manager):
    user = seed.user("u1")
    existing = seed.collection("u1", "Beach")

    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="beach")]))

    assert rule["actions"][0]["config"]["collection_id"] == existing
    assert len(seed.rows("SELECT id FROM collections")) == 1


def test_a_rule_cannot_target_another_users_collection(seed, manager):
    user = seed.user("u1")
    seed.user("u2")
    theirs = seed.collection("u2", "Theirs")

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(actions=[collection_action(theirs, create=False)]))

    assert problems_of(excinfo) == [("actions.0.config.collection_id", "collection_not_found")]


def test_a_rule_cannot_target_a_collection_of_the_wrong_scope(seed, manager):
    user = seed.user("u1")
    library = seed.collection("u1", "Library one", scope="library")

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(actions=[collection_action(library, create=False)]))

    assert excinfo.value.status_code == 422


def test_a_user_cannot_reference_a_model_they_do_not_have(seed, manager):
    user = seed.user("u1")
    mine = seed.model("mine.safetensors")
    other = seed.model("other.safetensors")
    seed.assign("u1", mine)
    landscapes = seed.collection("u1")

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(
            conditions=[{"fact": "model", "operator": "is_any_of", "value": [mine, other]}],
            actions=[collection_action(landscapes)],
        ))

    assert problems_of(excinfo) == [("conditions.0.value", "model_not_allowed")]


def test_restricted_users_cannot_reference_nsfw_models(seed, manager, visibility):
    user = seed.user("u1")
    model = seed.model()
    seed.assign("u1", model)
    seed.flag_nsfw(model)
    visibility.restricted.add("u1")

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(
            conditions=[{"fact": "model", "operator": "is", "value": model}],
            actions=[collection_action(seed.collection("u1"))],
        ))

    assert problems_of(excinfo) == [("conditions.0.value", "model_not_allowed")]


@pytest.mark.parametrize("body,expected", [
    (rule_body(name=""), ("name", "name_required")),
    (rule_body(actions=[]), ("actions", "no_actions")),
    (rule_body(conditions=[{"fact": "nope", "operator": "is", "value": "x"}], actions=[collection_action(name="A")]),
     ("conditions.0.fact", "unknown_fact")),
    (rule_body(conditions=[{"fact": "model_type", "operator": "is", "value": "lora"}], actions=[collection_action(name="A")]),
     ("conditions.0.fact", "fact_subject")),
    (rule_body(conditions=[{"fact": "aspect", "operator": "is_not", "value": "square"}], actions=[collection_action(name="A")]),
     ("conditions.0.operator", "unknown_operator")),
    (rule_body(conditions=[{"fact": "aspect", "operator": "is", "value": "round"}], actions=[collection_action(name="A")]),
     ("conditions.0.value", "bad_value")),
    (rule_body(conditions=[{"fact": "resolution", "operator": "is", "value": {"width": 0, "height": 5}}],
               actions=[collection_action(name="A")]), ("conditions.0.value", "bad_value")),
    (rule_body(subject="model", actions=[{"action": "add_tags", "config": {"tags": ["x"]}}]),
     ("actions.0.action", "action_subject")),
    (rule_body(actions=[{"action": "add_tags", "config": {"tags": []}}]), ("actions.0.config.tags", "bad_config")),
    (rule_body(actions=[{"action": "add_to_collection", "config": {}}]), ("actions.0.config.collection_id", "bad_config")),
])
def test_validation_problems(seed, manager, body, expected):
    user = seed.user("u1")

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, body)

    assert excinfo.value.code == "invalid_rule"
    assert expected in problems_of(excinfo)
    assert seed.rows("SELECT * FROM organize_rules") == []
    assert seed.rows("SELECT * FROM collections") == []


def test_rules_of_another_user_answer_not_found(seed, manager):
    one = seed.user("u1")
    two = seed.user("u2")
    rule = manager.create_rule(one, rule_body(actions=[collection_action(name="Mine")]))

    for call in (
        lambda: manager.get_rule(two, rule["id"]),
        lambda: manager.update_rule(two, rule["id"], rule_body(actions=[collection_action(name="X")])),
        lambda: manager.patch_rule(two, rule["id"], {"enabled": False}),
        lambda: manager.delete_rule(two, rule["id"]),
        lambda: manager.start_backfill(two, rule["id"]),
        lambda: manager.duplicate(two, rule["id"]),
    ):
        with pytest.raises(OrganizeError) as excinfo:
            call()
        assert excinfo.value.code == "rule_not_found"
    assert manager.list_rules(two, None) == []
    assert manager.get_rule(one, rule["id"])["enabled"] is True


def test_the_writer_never_files_another_users_item(seed, manager):
    seed.user("u1")
    seed.user("u2")
    mine = seed.collection("u1")
    theirs_generation = seed.generation("u2")
    theirs_collection = seed.collection("u2")
    my_generation = seed.generation("u1")
    writer = manager.c.writer

    assert writer.add_member("history", mine, theirs_generation, "u1") is False
    assert writer.add_member("history", theirs_collection, my_generation, "u1") is False
    tag_id, _ = writer.ensure_tag("x", "GENERATION", "u1")
    assert writer.attach_tag("generation", theirs_generation, tag_id, "u1") is False
    assert writer.attach_tag("generation", my_generation, tag_id, "u2") is False
    assert seed.rows("SELECT * FROM collection_generations") == []
    assert seed.rows("SELECT * FROM generation_tags") == []


def test_live_events_only_touch_the_owners_rules(seed, manager):
    one = seed.user("u1")
    seed.user("u2")
    mine = seed.collection("u1")
    manager.create_rule(one, rule_body(actions=[collection_action(mine)]))
    theirs = seed.generation("u2")

    manager.handle_event("generation_completed", {"generation_id": theirs, "status": "completed", "user_id": "u1"})

    assert seed.members(mine) == set()


def test_rule_cap_blocks_new_rules(seed, manager):
    user = seed.user("u1")
    manager.admin_user("u1", {"rule_cap": 1})
    manager.create_rule(user, rule_body(actions=[collection_action(name="A")]))

    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(actions=[collection_action(name="B")]))

    assert excinfo.value.code == "rule_cap_reached"
    assert excinfo.value.status_code == 409


def test_update_keeps_the_subject_and_clears_a_pause(seed, manager):
    user = seed.user("u1")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="A")]))
    manager.c.rules.pause("u1", rule["id"], "rate_limited")

    with pytest.raises(OrganizeError) as excinfo:
        manager.update_rule(user, rule["id"], {"subject": "upload"})
    assert problems_of(excinfo) == [("subject", "subject_locked")]

    updated = manager.update_rule(user, rule["id"], {"name": "Renamed"})
    assert updated["name"] == "Renamed"
    assert updated["paused_reason"] is None
    assert updated["actions"] == rule["actions"]


def test_changing_the_actions_lets_apply_existing_file_items_again(seed, manager):
    user = seed.user("u1")
    first = seed.collection("u1", "First")
    second = seed.collection("u1", "Second")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(first)]))
    generation = seed.generation("u1")
    manager.process_item("generation", "u1", generation)

    manager.update_rule(user, rule["id"], {"conditions": [{"fact": "mode", "operator": "is", "value": "txt2img"}]})
    assert manager.preview(user, {**rule_body(actions=[collection_action(second)]), "rule_id": rule["id"]})["already_handled"] == 1

    manager.update_rule(user, rule["id"], {"actions": [collection_action(second)]})
    manager.start_backfill(user, rule["id"])

    assert seed.members(second) == {generation}


def test_reorder_needs_every_rule_of_the_subject(seed, manager):
    user = seed.user("u1")
    a = manager.create_rule(user, rule_body(name="a", actions=[collection_action(name="A")]))
    b = manager.create_rule(user, rule_body(name="b", actions=[collection_action(name="B")]))

    reordered = manager.reorder(user, "generation", [b["id"], a["id"]])
    assert [r["name"] for r in reordered] == ["b", "a"]

    with pytest.raises(OrganizeError) as excinfo:
        manager.reorder(user, "generation", [a["id"]])
    assert excinfo.value.code == "invalid_order"


def test_duplicate_is_off_and_named_copy(seed, manager):
    user = seed.user("u1")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="A")]))

    copy = manager.duplicate(user, rule["id"])

    assert copy["name"] == "Krea landscapes (copy)"
    assert copy["enabled"] is False
    assert copy["status"] == "off"


def test_summary_counts_active_and_attention(seed, manager):
    user = seed.user("u1")
    gone = seed.collection("u1", "Gone")
    manager.create_rule(user, rule_body(name="ok", actions=[collection_action(name="A")]))
    manager.create_rule(user, rule_body(name="broken", actions=[collection_action(gone, create=False)]))
    manager.create_rule(user, rule_body(name="off", actions=[collection_action(name="B")], enabled=False))
    manager.create_rule(user, rule_body(name="up", subject="upload", actions=[collection_action(name="C")]))
    seed._exec("DELETE FROM collections WHERE id = ?", (gone,))

    summary = manager.summary(user)

    assert summary["subjects"]["generation"] == {"total": 3, "active": 1, "needs_attention": 1}
    assert summary["subjects"]["upload"] == {"total": 1, "active": 1, "needs_attention": 0}
    assert summary["rule_count"] == 4
    assert summary["rule_cap"] == 50
    assert summary["hourly_limit"] == 200


def test_collection_echo_lists_rules_filling_it(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    manager.create_rule(user, rule_body(name="other", actions=[collection_action(name="Else")]))

    assert manager.collection_rules(user, "history", landscapes) == [
        {"id": rule["id"], "name": "Krea landscapes", "status": "active"}
    ]


def test_preview_flags_duplicate_rules(seed, manager):
    user = seed.user("u1")
    conditions = [{"fact": "aspect", "operator": "is", "value": "landscape"}]
    existing = manager.create_rule(user, rule_body(conditions=conditions, actions=[collection_action(name="A")]))

    preview = manager.preview(user, {"subject": "generation", "match": "all", "conditions": conditions})

    assert preview["duplicates"] == [{"rule_id": existing["id"], "name": "Krea landscapes"}]
