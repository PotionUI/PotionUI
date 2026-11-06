from types import SimpleNamespace

import pytest

from src.features.models.exceptions import ModelTypeConflictException
from src.features.organize.errors import OrganizeError
from src.features.organize.manager import MAX_ATTEMPTS, instance_viewer, viewer_of
from src.features.organize.rule_repository import INSTANCE_SCOPE
from src.features.organize.worker import OrganizeWorker
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins.hooks import HookChain
from src.platform.plugins.organize import OrganizeActionRetry, OrganizeActionSkipped, OrganizeChange
from tests.features.organize.conftest import collection_action, rule_body


def group_rule(world, group_id, conditions=None, **extra):
    return world.manager.create_rule(world.viewer, rule_body(
        subject="model", name=extra.pop("name", "Group"), conditions=conditions or [],
        actions=[{"action": "assign_model_group", "config": {"group_id": group_id}}], **extra,
    ))


def fetch_rule(world, conditions=None, **extra):
    return world.manager.create_rule(world.viewer, rule_body(
        subject="model", name=extra.pop("name", "Fetch"), conditions=conditions or [],
        actions=[{"action": "test.fetch", "config": {}}], **extra,
    ))


def make_group(name="krea-2"):
    return UserGroupRepository().create_group(name).id


def members(world, group_id):
    return {r["model_id"] for r in world.seed.rows("SELECT model_id FROM user_group_models WHERE group_id = ?", (group_id,))}


def outcome(world, rule_id, model_id):
    rows = world.seed.rows(
        "SELECT * FROM organize_outcomes WHERE rule_id = ? AND item_id = ?", (rule_id, model_id)
    )
    return rows[0] if rows else None


def handled(world, rule_id):
    return {r["item_id"] for r in world.seed.rows("SELECT item_id FROM organize_handled WHERE rule_id = ?", (rule_id,))}


def unverify(world, model_id):
    world.seed._exec("UPDATE models SET sha256 = NULL WHERE id = ?", (model_id,))


def test_instance_rules_have_no_owner_and_record_who_made_them(world):
    group = make_group()

    created = group_rule(world, group)

    row = world.seed.rows("SELECT user_id, created_by, updated_by FROM organize_rules WHERE id = ?", (created["id"],))[0]
    assert row == {"user_id": None, "created_by": "root", "updated_by": "root"}
    assert created["scope"] == "instance"
    assert (created["created_by_name"], created["updated_by_name"]) == ("root", "root")


def test_a_later_edit_records_the_admin_who_made_it(world, seed):
    other = seed.user("second", admin=True)
    group = make_group()
    created = group_rule(world, group)

    updated = world.manager.patch_rule(instance_viewer(other), created["id"], {"name": "Renamed"})

    assert updated["created_by"] == "root"
    assert (updated["updated_by"], updated["updated_by_name"]) == ("second", "second")


def test_instance_rules_are_invisible_to_user_routes_and_users_rules_to_instance_routes(world, seed):
    user = seed.user("someone")
    group = make_group()
    instance_rule = group_rule(world, group)
    personal = world.manager.create_rule(user, rule_body(actions=[collection_action(name="Mine")]))

    assert [r["id"] for r in world.manager.list_rules(user, None)] == [personal["id"]]
    assert [r["id"] for r in world.manager.list_rules(world.viewer, None)] == [instance_rule["id"]]
    with pytest.raises(OrganizeError) as hidden:
        world.manager.get_rule(user, instance_rule["id"])
    with pytest.raises(OrganizeError) as other_way:
        world.manager.get_rule(world.viewer, personal["id"])
    assert hidden.value.status_code == other_way.value.status_code == 404
    admin_as_user = world.manager.list_rules(world.admin_user, None)
    assert instance_rule["id"] not in [r["id"] for r in admin_as_user]


def test_catalogs_never_mix_user_and_instance_actions(world, seed):
    user = seed.user("someone")

    as_user = {a["key"] for a in world.manager.catalog(user, "model")["actions"]}
    as_admin_user = {a["key"] for a in world.manager.catalog(world.admin_user, "model")["actions"]}
    instance = world.manager.catalog(world.viewer)

    instance_keys = {a["key"] for a in instance["actions"]}
    assert as_user == as_admin_user == {"add_to_collection"}
    assert instance_keys == {"assign_model_group", "add_model_tags", "set_model_type", "test.fetch"}
    assert instance["scope"] == "instance"
    assert [s["key"] for s in instance["subjects"]] == ["model"]
    assert all("model" in f["subjects"] for f in instance["facts"])
    fetch = next(a for a in instance["actions"] if a["key"] == "test.fetch")
    assert (fetch["needs"], fetch["paced"], fetch["notes"], fetch["done_label"]) == (["digest"], True, ["Exact match only"], "Fetched data")
    set_type = next(a for a in instance["actions"] if a["key"] == "set_model_type")
    assert {"value": "lora", "label": "lora"} in set_type["config_schema"][0]["options"]


def test_a_user_cannot_save_an_instance_action_and_an_instance_rule_cannot_use_a_user_action(world, seed):
    user = seed.user("someone")
    group = make_group()

    with pytest.raises(OrganizeError) as by_user:
        world.manager.create_rule(user, rule_body(
            subject="model", actions=[{"action": "assign_model_group", "config": {"group_id": group}}]
        ))
    with pytest.raises(OrganizeError) as by_instance:
        world.manager.create_rule(world.viewer, rule_body(subject="model", actions=[collection_action(name="X")]))
    with pytest.raises(OrganizeError) as wrong_subject:
        world.manager.create_rule(world.viewer, rule_body(
            subject="generation", actions=[{"action": "add_tags", "config": {"tags": ["x"]}}]
        ))

    assert by_user.value.extra["problems"][0]["code"] == "action_scope"
    assert by_instance.value.extra["problems"][0]["code"] == "action_scope"
    assert wrong_subject.value.extra["problems"][0]["path"] == "subject"


def test_an_unknown_group_or_type_is_refused_on_save(world):
    with pytest.raises(OrganizeError) as group:
        world.manager.create_rule(world.viewer, rule_body(
            subject="model", actions=[{"action": "assign_model_group", "config": {"group_id": "nope"}}]
        ))
    with pytest.raises(OrganizeError) as kind:
        world.manager.create_rule(world.viewer, rule_body(
            subject="model", actions=[{"action": "set_model_type", "config": {"model_type": "banana"}}]
        ))

    assert group.value.extra["problems"][0]["code"] == "group_not_found"
    assert kind.value.extra["problems"][0]["path"] == "actions.0.config.model_type"


def test_instance_rules_have_their_own_cap(world):
    group = make_group()
    world.manager.c.rules.save_controls(INSTANCE_SCOPE, SimpleNamespace(paused=False, rule_cap=1, hourly_limit=None))
    assert world.manager.effective_cap(None) == 1
    group_rule(world, group)

    with pytest.raises(OrganizeError) as capped:
        group_rule(world, group, name="Second")

    assert capped.value.code == "rule_cap_reached"
    assert world.admin_ops.instance_row()["rules"] == 1


def test_default_instance_cap_is_a_hundred(world):
    assert world.manager.effective_cap(None) == 100
    assert world.manager.effective_cap("u1") == 50


def test_a_new_model_whose_name_matches_gets_the_group_and_only_once(world, seed):
    group = make_group()
    other = make_group("other")
    rule = group_rule(world, group, [{"fact": "name", "operator": "contains", "value": "krea2"}])
    hit = seed.model("krea2-dev.safetensors")
    miss = seed.model("sdxl.safetensors")

    world.manager.handle_event("model_added", {"model_id": hit})
    world.manager.handle_event("model_added", {"model_id": miss})
    world.manager.handle_event("model_added", {"model_id": hit})

    assert members(world, group) == {hit}
    assert members(world, other) == set()
    applications = seed.rows("SELECT user_id, target_type, target_id, target_name, item_id FROM organize_applications")
    assert applications == [{"user_id": None, "target_type": "user_group", "target_id": group, "target_name": "krea-2", "item_id": hit}]
    assert handled(world, rule["id"]) == {hit}


def test_the_group_action_returns_nothing_for_a_model_already_in_the_group(world, seed):
    group = make_group()
    model = seed.model("krea2.safetensors")
    seed._exec("INSERT INTO user_group_models (id, group_id, model_id) VALUES ('x', ?, ?)", (group, model))
    rule = group_rule(world, group)

    world.manager.handle_event("model_added", {"model_id": model})

    assert seed.rows("SELECT * FROM organize_applications") == []
    assert handled(world, rule["id"]) == {model}
    definition = world.registry.action("assign_model_group")
    item = SimpleNamespace(item_id=model, subject="model")
    assert definition.apply(item, {"group_id": group}, None) == []


def test_undoing_a_group_run_removes_exactly_what_the_run_added(world, seed):
    group = make_group()
    already = seed.model("krea2-a.safetensors")
    seed._exec("INSERT INTO user_group_models (id, group_id, model_id) VALUES ('x', ?, ?)", (group, already))
    fresh = seed.model("krea2-b.safetensors")
    rule = group_rule(world, group, [{"fact": "name", "operator": "contains", "value": "krea2"}])
    world.backfill.start(world.viewer, rule["id"])
    run = world.manager.activity(world.viewer, "model", None, None, 10)["runs"][0]
    assert run["can_undo"] is True

    result = world.manager.undo_run(world.viewer, run["id"])

    assert result["items"] == 1
    assert members(world, group) == {already}
    assert fresh not in members(world, group)
    undone = world.seed.rows("SELECT undone_by FROM organize_runs WHERE id = ?", (run["id"],))[0]
    assert undone["undone_by"] == "root"


def test_a_veto_becomes_a_skipped_outcome_and_the_model_is_not_retried(world, seed):
    group = make_group()
    model = seed.model("krea2.safetensors")
    world.plugins.vetoed.add(model)
    rule = group_rule(world, group)

    world.manager.handle_event("model_added", {"model_id": model})

    assert members(world, group) == set()
    row = outcome(world, rule["id"], model)
    assert (row["outcome"], row["code"]) == ("skipped", "vetoed")
    assert handled(world, rule["id"]) == {model}


def test_deleting_the_group_pauses_the_rule_and_the_card_says_why(world, seed):
    group = make_group()
    rule = group_rule(world, group)
    model = seed.model("krea2.safetensors")
    UserGroupRepository().delete_group(group)

    world.manager.handle_event("model_added", {"model_id": model})

    card = world.manager.get_rule(world.viewer, rule["id"])
    assert card["paused_reason"] == "group_missing"
    assert card["status"] == "paused"
    assert card["targets"]["groups"] == [{"id": group, "name": "", "exists": False}]
    assert card["issues"][0]["code"] == "group_missing"
    assert world.notices.of_type("organize.rule_paused")[0]["user_id"] == "root"


def test_name_matching_can_ignore_dashes_and_underscores(world, seed):
    group = make_group()
    dashed = seed.model("Krea-2_Dev.safetensors")
    plain = seed.model("krea2.safetensors")
    other = seed.model("kre-a.safetensors")
    ignoring = [{"fact": "name", "operator": "contains", "value": "krea2", "ignore_separators": True}]
    strict = [{"fact": "name", "operator": "contains", "value": "krea2"}]

    on = world.manager.preview(world.viewer, {"subject": "model", "conditions": ignoring})
    off = world.manager.preview(world.viewer, {"subject": "model", "conditions": strict})

    assert on["matched"] == 2 and {s["item_id"] for s in on["sample"]} == {dashed, plain}
    assert off["matched"] == 1 and off["sample"][0]["item_id"] == plain
    assert other not in {s["item_id"] for s in on["sample"]}


@pytest.mark.parametrize("operator,value,expected", [
    ("contains", "ea-2d", True),
    ("starts_with", "krea2", True),
    ("starts_with", "2dev", False),
    ("ends_with", "2-dev", True),
    ("is", "krea2dev", True),
    ("is", "krea2", False),
    ("not_contains", "krea2", False),
    ("not_contains", "flux", True),
])
def test_separator_insensitive_matching_agrees_between_sql_and_live_evaluation(world, seed, operator, value, expected):
    model = seed.model("Krea_2-Dev.safetensors")
    conditions = [{"fact": "name", "operator": operator, "value": value, "ignore_separators": True}]

    sql_ids, _ = world.manager.matching_ids(world.viewer, "model", "all", conditions, None)
    item = world.manager.load_items("model", None, [model], True, False)[0]
    from src.features.organize.evaluator import evaluate

    assert (model in sql_ids) is expected
    assert evaluate(world.registry, "all", conditions, item) is expected


def test_file_name_conditions_can_ignore_separators_too(world, seed):
    model = seed.model("My_Lora-v2.safetensors", "lora")
    conditions = [{"fact": "filename", "operator": "contains", "value": "mylorav2", "ignore_separators": True}]

    ids, _ = world.manager.matching_ids(world.viewer, "model", "all", conditions, None)

    assert ids == [model]


def test_the_separator_option_is_refused_where_it_does_not_apply(world):
    group = make_group()
    for condition in (
        {"fact": "model_type", "operator": "is", "value": "lora", "ignore_separators": True},
        {"fact": "name", "operator": "contains", "value": "x", "ignore_separators": "yes"},
    ):
        with pytest.raises(OrganizeError) as refused:
            world.manager.create_rule(world.viewer, rule_body(
                subject="model", conditions=[condition],
                actions=[{"action": "assign_model_group", "config": {"group_id": group}}],
            ))
        assert refused.value.extra["problems"][0]["path"] == "conditions.0.ignore_separators"


def test_the_separator_option_is_kept_on_the_saved_condition(world):
    group = make_group()

    created = group_rule(world, group, [{"fact": "name", "operator": "contains", "value": "krea2", "ignore_separators": True}])

    assert created["conditions"] == [
        {"fact": "name", "operator": "contains", "value": "krea2", "ignore_separators": True}
    ]


def test_model_tags_are_global_idempotent_and_undoable(world, seed):
    model = seed.model("krea2.safetensors")
    rule = world.manager.create_rule(world.viewer, rule_body(
        subject="model", actions=[{"action": "add_model_tags", "config": {"tags": ["Krea", "portrait"]}}]
    ))
    world.manager.handle_event("model_added", {"model_id": model})
    definition = world.registry.action("add_model_tags")

    again = definition.apply(SimpleNamespace(item_id=model, subject="model"), {"tags": ["krea"]}, None)

    names = sorted(r["name"] for r in seed.rows(
        "SELECT t.name FROM model_tags mt JOIN tags t ON t.id = mt.tag_id WHERE mt.model_id = ?", (model,)
    ))
    assert names == ["Krea", "portrait"]
    assert seed.rows("SELECT DISTINCT user_id FROM tags WHERE type = 'MODEL'") == [{"user_id": None}]
    assert again == []
    assert any(str(h).endswith("after_update_tags") for h, _ in world.plugins.fired)
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    assert [t["name"] for t in run["changes"]["tags"]] == ["Krea", "portrait"]

    world.manager.undo_run(world.viewer, run["id"])

    assert seed.rows("SELECT * FROM model_tags WHERE model_id = ?", (model,)) == []
    assert rule["id"]


def test_set_model_type_waits_for_the_digest_then_sets_and_undoes(world, seed):
    model = seed.model("odd.safetensors", "checkpoint")
    unverify(world, model)
    rule = world.manager.create_rule(world.viewer, rule_body(
        subject="model", actions=[{"action": "set_model_type", "config": {"model_type": "lora"}}]
    ))

    world.manager.handle_event("model_added", {"model_id": model})

    waiting = outcome(world, rule["id"], model)
    assert (waiting["outcome"], waiting["code"], waiting["attempts"], waiting["next_try_at"]) == ("waiting", "unverified", 0, None)
    assert handled(world, rule["id"]) == set()
    assert world.types.set_calls == []

    seed._exec("UPDATE models SET sha256 = 'abc123' WHERE id = ?", (model,))
    world.manager.handle_event("models_verified", {"model_ids": [model]})

    assert world.types.set_calls == [(model, "lora", None)]
    assert outcome(world, rule["id"], model) is None
    assert handled(world, rule["id"]) == {model}
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    world.manager.undo_run(world.viewer, run["id"])
    assert world.types.reset_calls == [model]


def test_set_model_type_does_nothing_for_a_model_already_of_that_type(world, seed):
    model = seed.model("a.safetensors", "lora")
    world.manager.create_rule(world.viewer, rule_body(
        subject="model", actions=[{"action": "set_model_type", "config": {"model_type": "lora"}}]
    ))

    world.manager.handle_event("model_added", {"model_id": model})

    assert world.types.set_calls == []
    assert seed.rows("SELECT * FROM organize_applications") == []


def test_set_model_type_conflicts_are_skipped_and_pending_digests_wait(world, seed):
    first = seed.model("a.safetensors", "checkpoint")
    second = seed.model("b.safetensors", "checkpoint")
    rule = world.manager.create_rule(world.viewer, rule_body(
        subject="model", actions=[{"action": "set_model_type", "config": {"model_type": "lora"}}]
    ))
    world.types.error = ModelTypeConflictException("a lora with this name exists")
    world.manager.handle_event("model_added", {"model_id": first})
    world.types.error = None
    world.types.pending = True
    world.manager.handle_event("model_added", {"model_id": second})

    assert (outcome(world, rule["id"], first)["outcome"], outcome(world, rule["id"], first)["code"]) == ("skipped", "conflict")
    assert (outcome(world, rule["id"], second)["outcome"], outcome(world, rule["id"], second)["code"]) == ("waiting", "unverified")
    assert handled(world, rule["id"]) == {first}


def test_a_failing_action_no_longer_marks_the_item_done(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)

    def boom(item):
        raise RuntimeError("network down")

    world.fetch.behavior = boom
    world.manager.handle_event("model_added", {"model_id": model})

    row = outcome(world, rule["id"], model)
    assert (row["outcome"], row["code"], row["attempts"]) == ("waiting", "error", 1)
    assert row["next_try_at"] is not None
    assert handled(world, rule["id"]) == set()


def test_retry_outcomes_are_waiting_with_backoff_and_a_typed_code(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)

    def retry(item):
        raise OrganizeActionRetry("unreachable", "CivitAI could not be reached")

    world.fetch.behavior = retry
    world.manager.handle_event("model_added", {"model_id": model})

    row = outcome(world, rule["id"], model)
    assert (row["outcome"], row["code"], row["detail"], row["attempts"]) == (
        "waiting", "unreachable", "CivitAI could not be reached", 1
    )
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    assert run["outcomes"] == {"updated": 0, "skipped": 0, "waiting": 1}
    assert run["reasons"] == [{"outcome": "waiting", "code": "unreachable", "detail": "CivitAI could not be reached", "count": 1}]
    assert world.manager.get_rule(world.viewer, rule["id"])["waiting_count"] == 1


def test_after_five_attempts_a_waiting_item_is_skipped_and_stays_done(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)

    def retry(item):
        raise OrganizeActionRetry("unreachable", "CivitAI could not be reached", after_s=60)

    world.fetch.behavior = retry
    world.manager.handle_event("model_added", {"model_id": model})
    for attempt in range(2, MAX_ATTEMPTS + 1):
        seed._exec("UPDATE organize_outcomes SET next_try_at = '2000-01-01T00:00:00+00:00'")
        assert world.manager.retry_waiting() == 1
        row = outcome(world, rule["id"], model)
        expected = "waiting" if attempt < MAX_ATTEMPTS else "skipped"
        assert row["outcome"] == expected, attempt

    row = outcome(world, rule["id"], model)
    assert row["code"] == "gave_up"
    assert handled(world, rule["id"]) == {model}
    assert len(world.fetch.calls) == MAX_ATTEMPTS
    seed._exec("UPDATE organize_outcomes SET next_try_at = '2000-01-01T00:00:00+00:00'")
    assert world.manager.retry_waiting() == 0


def test_a_waiting_item_that_succeeds_later_clears_its_outcome_and_is_handled(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)
    state = {"fail": True}

    def flaky(item):
        if state["fail"]:
            raise OrganizeActionRetry("rate_limited", "slow down", after_s=60)
        return [OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")]

    world.fetch.behavior = flaky
    world.manager.handle_event("model_added", {"model_id": model})
    state["fail"] = False
    seed._exec("UPDATE organize_outcomes SET next_try_at = '2000-01-01T00:00:00+00:00'")

    world.manager.retry_waiting()

    assert outcome(world, rule["id"], model) is None
    assert handled(world, rule["id"]) == {model}
    assert len(seed.rows("SELECT * FROM organize_applications WHERE item_id = ?", (model,))) == 1
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    assert run["outcomes"] == {"updated": 1, "skipped": 0, "waiting": 0}
    assert run["matched"] == 1


def test_the_sweeper_leaves_items_that_are_not_due_and_rules_that_are_paused(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)
    world.fetch.behavior = lambda item: (_ for _ in ()).throw(OrganizeActionRetry("unreachable", "x", after_s=3600))
    world.manager.handle_event("model_added", {"model_id": model})

    assert world.manager.retry_waiting() == 0

    seed._exec("UPDATE organize_outcomes SET next_try_at = '2000-01-01T00:00:00+00:00'")
    world.manager.patch_rule(world.viewer, rule["id"], {"enabled": False})
    assert world.manager.retry_waiting() == 0
    assert len(world.fetch.calls) == 1


def test_a_skipped_action_lets_the_remaining_actions_run_and_the_item_is_done(world, seed):
    group = make_group()
    model = seed.model("a.safetensors", "lora")
    rule = world.manager.create_rule(world.viewer, rule_body(subject="model", actions=[
        {"action": "test.fetch", "config": {}},
        {"action": "assign_model_group", "config": {"group_id": group}},
    ]))
    world.fetch.behavior = lambda item: (_ for _ in ()).throw(OrganizeActionSkipped("hash_mismatch", "different file"))

    world.manager.handle_event("model_added", {"model_id": model})

    assert members(world, group) == {model}
    row = outcome(world, rule["id"], model)
    assert (row["outcome"], row["code"], row["detail"]) == ("skipped", "hash_mismatch", "different file")
    assert handled(world, rule["id"]) == {model}
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    assert run["outcomes"]["skipped"] == 1 and run["outcomes"]["updated"] == 1


def test_actions_that_need_the_digest_wait_for_the_file_to_be_verified_without_calling_the_plugin(world, seed):
    model = seed.model("a.safetensors", "lora")
    unverify(world, model)
    rule = fetch_rule(world)

    world.manager.handle_event("model_added", {"model_id": model})
    world.manager.handle_event("model_added", {"model_id": model})

    assert world.fetch.calls == []
    row = outcome(world, rule["id"], model)
    assert (row["outcome"], row["code"], row["attempts"]) == ("waiting", "unverified", 0)


def test_the_verified_trigger_runs_waiting_rules_for_the_model(world, seed):
    model = seed.model("a.safetensors", "lora")
    unverify(world, model)
    rule = fetch_rule(world)
    world.manager.handle_event("model_added", {"model_id": model})
    seed._exec("UPDATE models SET sha256 = 'f00d' WHERE id = ?", (model,))

    world.manager.handle_event("models_verified", {"model_ids": [model]})

    assert world.fetch.calls == [(model, None)]
    assert outcome(world, rule["id"], model) is None
    assert handled(world, rule["id"]) == {model}


def test_after_digests_hook_feeds_the_verified_trigger_through_the_worker(world, seed):
    model = seed.model("a.safetensors", "lora")
    unverify(world, model)
    rule = fetch_rule(world)
    world.manager.handle_event("model_added", {"model_id": model})
    seed._exec("UPDATE models SET sha256 = 'f00d' WHERE id = ?", (model,))
    worker = OrganizeWorker(world.manager.handle_event)
    chain = HookChain()
    worker.subscribe(chain)

    chain.execute("model_index.after_digests", initial_data={"model_ids": [model]})
    worker.drain()

    assert world.fetch.calls == [(model, None)]
    assert handled(world, rule["id"]) == {model}


def test_a_file_that_gets_a_new_hash_does_not_reset_the_ledger(world, seed):
    model = seed.model("a.safetensors", "lora")
    fetch_rule(world)
    world.manager.handle_event("model_added", {"model_id": model})
    assert len(world.fetch.calls) == 1

    seed._exec("UPDATE models SET sha256 = 'new-hash' WHERE id = ?", (model,))
    world.manager.handle_event("models_verified", {"model_ids": [model]})

    assert len(world.fetch.calls) == 1


def test_models_that_existed_before_the_rule_wait_for_apply_existing(world, seed):
    old = seed.model("old.safetensors", "lora", created_at="2020-01-01 00:00:00")
    rule = fetch_rule(world)

    world.manager.handle_event("models_verified", {"model_ids": [old]})
    assert world.fetch.calls == []

    job = world.backfill.start(world.viewer, rule["id"])

    assert world.fetch.calls == [(old, None)]
    assert job["scope"] == "instance"


def test_instance_rules_run_for_downloads_but_not_for_a_users_assignment(world, seed):
    group = make_group()
    group_rule(world, group)
    downloaded = seed.model("krea2.safetensors")
    assigned = seed.model("krea2-b.safetensors")

    world.manager.handle_event("model_added", {"model_id": assigned, "user_id": "u1"})
    world.manager.handle_event("model_added", {"model_id": downloaded})

    assert members(world, group) == {downloaded}


def test_instance_rules_react_to_tag_and_metadata_changes_of_a_model(world, seed):
    group = make_group()
    model = seed.model("plain.safetensors")
    group_rule(world, group, [{"fact": "name", "operator": "contains", "value": "krea"}], name="By name")
    seed._exec("UPDATE models SET filename = 'krea-renamed.safetensors' WHERE id = ?", (model,))

    world.manager.handle_event("model_metadata_changed", {"model_id": model, "user_id": None})

    assert members(world, group) == {model}


def test_the_global_kill_switch_stops_instance_rules(world, seed):
    group = make_group()
    group_rule(world, group)
    model = seed.model("a.safetensors")
    world.admin_ops.controls({"paused_all": True})

    world.manager.handle_event("model_added", {"model_id": model})

    assert members(world, group) == set()
    assert world.manager.list_rules(world.viewer, None)[0]["paused_reason"] == "admin_paused"
    with pytest.raises(OrganizeError) as stopped:
        world.backfill.start(world.viewer, world.manager.list_rules(world.viewer, None)[0]["id"])
    assert stopped.value.code == "organize_paused"


def test_instance_controls_pause_only_instance_rules(world, seed):
    group = make_group()
    group_rule(world, group)
    world.manager.create_rule(seed.user("someone"), rule_body(actions=[collection_action(name="Mine")]))
    model = seed.model("a.safetensors")

    row = world.admin_ops.instance_controls({"paused": True, "rule_cap": 3})
    world.manager.handle_event("model_added", {"model_id": model})

    assert (row["paused"], row["rule_cap"], row["effective_rule_cap"]) == (True, 3, 3)
    assert members(world, group) == set()
    overview = world.admin_ops.overview()
    assert overview["instance"]["rules"] == 1
    assert all(u["user_id"] != INSTANCE_SCOPE for u in overview["users"])


def test_paced_rules_are_exempt_from_the_hourly_pause_and_others_are_not(world, seed):
    group = make_group()
    world.admin_ops.controls({"hourly_limit": 1})
    paced = fetch_rule(world, name="Paced")
    plain = group_rule(world, group, name="Plain")
    models = [seed.model(f"m{i}.safetensors", "lora") for i in range(3)]

    for model in models:
        world.manager.handle_event("model_added", {"model_id": model})

    cards = {c["name"]: c for c in world.manager.list_rules(world.viewer, None)}
    assert cards["Paced"]["paused_reason"] is None
    assert cards["Plain"]["paused_reason"] == "rate_limited"
    assert len(world.fetch.calls) == 3
    assert paced["id"] != plain["id"]


def test_apply_existing_reports_updated_skipped_and_waiting_and_records_who_ran_it(world, seed):
    ok = seed.model("ok.safetensors", "lora")
    miss = seed.model("miss.safetensors", "lora")
    down = seed.model("down.safetensors", "lora")
    rule = fetch_rule(world)

    def behavior(item):
        if item.item_id == miss:
            raise OrganizeActionSkipped("not_found", "No model with this file hash")
        if item.item_id == down:
            raise OrganizeActionRetry("unreachable", "CivitAI could not be reached")
        return [OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")]

    world.fetch.behavior = behavior

    job = world.backfill.start(world.viewer, rule["id"])

    finished = world.backfill.get_job(world.viewer, job["id"])
    assert (finished["status"], finished["applied"], finished["skipped"], finished["waiting"]) == ("completed", 1, 1, 1)
    run = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]
    assert (run["started_by"], run["started_by_name"], run["scope"]) == ("root", "root", "instance")
    assert run["outcomes"] == {"updated": 1, "skipped": 1, "waiting": 1}
    assert run["changes"]["other"][0]["done_label"] == "Fetched data"
    assert run["can_undo"] is True
    assert world.notices.of_type("organize.job_finished")[0]["message"] == "Fetched data: 1 item (1 skipped, 1 waiting)"
    assert ok and miss and down


def test_run_items_lists_every_model_with_its_outcome_and_pages(world, seed):
    ids = {name: seed.model(f"{name}.safetensors", "lora") for name in ("a", "b", "c")}
    rule = fetch_rule(world)

    def behavior(item):
        if item.item_id == ids["b"]:
            raise OrganizeActionSkipped("hash_mismatch", "different file")
        if item.item_id == ids["c"]:
            raise OrganizeActionRetry("unreachable", "down")
        return [OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")]

    world.fetch.behavior = behavior
    world.backfill.start(world.viewer, rule["id"])
    run_id = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]["id"]

    everything = world.manager.run_items(world.viewer, run_id, None, 1, 25)
    waiting = world.manager.run_items(world.viewer, run_id, "waiting", 1, 25)
    page = world.manager.run_items(world.viewer, run_id, None, 2, 2)

    by_name = {i["name"]: i for i in everything["items"]}
    assert everything["total"] == 3
    assert (by_name["a"]["status"], by_name["b"]["status"], by_name["b"]["code"]) == ("updated", "skipped", "hash_mismatch")
    assert by_name["c"]["status"] == "waiting" and by_name["c"]["attempts"] == 1
    assert [i["name"] for i in waiting["items"]] == ["c"]
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2


def test_run_items_of_another_scope_are_not_found(world, seed):
    user = seed.user("someone")
    rule = world.manager.create_rule(user, rule_body(subject="model", actions=[collection_action(name="M")]))
    seed.model("a.safetensors")
    world.backfill.start(user, rule["id"])
    user_run = world.manager.activity(user, "model", None, None, 5)["runs"][0]["id"]

    with pytest.raises(OrganizeError) as refused:
        world.manager.run_items(world.viewer, user_run, None, 1, 25)

    assert refused.value.status_code == 404


def test_retrying_the_waiting_items_of_a_run_runs_a_job_that_finishes_them(world, seed):
    model = seed.model("a.safetensors", "lora")
    rule = fetch_rule(world)
    state = {"down": True}

    def behavior(item):
        if state["down"]:
            raise OrganizeActionRetry("unreachable", "down")
        return [OrganizeChange(target_type="plugin", target_id=item.item_id, target_name="CivitAI")]

    world.fetch.behavior = behavior
    world.backfill.start(world.viewer, rule["id"])
    run_id = world.manager.activity(world.viewer, "model", None, None, 5)["runs"][0]["id"]
    state["down"] = False

    job = world.backfill.retry_waiting(world.viewer, run_id)

    finished = world.backfill.get_job(world.viewer, job["id"])
    assert (finished["kind"], finished["status"], finished["applied"], finished["waiting"]) == ("retry", "completed", 1, 0)
    assert outcome(world, rule["id"], model) is None
    assert handled(world, rule["id"]) == {model}
    with pytest.raises(OrganizeError) as nothing:
        world.backfill.retry_waiting(world.viewer, run_id)
    assert nothing.value.code == "nothing_waiting"


def test_preview_lists_names_and_the_matches_dialog_pages_by_status(world, seed):
    group = make_group()
    done = [seed.model(f"krea2-{i}.safetensors") for i in range(3)]
    todo = [seed.model(f"krea2-new-{i}.safetensors") for i in range(4)]
    for model in done:
        seed._exec("INSERT INTO user_group_models (id, group_id, model_id) VALUES (?, ?, ?)", (f"x{model}", group, model))
    body = {
        "subject": "model",
        "conditions": [{"fact": "name", "operator": "contains", "value": "krea2"}],
        "actions": [{"action": "assign_model_group", "config": {"group_id": group}}],
    }

    preview = world.manager.preview(world.viewer, body)
    first = world.manager.preview_matches(world.viewer, {**body, "page": 1, "page_size": 5})
    changing = world.manager.preview_matches(world.viewer, {**body, "status": "would_change"})
    already = world.manager.preview_matches(world.viewer, {**body, "status": "already_done", "page_size": 2, "page": 2})

    assert (preview["matched"], preview["would_change"]) == (7, 4)
    assert all("name" in s for s in preview["sample"])
    assert first["total"] == 7 and len(first["items"]) == 5 and first["counts"] == {"would_change": 4, "already_done": 3}
    assert {i["item_id"] for i in changing["items"]} == set(todo)
    assert already["total"] == 3 and len(already["items"]) == 1
    assert {"name", "model_type", "status", "item_id"} <= set(first["items"][0])


def test_preview_would_change_understands_every_instance_action(world, seed):
    group = make_group()
    a = seed.model("a.safetensors", "lora")
    b = seed.model("b.safetensors", "checkpoint")
    seed.tag_model(a, "x")
    base = {"subject": "model", "conditions": []}

    tags = world.manager.preview(world.viewer, {**base, "actions": [{"action": "add_model_tags", "config": {"tags": ["x"]}}]})
    kind = world.manager.preview(world.viewer, {**base, "actions": [{"action": "set_model_type", "config": {"model_type": "lora"}}]})

    assert tags["would_change"] == 1
    assert kind["would_change"] == 1
    assert group and b


def test_templates_are_scoped_and_hide_actions_that_are_not_installed(world):
    user_templates = world.manager.templates("model", "user")
    instance_templates = world.manager.templates("model", "instance")

    assert [t["id"] for t in user_templates] == ["loras"]
    assert [t["id"] for t in instance_templates] == ["instance-name-to-group"]


def test_the_viewer_of_an_instance_is_never_a_user(world):
    assert viewer_of(world.viewer) is world.viewer
    assert world.viewer.id is None and world.viewer.is_admin and world.viewer.actor == "root"
    assert viewer_of(world.admin_user).id == "root"


def test_user_rule_processing_never_runs_instance_rules(world, seed):
    group = make_group()
    group_rule(world, group)
    model = seed.model("a.safetensors")
    seed.assign("u1", model)

    filed = world.manager.process_item("model", "u1", model)

    assert filed == 0
    assert members(world, group) == set()


def test_listing_by_subject_keeps_the_two_scopes_apart(world, seed):
    user = seed.user("someone")
    group = make_group()
    instance_rule = group_rule(world, group)
    personal = world.manager.create_rule(user, rule_body(subject="model", actions=[collection_action(name="Mine")]))

    assert [r["id"] for r in world.manager.list_rules(user, "model")] == [personal["id"]]
    assert [r["id"] for r in world.manager.list_rules(world.viewer, "model")] == [instance_rule["id"]]
    assert world.manager.reorder(world.viewer, "model", [instance_rule["id"]])[0]["id"] == instance_rule["id"]
    with pytest.raises(OrganizeError):
        world.manager.reorder(user, "model", [instance_rule["id"]])


def test_instance_attribute_choices_include_admin_only_attributes(world, seed):
    seed._exec(
        "INSERT INTO model_attribute_definitions (id, key, label, field_type, admin_only) "
        "VALUES ('d1', 'secret_grade', 'Secret grade', 'text', 1)"
    )

    options = world.manager.fact_options(world.viewer, "attribute", "model", "secret", 50)
    user_options = world.manager.fact_options(world.user("u1"), "attribute", "model", "secret", 50)

    assert [o["value"] for o in options] == ["secret_grade"]
    assert user_options == []


def due_rule_with_models(world, seed, name, count):
    rule = fetch_rule(world, name=name)
    ids = [seed.model(f"{name}-{i}.safetensors", "lora") for i in range(count)]
    for model_id in ids:
        seed._exec(
            "INSERT INTO organize_outcomes (rule_id, item_type, item_id, outcome, code, attempts, next_try_at, updated_at) "
            "VALUES (?, 'model', ?, 'waiting', 'unreachable', 1, '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')",
            (rule["id"], model_id),
        )
    return rule


def test_one_sweep_attempts_at_most_twenty_five_items_of_a_rule(world, seed):
    from src.features.organize.manager import SWEEP_PER_RULE

    rule = due_rule_with_models(world, seed, "big", SWEEP_PER_RULE + 15)

    processed = world.manager.retry_waiting()

    assert processed == SWEEP_PER_RULE == len(world.fetch.calls)
    remaining = seed.rows("SELECT COUNT(*) AS n FROM organize_outcomes WHERE rule_id = ?", (rule["id"],))[0]["n"]
    assert remaining == 15


def test_one_sweep_never_looks_at_more_than_the_total_cap_across_rules(world, seed):
    from src.features.organize.manager import SWEEP_PER_RULE, SWEEP_TOTAL

    rules = SWEEP_TOTAL // SWEEP_PER_RULE + 2
    for index in range(rules):
        due_rule_with_models(world, seed, f"rule{index}", SWEEP_PER_RULE + 2)

    processed = world.manager.retry_waiting()

    assert processed <= SWEEP_TOTAL
    assert processed == len(world.fetch.calls) > SWEEP_PER_RULE
