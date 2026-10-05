import shutil
from pathlib import Path

import pytest

from src.features.organize import manager as manager_module
from src.features.organize.errors import OrganizeError
from src.platform.plugins.registry import PluginRegistry
from tests.features.organize.conftest import collection_action, rule_body

EXAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "organize_example_plugin"

CATS = [{"fact": "example.labels", "operator": "has", "value": ["cat"]}]


@pytest.fixture
def plugin(tmp_path, registry, manager):
    marketplace = tmp_path / "marketplace"
    (tmp_path / "local").mkdir()
    marketplace.mkdir()
    shutil.copytree(EXAMPLE, marketplace / "organize-example")
    plugins = PluginRegistry(str(marketplace), str(tmp_path / "local"), organize_registry=registry)
    plugins.discover_plugins()
    assert plugins.enable_plugin("organize-example") is True
    module_globals = registry.action("example.notify_webhook").apply.__globals__
    module_globals["SENT"].clear()
    module_globals["UNDONE"].clear()
    return plugins, module_globals


def test_plugin_facts_appear_in_the_catalog_with_their_kind(seed, manager, plugin):
    user = seed.user("u1")

    facts = {f["key"]: f for f in manager.catalog(user, "generation")["facts"]}

    assert facts["example.labels"]["kind"] == "tag_list"
    assert facts["example.labels"]["source"] == "organize-example"
    assert facts["example.labels"]["previewable"] is False
    assert manager.fact_options(user, "example.labels", "generation", "ca", 10) == [{"value": "cat", "label": "cat"}]


def test_a_plugin_fact_drives_a_core_action(seed, manager, plugin):
    user = seed.user("u1")
    pets = seed.collection("u1", "Pets")
    manager.create_rule(user, rule_body(conditions=CATS, actions=[collection_action(pets)]))
    cat = seed.generation("u1", prompt="a cat on a sofa")
    dog = seed.generation("u1", prompt="a dog")

    manager.process_item("generation", "u1", cat)
    manager.process_item("generation", "u1", dog)

    assert seed.members(pets) == {cat}


def test_preview_of_a_plugin_fact_scans_items_and_marks_the_count_approximate_past_the_cap(seed, manager, plugin, monkeypatch):
    user = seed.user("u1")
    for _ in range(3):
        seed.generation("u1", prompt="cat")
    seed.generation("u1", prompt="dog")

    exact = manager.preview(user, {"subject": "generation", "conditions": CATS})
    monkeypatch.setattr(manager_module, "PREVIEW_SCAN_CAP", 2)
    capped = manager.preview(user, {"subject": "generation", "conditions": CATS})

    assert (exact["matched"], exact["approximate"]) == (3, False)
    assert capped["approximate"] is True


def test_admin_only_plugin_actions_are_hidden_from_users_and_refused(seed, manager, plugin):
    user = seed.user("u1")
    admin = seed.user("admin", admin=True)
    webhook = {"action": "example.notify_webhook", "config": {"channel": "#art"}}

    assert "example.notify_webhook" not in {a["key"] for a in manager.catalog(user, "generation")["actions"]}
    with pytest.raises(OrganizeError) as excinfo:
        manager.create_rule(user, rule_body(actions=[webhook]))
    assert excinfo.value.extra["problems"][0]["code"] == "action_admin_only"
    assert manager.create_rule(admin, rule_body(actions=[webhook]))["status"] == "active"


def test_plugin_actions_record_changes_and_undo_through_the_plugin(seed, manager, plugin):
    _plugins, module = plugin
    admin = seed.user("admin", admin=True)
    rule = manager.create_rule(admin, rule_body(conditions=CATS, actions=[
        {"action": "example.notify_webhook", "config": {"channel": "#art"}},
    ]))
    generation = seed.generation("admin", prompt="cat")

    manager.process_item("generation", "admin", generation)
    run = manager.activity(admin, None, None, None, 10)["runs"][0]
    manager.undo_run(admin, run["id"])

    assert module["SENT"] == [(generation, "#art", "admin")]
    assert run["changes"]["other"] == [{"action": "example.notify_webhook", "label": "Tell my webhook", "count": 1}]
    assert module["UNDONE"] == [(generation, "#art", "admin")]
    assert manager.get_rule(admin, rule["id"])["filed_count"] == 0


def test_disabling_the_plugin_flags_rules_and_stops_them(seed, manager, plugin):
    plugins, _ = plugin
    user = seed.user("u1")
    pets = seed.collection("u1", "Pets")
    rule = manager.create_rule(user, rule_body(conditions=CATS, actions=[collection_action(pets)]))

    plugins.disable_plugin("organize-example")
    manager.process_item("generation", "u1", seed.generation("u1", prompt="cat"))

    described = manager.get_rule(user, rule["id"])
    assert described["status"] == "needs_attention"
    assert described["issues"][0]["code"] == "fact_unavailable"
    assert seed.members(pets) == set()
    with pytest.raises(OrganizeError) as excinfo:
        manager.start_backfill(user, rule["id"])
    assert excinfo.value.code == "rule_not_runnable"
