import shutil
from pathlib import Path

import pytest

from src.features.plans.errors import LimitExceeded
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import AdmissionRequest
from src.platform.plugins.registry import PluginRegistry
from tests.features.plans.conftest import make_plan

EXAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "limits_example_plugin"


@pytest.fixture
def plugin(tmp_path, kinds, plans):
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir()
    (tmp_path / "local").mkdir()
    shutil.copytree(EXAMPLE, marketplace / "limits-example")
    registry = PluginRegistry(str(marketplace), str(tmp_path / "local"), limit_kind_registry=kinds)
    registry.discover_plugins()
    assert registry.enable_plugin("limits-example") is True
    usage = kinds.get("example.projects").measure.__globals__["PROJECTS"]
    usage.clear()
    return registry, usage


def assign(plans, **limits):
    plan = make_plan(plans, "Plugin plan", **limits)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)
    return plan


def test_a_plugin_ledger_kind_counts_submits_and_refuses_with_its_code(seed, plans, plugin):
    seed.user("u1")
    assign(plans, **{"example.credits": 1})
    guard = plans.guard

    guard.admit(AdmissionRequest(point="submit", user_id="u1", ref_id="g1"))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(AdmissionRequest(point="submit", user_id="u1", ref_id="g2"))

    assert refused.value.payload()["code"] == "credits_exhausted"
    assert refused.value.payload()["resets_at"] == "2026-11-01T00:00:00+00:00"


def test_a_plugin_measured_kind_applies_only_where_it_says(seed, plans, plugin):
    seed.user("u1")
    assign(plans, **{"example.projects": 2})
    _, usage = plugin
    usage["u1"] = 2
    guard = plans.guard

    guard.admit(AdmissionRequest(point="upload", user_id="u1", incoming_bytes=10))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(AdmissionRequest(point="upload", user_id="u1", incoming_bytes=500))

    assert refused.value.payload()["message"] == "You have 2 of 2 projects. Ask your admin for more."


def test_kinds_of_a_disabled_plugin_show_inactive_and_are_never_enforced(seed, plans, plugin, manager):
    seed.user("u1")
    plan = assign(plans, **{"example.credits": 0})
    registry, _ = plugin

    descriptor = {k["key"]: k for k in manager.kinds()["kinds"]}["example.credits"]
    assert descriptor["plugin"] is True and descriptor["source"] == "limits-example"

    registry.disable_plugin("limits-example")

    plans.guard.admit(AdmissionRequest(point="submit", user_id="u1", ref_id="g1"))
    detail = manager.get_plan(plan.id)["plan"]
    assert detail["limits"] == [{"kind": "example.credits", "value": 0, "active": False}]
    assert manager.my_limits(seed.user("u2"))["limits"] == []


def test_a_plan_keeps_an_inactive_kind_on_save_but_cannot_add_an_unknown_one(seed, plans, plugin, manager):
    from src.features.plans.dto import PlanBody
    from src.features.plans.errors import PlanError

    plan = assign(plans, **{"example.credits": 5})
    registry, _ = plugin
    registry.disable_plugin("limits-example")

    kept = manager.update_plan(plan.id, PlanBody(name="Plugin plan", limits=[{"kind": "example.credits", "value": 5}]))
    assert kept["limits"][0]["active"] is False
    with pytest.raises(PlanError):
        manager.create_plan(PlanBody(name="New", limits=[{"kind": "example.credits", "value": 5}]))


def test_a_plugin_kind_whose_measure_breaks_is_skipped_not_enforced_as_zero(seed, plans, plugin):
    seed.user("u1")
    assign(plans, **{"example.projects": 0})
    _, usage = plugin
    usage["u1"] = "broken"

    plans.guard.admit(AdmissionRequest(point="upload", user_id="u1", incoming_bytes=500))
