import pytest

from src.features.plans.records import GroupPlan, Plan, PlanLimit, PlanSubject
from src.features.plans.resolver import PlanResolver
from src.features.user_groups.constants import ALL_USERS_GROUP_ID

ACTIVE = {"storage_bytes", "generations_per_day", "cloud_spend_usd_month"}


def plan(plan_id, **limits):
    return Plan(id=plan_id, name=plan_id.title(), limits=tuple(PlanLimit(k, v) for k, v in limits.items()))


PLANS = {
    "free": plan("free", storage_bytes=5, generations_per_day=20),
    "tier1": plan("tier1", storage_bytes=20, generations_per_day=100),
    "tier2": plan("tier2", storage_bytes=100, generations_per_day=500, cloud_spend_usd_month=50),
    "uploader": plan("uploader", storage_bytes=200),
    "tiny": plan("tiny", storage_bytes=1),
    "unlimited": plan("unlimited"),
    "credits": plan("credits", **{"example.credits": 10, "storage_bytes": 7}),
}


def subject(groups=(), override=None, default=None, account_type="USER"):
    return PlanSubject(
        user_id="u1", username="u1", email="u1@example.test", account_type=account_type,
        override_plan_id=override,
        groups=tuple(GroupPlan(gid, gid, pid) for gid, pid in groups),
        default_plan_id=default,
    )


@pytest.fixture
def resolver():
    return PlanResolver(is_active=lambda key: key in ACTIVE)


def values(resolution):
    return {kind: limit.value for kind, limit in resolution.limits.items()}


def test_nothing_assigned_anywhere_means_no_limits(resolver):
    resolution = resolver.resolve(subject(), PLANS)

    assert resolution.decided_by == "none"
    assert resolution.limits == {}
    assert resolution.primary is None


def test_the_all_users_plan_is_the_default_and_absent_kinds_are_unlimited(resolver):
    resolution = resolver.resolve(subject(default="uploader"), PLANS)

    assert resolution.source == "default"
    assert values(resolution) == {"storage_bytes": 200}
    assert resolution.primary.plan.id == "uploader"


def test_a_group_plan_replaces_the_default_even_when_the_default_is_bigger(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", "tiny")], default="tier2"), PLANS)

    assert resolution.decided_by == "groups"
    assert values(resolution) == {"storage_bytes": 1}
    assert resolution.limits["storage_bytes"].candidate.group_id == "g1"


def test_two_group_plans_take_the_most_generous_value_per_kind(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", "tier1"), ("g2", "tier2")], default="free"), PLANS)

    assert values(resolution) == {"storage_bytes": 100, "generations_per_day": 500, "cloud_spend_usd_month": None}


def test_a_group_plan_without_a_kind_counts_as_unlimited_and_wins(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", "tier1"), ("g2", "uploader")]), PLANS)

    assert values(resolution) == {"storage_bytes": 200, "generations_per_day": None}
    assert resolution.limits["generations_per_day"].candidate.group_id == "g2"
    assert [limit.kind for limit in resolution.limited()] == ["storage_bytes"]


def test_the_override_replaces_every_group_plan(resolver):
    resolution = resolver.resolve(subject(groups=[("g2", "tier2")], override="tiny", default="free"), PLANS)

    assert resolution.decided_by == "override"
    assert values(resolution) == {"storage_bytes": 1}


def test_an_unlimited_override_lifts_everything(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", "tier1")], override="unlimited", default="free"), PLANS)

    assert resolution.decided_by == "override"
    assert resolution.limits == {}
    assert resolution.primary.plan.id == "unlimited"


def test_a_plan_on_the_all_users_group_membership_is_not_a_group_tier(resolver):
    resolution = resolver.resolve(subject(groups=[(ALL_USERS_GROUP_ID, "tier2")], default="free"), PLANS)

    assert resolution.decided_by == "default"
    assert values(resolution) == {"storage_bytes": 5, "generations_per_day": 20}


def test_plan_less_groups_and_unknown_plans_are_ignored(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", None), ("g2", "deleted"), ("g3", "tier1")]), PLANS)

    assert values(resolution) == {"storage_bytes": 20, "generations_per_day": 100}


def test_inactive_kinds_are_never_resolved(resolver):
    resolution = resolver.resolve(subject(default="credits"), PLANS)

    assert values(resolution) == {"storage_bytes": 7}


def test_the_primary_plan_is_the_group_that_wins_most_kinds(resolver):
    resolution = resolver.resolve(subject(groups=[("g1", "tier1"), ("g2", "tier2")]), PLANS)

    assert resolution.primary.plan.id == "tier2"
    assert [step.step for step in resolution.steps] == ["override", "groups", "default"]


def test_the_resolver_takes_its_sources_from_a_list(resolver):
    class Granted:
        step = source = "assignment"
        merge = False

        def candidates(self, subject, plans):
            from src.features.plans.resolver import PlanCandidate
            return [PlanCandidate(plans["tier2"])]

    custom = PlanResolver(is_active=lambda key: key in ACTIVE, sources=[Granted(), *resolver.sources])

    resolution = custom.resolve(subject(groups=[("g1", "tiny")]), PLANS)

    assert resolution.decided_by == "assignment"
    assert values(resolution)["storage_bytes"] == 100
