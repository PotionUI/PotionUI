from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker
from src.features.plans.errors import LimitExceeded
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import AdmissionRequest
from tests.features.plans.conftest import GB, make_plan


def submit(user_id="u1", ref_id="g1", engine="native"):
    return AdmissionRequest(point="submit", user_id=user_id, engine=engine, ref_id=ref_id)


def upload(size, user_id="u1"):
    return AdmissionRequest(point="upload", user_id=user_id, incoming_bytes=size)


def default_plan(plans, **limits):
    created = make_plan(plans, "Free", **limits)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, created.id)
    return created


def storage_used(guard, user_id="u1"):
    return guard.measure(guard.registry.get("storage_bytes"), user_id).used


def daily_used(guard, user_id="u1"):
    return guard.measure(guard.registry.get("generations_per_day"), user_id).used


def test_storage_sums_generation_files_and_library_uploads_only(seed, guard):
    seed.user("u1")
    seed.user("u2")
    seed.generation("u1", sizes=(100, 50))
    seed.upload("u1", 30)
    seed.upload("u1", 1000, purpose="derived_artifact")
    seed.generation("u2", sizes=(999,))

    assert storage_used(guard) == 180


def test_deleting_a_generation_frees_storage_at_once(seed, guard):
    seed.user("u1")
    generation_id = seed.generation("u1", sizes=(100,))
    seed.delete_generation(generation_id)

    assert storage_used(guard) == 0


def test_storage_refuses_submit_when_full_and_upload_when_the_file_does_not_fit(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, storage_bytes=1000)
    seed.upload("u1", 900)

    guard.admit(submit())
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(upload(101))
    guard.admit(upload(100))

    payload = refused.value.payload()
    assert payload["error"] == "limit_exceeded"
    assert payload["code"] == "storage_quota_exceeded"
    assert payload["kind"] == "storage_bytes"
    assert payload["point"] == "upload"
    assert (payload["used"], payload["limit"], payload["resets_at"]) == (900, 1000, None)
    assert payload["message"].startswith("Your storage is full.")
    assert payload["message"].endswith("Ask your admin for more.")

    seed.upload("u1", 100)
    with pytest.raises(LimitExceeded):
        guard.admit(submit(ref_id="g2"))


def test_daily_generations_count_each_submit_and_refuse_at_the_limit(seed, plans, guard, hooks):
    seed.user("u1")
    default_plan(plans, generations_per_day=2)

    guard.admit(submit(ref_id="g1"))
    guard.admit(submit(ref_id="g2"))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(submit(ref_id="g3"))

    payload = refused.value.payload()
    assert payload["code"] == "daily_generations_exceeded"
    assert (payload["used"], payload["limit"]) == (2, 2)
    assert payload["resets_at"] == "2026-10-06T00:00:00+00:00"
    assert "You can generate again in 12 h 0 min (at 00:00 UTC)." in payload["message"]
    assert daily_used(guard) == 2
    assert hooks[-1][0] == "plans.limit_exceeded"
    assert hooks[-1][1]["kinds"] == ["generations_per_day"]


def test_deleting_history_does_not_give_the_day_back(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, generations_per_day=1)
    generation_id = seed.generation("u1")
    guard.admit(submit(ref_id=generation_id))
    seed.delete_generation(generation_id)

    with pytest.raises(LimitExceeded):
        guard.admit(submit(ref_id="next"))


def test_submits_are_recorded_even_without_a_limit_so_a_later_plan_sees_today(seed, plans, guard):
    seed.user("u1")
    guard.admit(submit(ref_id="g1"))
    default_plan(plans, generations_per_day=1)

    with pytest.raises(LimitExceeded):
        guard.admit(submit(ref_id="g2"))


def test_the_day_resets_at_midnight_in_the_instance_timezone(seed, plans, guard, settings, clock):
    seed.user("u1")
    default_plan(plans, generations_per_day=1)
    settings.set_setting("plans_day_timezone", "Europe/Warsaw")
    clock.now = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)
    guard.admit(submit(ref_id="g1"))

    clock.now = datetime(2026, 10, 5, 21, 59, tzinfo=timezone.utc)
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(submit(ref_id="g2"))
    assert refused.value.payload()["resets_at"] == "2026-10-05T22:00:00+00:00"
    assert "(at 00:00 Europe/Warsaw)" in refused.value.payload()["message"]

    clock.now = datetime(2026, 10, 5, 22, 0, tzinfo=timezone.utc)
    guard.admit(submit(ref_id="g3"))


def test_cancelled_before_start_and_failed_without_output_are_refunded(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, generations_per_day=5)
    for ref in ("queued", "empty-failure"):
        guard.admit(submit(ref_id=ref))

    guard.on_generation_terminal(SimpleNamespace(id="queued", state=GenerationState.CANCELLED, started_at=None))
    guard.on_generation_terminal(SimpleNamespace(id="empty-failure", state=GenerationState.FAILED, started_at=1.0))

    assert daily_used(guard) == 0


def test_runs_that_used_the_machine_still_count(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, generations_per_day=5)
    with_file = seed.generation("u1", sizes=(10,))
    with_cost = seed.generation("u1")
    seed.cost("u1", 0.5, guard.clock(), generation_id=with_cost)
    for ref in ("mid-run", with_file, with_cost, "done"):
        guard.admit(submit(ref_id=ref))

    guard.on_generation_terminal(SimpleNamespace(id="mid-run", state=GenerationState.CANCELLED, started_at=1.0))
    guard.on_generation_terminal(SimpleNamespace(id=with_file, state=GenerationState.FAILED, started_at=1.0))
    guard.on_generation_terminal(SimpleNamespace(id=with_cost, state=GenerationState.FAILED, started_at=1.0))
    guard.on_generation_terminal(SimpleNamespace(id="done", state=GenerationState.COMPLETED, started_at=1.0))

    assert daily_used(guard) == 4


def test_the_status_tracker_tells_the_guard_about_terminal_states(seed, plans, guard, monkeypatch):
    from src.features.generation import status_tracker as tracker_module

    monkeypatch.setattr(tracker_module.generation_repo, "update_status", lambda *a, **k: None)
    seed.user("u1")
    tracker = GenerationStatusTracker()
    tracker.add_terminal_listener(guard.on_generation_terminal)
    tracker.create(id="queued", user_id="u1")
    tracker.create(id="ran", user_id="u1")
    guard.admit(submit(ref_id="queued"))
    guard.admit(submit(ref_id="ran"))

    tracker.transition("queued", GenerationState.CANCELLED)
    tracker.transition("ran", GenerationState.RUNNING)
    tracker.transition("ran", GenerationState.CANCELLED)

    assert daily_used(guard) == 1


def test_cloud_spend_is_a_monthly_sum_and_only_refuses_cloud_submits(seed, plans, guard, clock):
    seed.user("u1")
    default_plan(plans, cloud_spend_usd_month=10)
    seed.cost("u1", 4, datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc))
    seed.cost("u1", 6, datetime(2026, 10, 2, tzinfo=timezone.utc))
    seed.cost("u1", 3.5, datetime(2026, 10, 3, tzinfo=timezone.utc))

    assert guard.measure(guard.registry.get("cloud_spend_usd_month"), "u1").used == 9.5
    guard.admit(submit(ref_id="c1", engine="cloud"))
    seed.cost("u1", 0.5, datetime(2026, 10, 4, tzinfo=timezone.utc))
    guard.admit(submit(ref_id="n1", engine="native"))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(submit(ref_id="c2", engine="cloud"))

    payload = refused.value.payload()
    assert payload["code"] == "cloud_budget_exceeded"
    assert payload["used"] is None and payload["limit"] is None
    assert payload["percent"] == 100.0
    assert "$" not in payload["message"]
    assert "It resets on Nov 1." in payload["message"]


def test_admins_are_exempt_by_default_and_refused_when_the_toggle_is_off(seed, plans, guard, settings):
    seed.user("admin", admin=True)
    default_plan(plans, generations_per_day=0)

    guard.admit(submit(user_id="admin", ref_id="a1"))
    settings.set_setting("plans_exempt_admins", False)

    with pytest.raises(LimitExceeded):
        guard.admit(submit(user_id="admin", ref_id="a2"))
    assert daily_used(guard, "admin") == 1


def test_several_failing_kinds_list_storage_first(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, generations_per_day=0, storage_bytes=0)

    with pytest.raises(LimitExceeded) as refused:
        guard.admit(submit())

    assert refused.value.kinds == ["storage_bytes", "generations_per_day"]
    assert refused.value.payload()["code"] == "storage_quota_exceeded"


def test_a_refused_submit_records_nothing(seed, plans, guard):
    seed.user("u1")
    default_plan(plans, storage_bytes=0)

    with pytest.raises(LimitExceeded):
        guard.admit(submit())

    assert daily_used(guard) == 0


def test_unknown_users_pass_through_without_a_ledger_row(seed, plans, guard):
    default_plan(plans, generations_per_day=0)

    assert guard.admit(submit(user_id="setup")) == []


def test_the_ledger_is_pruned_after_its_retention(seed, plans, guard, clock):
    seed.user("u1")
    guard.admit(submit(ref_id="old"))
    clock.advance(days=41)
    guard._last_prune = 0.0

    guard.admit(submit(ref_id="new"))

    assert [row["ref_id"] for row in seed.rows("SELECT ref_id FROM limit_events")] == ["new"]


def test_group_limit_storage_is_still_enforced_with_more_generous_default(seed, plans, guard):
    seed.user("u1")
    group_id = seed.group("small")
    seed.join(group_id, "u1")
    small = make_plan(plans, "Small", storage_bytes=10)
    default_plan(plans, storage_bytes=10 * GB)
    plans.guard.plans.set_group_plan(group_id, small.id)
    seed.upload("u1", 10)

    with pytest.raises(LimitExceeded):
        guard.admit(upload(1))
