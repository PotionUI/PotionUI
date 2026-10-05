import pytest

from src.features.organize import manager as manager_module
from src.features.organize.errors import OrganizeError
from src.features.organize.manager import NOTICE_JOB_FINISHED, NOTICE_JOB_PROGRESS
from tests.features.organize.conftest import collection_action, rule_body

LANDSCAPE = [{"fact": "aspect", "operator": "is", "value": "landscape"}]


@pytest.fixture
def history(seed):
    user = seed.user("u1")
    wide = [seed.generation("u1") for _ in range(3)]
    square = seed.generation("u1", files=((1024, 1024, "IMAGE", None),))
    return user, wide, square


def test_preview_counts_matches_and_what_would_change(seed, manager, history):
    user, wide, square = history
    landscapes = seed.collection("u1")
    seed._exec("INSERT INTO collection_generations (collection_id, generation_id) VALUES (?, ?)", (landscapes, wide[0]))

    preview = manager.preview(user, {
        "subject": "generation", "match": "all", "conditions": LANDSCAPE, "actions": [collection_action(landscapes)],
    })

    assert preview["matched"] == 3
    assert preview["would_change"] == 2
    assert preview["already_handled"] == 0
    assert preview["approximate"] is False
    assert {s["item_id"] for s in preview["sample"]} == set(wide)
    assert seed.members(landscapes) == {wide[0]}


def test_preview_of_a_new_collection_would_change_every_match(seed, manager, history):
    user, wide, _ = history

    preview = manager.preview(user, {
        "subject": "generation", "conditions": LANDSCAPE, "actions": [collection_action(name="Brand new")],
    })

    assert preview["would_change"] == 3
    assert seed.rows("SELECT * FROM collections") == []


def test_preview_with_tags_counts_items_missing_a_tag(seed, manager, history):
    user, wide, _ = history
    seed.tag_generation("u1", wide[0], "landscape")

    preview = manager.preview(user, {
        "subject": "generation", "conditions": LANDSCAPE,
        "actions": [{"action": "add_tags", "config": {"tags": ["Landscape"]}}],
    })

    assert preview["would_change"] == 2


def test_apply_existing_files_matches_and_records_one_backfill_run(seed, manager, notices, history):
    user, wide, square = history
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(name="Landscapes")]))

    job = manager.start_backfill(user, rule["id"])

    collection_id = manager.get_rule(user, rule["id"])["actions"][0]["config"]["collection_id"]
    assert seed.members(collection_id) == set(wide)
    finished = manager.get_job(user, job["id"])
    assert finished["status"] == "completed"
    assert (finished["total"], finished["processed"], finished["applied"]) == (3, 3, 3)
    activity = manager.activity(user, "generation", None, None, 50)["runs"]
    assert [(r["kind"], r["matched"], r["applied"], r["status"]) for r in activity] == [("backfill", 3, 3, "completed")]
    assert activity[0]["changes"]["collections"] == [
        {"id": collection_id, "name": "Landscapes", "count": 3, "created": False}
    ]
    assert notices.of_type(NOTICE_JOB_FINISHED)[0]["message"] == "Added 3 items to Landscapes"
    assert notices.of_type(NOTICE_JOB_PROGRESS)[0]["transient"] is True
    assert manager.get_rule(user, rule["id"])["filed_count"] == 3


def test_apply_existing_runs_in_chunks_and_bumps_history_once_per_chunk(seed, manager, history, monkeypatch):
    user, wide, _ = history
    monkeypatch.setattr(manager_module, "BACKFILL_CHUNK", 2)
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(name="L")]))
    bumps = []
    original = manager.c.writer.bump_history
    monkeypatch.setattr(manager.c.writer, "bump_history", lambda user_id: (bumps.append(user_id), original(user_id)))

    manager.start_backfill(user, rule["id"])

    assert bumps == ["u1", "u1"]


def test_apply_existing_skips_items_the_rule_already_handled(seed, manager, history):
    user, wide, _ = history
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(landscapes)]))
    manager.process_item("generation", "u1", wide[0])
    seed._exec("DELETE FROM collection_generations WHERE generation_id = ?", (wide[0],))

    job = manager.start_backfill(user, rule["id"])

    assert manager.get_job(user, job["id"])["total"] == 2
    assert seed.members(landscapes) == set(wide[1:])


def test_a_second_job_for_the_same_rule_is_refused_while_one_is_queued(seed, manager, executor, history):
    user, _, _ = history
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="L")]))
    executor.defer = True
    job = manager.start_backfill(user, rule["id"])

    with pytest.raises(OrganizeError) as excinfo:
        manager.start_backfill(user, rule["id"])

    assert excinfo.value.code == "job_running"
    assert excinfo.value.extra["job_id"] == job["id"]
    assert [j["id"] for j in manager.list_jobs(user, True)] == [job["id"]]


def test_cancelling_a_queued_job_files_nothing(seed, manager, executor, history):
    user, wide, _ = history
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(landscapes)]))
    executor.defer = True
    job = manager.start_backfill(user, rule["id"])

    assert manager.cancel_job(user, job["id"])["status"] == "cancelled"
    executor.run_deferred()

    assert seed.members(landscapes) == set()


def test_apply_existing_is_refused_while_paused_by_an_admin(seed, manager, history):
    user, _, _ = history
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="L")]))
    manager.admin_controls({"paused_all": True})

    with pytest.raises(OrganizeError) as excinfo:
        manager.start_backfill(user, rule["id"])

    assert (excinfo.value.code, excinfo.value.status_code) == ("organize_paused", 423)


def test_jobs_belong_to_their_owner(seed, manager, history):
    user, _, _ = history
    other = seed.user("u2")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="L")]))
    job = manager.start_backfill(user, rule["id"])

    with pytest.raises(OrganizeError):
        manager.get_job(other, job["id"])
    with pytest.raises(OrganizeError):
        manager.cancel_job(other, job["id"])
    assert manager.list_jobs(other, False) == []


def test_undo_removes_exactly_what_the_run_added(seed, manager, history):
    user, wide, square = history
    landscapes = seed.collection("u1")
    seed._exec("INSERT INTO collection_generations (collection_id, generation_id) VALUES (?, ?)", (landscapes, wide[0]))
    seed.tag_generation("u1", wide[1], "landscape")
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[
        collection_action(landscapes), {"action": "add_tags", "config": {"tags": ["landscape"]}},
    ]))
    manager.start_backfill(user, rule["id"])
    assert seed.members(landscapes) == set(wide)
    run = manager.activity(user, None, None, None, 50)["runs"][0]

    result = manager.undo_run(user, run["id"])

    assert seed.members(landscapes) == {wide[0]}
    assert seed.generation_tags(wide[1]) == ["landscape"]
    assert seed.generation_tags(wide[0]) == []
    assert seed.generation_tags(wide[2]) == []
    assert result["undone"] == 4
    assert result["skipped"] == 0
    assert result["run"]["status"] == "undone"
    assert result["run"]["can_undo"] is False
    assert manager.get_rule(user, rule["id"])["filed_count"] == 0


def test_undo_skips_what_the_user_already_removed_and_does_not_refile(seed, manager, history):
    user, wide, _ = history
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(landscapes)]))
    manager.start_backfill(user, rule["id"])
    seed._exec("DELETE FROM collection_generations WHERE generation_id = ?", (wide[0],))
    run = manager.activity(user, None, None, None, 50)["runs"][0]

    manager.undo_run(user, run["id"])
    manager.start_backfill(user, rule["id"])
    for generation in wide:
        manager.process_item("generation", "u1", generation)

    assert seed.members(landscapes) == set()


def test_undo_never_deletes_a_collection_the_run_created(seed, manager, history):
    user, wide, _ = history
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(name="Fresh")]))
    seed._exec("DELETE FROM collections")
    manager.start_backfill(user, rule["id"])
    run = manager.activity(user, None, None, None, 50)["runs"][0]
    assert run["changes"]["collections"][0]["created"] is True

    manager.undo_run(user, run["id"])

    assert [r["name"] for r in seed.rows("SELECT name FROM collections")] == ["Fresh"]


def test_undo_of_another_users_run_is_not_found(seed, manager, history):
    user, _, _ = history
    other = seed.user("u2")
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="L")]))
    manager.start_backfill(user, rule["id"])
    run = manager.activity(user, None, None, None, 50)["runs"][0]

    with pytest.raises(OrganizeError) as excinfo:
        manager.undo_run(other, run["id"])

    assert excinfo.value.code == "run_not_found"
    assert manager.activity(other, None, None, None, 50)["runs"] == []


def test_live_filing_groups_into_one_run_per_rule_per_hour(seed, manager, history):
    user, wide, _ = history
    landscapes = seed.collection("u1")
    manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(landscapes)]))

    for generation in wide:
        manager.process_item("generation", "u1", generation)

    runs = manager.activity(user, None, None, None, 50)["runs"]
    assert [(r["kind"], r["matched"], r["applied"]) for r in runs] == [("live", 3, 3)]


def test_provenance_lists_the_rule_and_says_when_it_was_deleted(seed, manager, history):
    user, wide, _ = history
    landscapes = seed.collection("u1")
    rule = manager.create_rule(user, rule_body(conditions=LANDSCAPE, actions=[collection_action(landscapes)]))
    manager.process_item("generation", "u1", wide[0])

    rows = manager.provenance(user, "generation", wide[0])
    assert [(r["rule_name"], r["rule_deleted"], r["target_type"], r["target_name"]) for r in rows] == [
        ("Krea landscapes", False, "collection", "Landscapes")
    ]

    manager.delete_rule(user, rule["id"])
    rows = manager.provenance(user, "generation", wide[0])
    assert [(r["rule_name"], r["rule_deleted"]) for r in rows] == [("Krea landscapes", True)]
    assert manager.provenance(seed.user("u2"), "generation", wide[0]) == []


def test_activity_pages_by_run_id(seed, manager, history):
    user, _, _ = history
    for name in ("a", "b", "c"):
        rule = manager.create_rule(user, rule_body(name=name, actions=[collection_action(name=name)]))
        manager.start_backfill(user, rule["id"])

    first = manager.activity(user, None, None, None, 2)
    second = manager.activity(user, None, None, first["next_before"], 2)

    assert [r["rule_name"] for r in first["runs"]] == ["c", "b"]
    assert [r["rule_name"] for r in second["runs"]] == ["a"]
    assert second["next_before"] is None


def test_prune_drops_runs_older_than_the_retention_window(seed, manager, history):
    user, wide, _ = history
    rule = manager.create_rule(user, rule_body(actions=[collection_action(name="L")]))
    manager.start_backfill(user, rule["id"])
    seed._exec("UPDATE organize_runs SET started_at = '2020-01-01T00:00:00+00:00'")

    assert manager.prune() == 1
    assert seed.rows("SELECT * FROM organize_applications") == []
