from src.features.organize.manager import NOTICE_RULE_PAUSED
from src.platform.plugins.hooks import HookChain, HookContext
from tests.features.organize.conftest import collection_action, rule_body


def complete(chain, generation_id, status="completed"):
    chain.execute("generation.after_complete", context=HookContext(
        hook_name="generation.after_complete", plugin_id="system",
        data={"generation_id": generation_id, "status": status, "user_id": "ignored"},
    ))


def test_a_completed_generation_matching_a_rule_lands_in_its_collection(seed, manager, worker):
    user = seed.user("u1")
    model = seed.model()
    seed.assign("u1", model)
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(
        conditions=[{"fact": "model", "operator": "is", "value": model},
                    {"fact": "resolution", "operator": "is", "value": {"width": 1344, "height": 768}}],
        actions=[collection_action(landscapes), {"action": "add_tags", "config": {"tags": ["landscape"]}}],
    ))
    chain = HookChain()
    worker.subscribe(chain)
    match = seed.generation("u1", models=[model])
    other = seed.generation("u1", models=[model], files=((1024, 1024, "IMAGE", None),))

    complete(chain, match)
    complete(chain, other)
    worker.drain()

    assert seed.members(landscapes) == {match}
    assert seed.generation_tags(match) == ["landscape"]
    assert seed.generation_tags(other) == []


def test_failed_generations_never_match(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    failed = seed.generation("u1", status="failed")

    manager.handle_event("generation_completed", {"generation_id": failed, "status": "failed"})
    manager.handle_event("generation_completed", {"generation_id": failed, "status": "completed"})

    assert seed.members(landscapes) == set()


def test_a_rule_files_each_item_only_once_even_after_the_user_removes_it(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)
    seed._exec("DELETE FROM collection_generations WHERE generation_id = ?", (generation,))
    manager.process_item("generation", "u1", generation, "tags_changed")
    manager.process_item("generation", "u1", generation)

    assert seed.members(landscapes) == set()


def test_disabled_rules_do_nothing(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(actions=[collection_action(landscapes)], enabled=False))

    manager.process_item("generation", "u1", seed.generation("u1"))

    assert seed.members(landscapes) == set()


def test_stop_after_keeps_later_rules_from_running(seed, manager):
    user = seed.user("u1")
    first = seed.collection("u1", "First")
    second = seed.collection("u1", "Second")
    manager.create_rule(user, rule_body(name="a", actions=[collection_action(first)], stop_after=True))
    manager.create_rule(user, rule_body(name="b", actions=[collection_action(second)]))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)

    assert seed.members(first) == {generation}
    assert seed.members(second) == set()


def test_without_stop_after_every_matching_rule_files_the_item(seed, manager):
    user = seed.user("u1")
    first = seed.collection("u1", "First")
    second = seed.collection("u1", "Second")
    manager.create_rule(user, rule_body(name="a", actions=[collection_action(first)]))
    manager.create_rule(user, rule_body(name="b", actions=[collection_action(second)]))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)

    assert seed.members(first) == {generation}
    assert seed.members(second) == {generation}


def test_late_tags_trigger_tag_rules(seed, manager, worker):
    user = seed.user("u1")
    pets = seed.collection("u1", "Pets")
    manager.create_rule(user, rule_body(
        conditions=[{"fact": "tags", "operator": "has", "value": ["cat"]}], actions=[collection_action(pets)],
    ))
    chain = HookChain()
    worker.subscribe(chain)
    generation = seed.generation("u1")
    complete(chain, generation)
    worker.drain()
    assert seed.members(pets) == set()

    seed.tag_generation("u1", generation, "Cat")
    chain.execute("generation.after_update_tags", initial_data={"generation_id": generation, "user_id": "u1"})
    worker.drain()

    assert seed.members(pets) == {generation}


def test_safety_valve_pauses_a_rule_past_the_hourly_limit(seed, manager, notices):
    user = seed.user("u1")
    everything = seed.collection("u1", "Everything")
    rule = manager.create_rule(user, rule_body(name="All of it", actions=[collection_action(everything)]))
    manager.admin_controls({"hourly_limit": 3})
    generations = [seed.generation("u1") for _ in range(5)]

    for generation in generations:
        manager.process_item("generation", "u1", generation)

    assert seed.members(everything) == set(generations[:3])
    stored = manager.get_rule(user, rule["id"])
    assert stored["status"] == "paused"
    assert stored["paused_reason"] == "rate_limited"
    paused = notices.of_type(NOTICE_RULE_PAUSED)
    assert len(paused) == 1
    assert "All of it" in paused[0]["message"] and "3 items" in paused[0]["message"]
    assert paused[0]["user_id"] == "u1"


def test_turning_a_paused_rule_back_on_resumes_it(seed, manager):
    user = seed.user("u1")
    everything = seed.collection("u1", "Everything")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(everything)]))
    manager.c.rules.pause("u1", rule["id"], "rate_limited")
    generation = seed.generation("u1")
    manager.process_item("generation", "u1", generation)
    assert seed.members(everything) == set()

    manager.patch_rule(user, rule["id"], {"enabled": True})
    manager.process_item("generation", "u1", generation)

    assert seed.members(everything) == {generation}


def test_backfill_filing_does_not_count_toward_the_safety_valve(seed, manager):
    user = seed.user("u1")
    everything = seed.collection("u1", "Everything")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(everything)]))
    manager.admin_controls({"hourly_limit": 2})
    old = [seed.generation("u1") for _ in range(4)]
    manager.start_backfill(user, rule["id"])
    assert seed.members(everything) == set(old)

    fresh = seed.generation("u1")
    manager.process_item("generation", "u1", fresh)

    assert fresh in seed.members(everything)
    assert manager.get_rule(user, rule["id"])["status"] == "active"


def test_a_deleted_collection_is_created_again_by_name(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1", "Landscapes")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    seed._exec("DELETE FROM collections WHERE id = ?", (landscapes,))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)

    recreated = seed.rows("SELECT id, name FROM collections WHERE user_id = 'u1' AND scope = 'history'")
    assert [r["name"] for r in recreated] == ["Landscapes"]
    assert seed.members(recreated[0]["id"]) == {generation}
    stored = manager.get_rule(user, rule["id"])
    assert stored["actions"][0]["config"]["collection_id"] == recreated[0]["id"]
    assert stored["status"] == "active"


def test_a_deleted_collection_without_recreate_pauses_the_rule(seed, manager, notices):
    user = seed.user("u1")
    landscapes = seed.collection("u1", "Landscapes")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(landscapes, create=False)]))
    seed._exec("DELETE FROM collections WHERE id = ?", (landscapes,))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)

    stored = manager.get_rule(user, rule["id"])
    assert stored["paused_reason"] == "collection_missing"
    assert any(i["code"] == "collection_missing" and i["blocking"] for i in stored["issues"])
    assert notices.of_type(NOTICE_RULE_PAUSED)[0]["metadata"]["reason"] == "collection_missing"
    assert seed.rows("SELECT * FROM organize_handled") == []


def test_a_renamed_collection_keeps_receiving_items_and_the_rule_shows_the_new_name(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1", "Landscapes")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    seed._exec("UPDATE collections SET name = 'Wide shots' WHERE id = ?", (landscapes,))
    generation = seed.generation("u1")

    manager.process_item("generation", "u1", generation)

    assert seed.members(landscapes) == {generation}
    assert manager.get_rule(user, rule["id"])["targets"]["collections"][0]["name"] == "Wide shots"


def test_a_deleted_model_still_matches_old_history_and_warns(seed, manager):
    user = seed.user("u1")
    model = seed.model()
    seed.assign("u1", model)
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(
        conditions=[{"fact": "model", "operator": "is", "value": model}], actions=[collection_action(landscapes)],
    ))
    generation = seed.generation("u1", models=[model])
    seed._exec("DELETE FROM models WHERE id = ?", (model,))

    manager.process_item("generation", "u1", generation)

    assert seed.members(landscapes) == {generation}
    issues = manager.get_rule(user, rule["id"])["issues"]
    assert [(i["code"], i["blocking"]) for i in issues] == [("model_missing", False)]


def test_multi_file_generations_match_when_any_output_matches(seed, manager):
    user = seed.user("u1")
    videos = seed.collection("u1", "Videos")
    manager.create_rule(user, rule_body(
        conditions=[{"fact": "media_kind", "operator": "is", "value": "video"}], actions=[collection_action(videos)],
    ))
    generation = seed.generation("u1", files=((1024, 1024, "IMAGE", None), (1280, 720, "VIDEO", 5.0)))

    manager.process_item("generation", "u1", generation)

    assert seed.members(videos) == {generation}


def test_kill_switch_stops_live_filing_for_everyone(seed, manager):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    manager.admin_controls({"paused_all": True})

    manager.process_item("generation", "u1", seed.generation("u1"))

    assert seed.members(landscapes) == set()
    assert manager.summary(user)["paused_by_admin"] is True


def test_per_user_kill_switch_only_stops_that_user(seed, manager):
    one = seed.user("u1")
    two = seed.user("u2")
    first = seed.collection("u1")
    second = seed.collection("u2")
    manager.create_rule(one, rule_body(actions=[collection_action(first)]))
    manager.create_rule(two, rule_body(actions=[collection_action(second)]))
    manager.admin_user("u1", {"paused": True})
    g1 = seed.generation("u1")
    g2 = seed.generation("u2")

    manager.process_item("generation", "u1", g1)
    manager.process_item("generation", "u2", g2)

    assert seed.members(first) == set()
    assert seed.members(second) == {g2}


def test_restricted_users_never_file_hidden_generations(seed, manager, visibility):
    user = seed.user("u1")
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    visible = seed.generation("u1")
    hidden = seed.generation("u1")
    visibility.restricted.add("u1")
    visibility.hidden_generations.add(hidden)

    manager.process_item("generation", "u1", visible)
    manager.process_item("generation", "u1", hidden)

    assert seed.members(landscapes) == {visible}


def test_a_rule_using_a_model_that_becomes_restricted_is_paused(seed, manager, visibility, notices):
    user = seed.user("u1")
    model = seed.model()
    seed.assign("u1", model)
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(
        conditions=[{"fact": "model", "operator": "is", "value": model}], actions=[collection_action(landscapes)],
    ))
    seed.flag_nsfw(model)
    visibility.restricted.add("u1")

    manager.process_item("generation", "u1", seed.generation("u1", models=[model]))

    assert seed.members(landscapes) == set()
    assert manager.get_rule(user, rule["id"])["paused_reason"] == "model_restricted"
    assert notices.of_type(NOTICE_RULE_PAUSED)[0]["metadata"]["reason"] == "model_restricted"


def test_uploads_are_filed_into_library_collections(seed, manager, worker):
    user = seed.user("u1")
    images = seed.collection("u1", "Images", scope="library")
    manager.create_rule(user, rule_body(
        subject="upload", conditions=[{"fact": "filename", "operator": "contains", "value": "HOLIDAY"}],
        actions=[collection_action(images), {"action": "add_tags", "config": {"tags": ["trip"]}}],
    ))
    chain = HookChain()
    worker.subscribe(chain)
    upload = seed.upload("u1", filename="Holiday-2026.png")
    derived = seed.upload("u1", filename="holiday-mask.png", purpose="derived_artifact")

    chain.execute("media.after_record", initial_data={"upload_id": upload, "user_id": "u1", "purpose": "user_upload"})
    chain.execute("media.after_record", initial_data={"upload_id": derived, "user_id": "u1", "purpose": "derived_artifact"})
    worker.drain()

    members = seed.rows("SELECT upload_id FROM collection_uploads WHERE collection_id = ?", (images,))
    assert [m["upload_id"] for m in members] == [upload]
    tags = seed.rows("SELECT t.name, t.type FROM upload_tags ut JOIN tags t ON t.id = ut.tag_id WHERE ut.upload_id = ?", (upload,))
    assert tags == [{"name": "trip", "type": "UPLOAD"}]


def test_models_added_after_the_rule_are_filed_into_model_collections(seed, manager, worker):
    user = seed.user("admin", admin=True)
    old = seed.model("old-lora.safetensors", "lora", created_at="2020-01-01 00:00:00")
    checkpoints = seed.model_collection("admin", "LoRAs")
    manager.create_rule(user, rule_body(
        subject="model", conditions=[{"fact": "model_type", "operator": "is", "value": "lora"}],
        actions=[collection_action(checkpoints)],
    ))
    fresh = seed.model("new-lora.safetensors", "lora", created_at="2999-01-01 00:00:00")
    other = seed.model("new-ckpt.safetensors", "checkpoint", created_at="2999-01-01 00:00:00")
    chain = HookChain()
    worker.subscribe(chain)

    chain.execute("model_index.after_index", initial_data={"result": {}})
    worker.drain()

    members = {r["model_id"] for r in seed.rows(
        "SELECT model_id FROM model_collection_members WHERE collection_id = ?", (checkpoints,)
    )}
    assert members == {fresh}
    assert old not in members and other not in members


def test_models_a_user_cannot_see_are_never_filed(seed, manager):
    user = seed.user("u1")
    visible = seed.model("mine.safetensors", "lora")
    hidden = seed.model("theirs.safetensors", "lora")
    seed.assign("u1", visible)
    loras = seed.model_collection("u1", "LoRAs")
    manager.create_rule(user, rule_body(subject="model", actions=[collection_action(loras)]))

    manager.handle_event("model_added", {"model_id": visible})
    manager.handle_event("model_added", {"model_id": hidden})

    members = {r["model_id"] for r in seed.rows("SELECT model_id FROM model_collection_members")}
    assert members == {visible}
