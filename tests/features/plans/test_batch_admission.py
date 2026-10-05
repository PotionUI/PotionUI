import pytest

from src.features.plans.errors import LimitExceeded
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import AdmissionRequest
from tests.features.plans.conftest import make_plan


def submit(user_id="u1"):
    return AdmissionRequest(point="submit", user_id=user_id, engine="native")


def free_plan(plans, daily):
    created = make_plan(plans, "Free", generations_per_day=daily)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, created.id)


def used(guard):
    return guard.measure(guard.registry.get("generations_per_day"), "u1").used


def test_a_batch_that_fits_is_allowed_and_records_nothing(seed, plans, guard):
    seed.user("u1")
    free_plan(plans, 12)

    guard.check_batch(submit(), 12)

    assert used(guard) == 0


def test_a_batch_over_the_remaining_allowance_is_refused_with_needed_and_remaining(seed, plans, guard):
    seed.user("u1")
    free_plan(plans, 12)
    for index in range(7):
        guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id=f"g{index}"))

    with pytest.raises(LimitExceeded) as refused:
        guard.check_batch(submit(), 6)

    payload = refused.value.payload()
    assert payload["code"] == "daily_generations_exceeded"
    assert (payload["needed"], payload["remaining"]) == (6, 5)
    assert used(guard) == 7


def test_the_batch_check_counts_each_cell_once_so_the_boundary_is_exact(seed, plans, guard):
    seed.user("u1")
    free_plan(plans, 12)
    for index in range(7):
        guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id=f"g{index}"))

    guard.check_batch(submit(), 5)
    with pytest.raises(LimitExceeded):
        guard.check_batch(submit(), 6)


def test_a_single_submit_refusal_carries_no_batch_figures(seed, plans, guard):
    seed.user("u1")
    free_plan(plans, 1)
    guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id="g0"))

    with pytest.raises(LimitExceeded) as refused:
        guard.admit(AdmissionRequest(point="submit", user_id="u1", engine="native", ref_id="g1"))

    payload = refused.value.payload()
    assert payload["needed"] is None
    assert payload["remaining"] is None


def test_admins_are_exempt_from_the_batch_check(seed, plans, guard):
    seed.user("admin", admin=True)
    free_plan(plans, 1)

    guard.check_batch(AdmissionRequest(point="submit", user_id="admin", engine="native"), 50)
