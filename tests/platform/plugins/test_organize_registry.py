import shutil
from pathlib import Path

import pytest
import yaml

from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.organize import (
    DuplicateOrganizeEntryError,
    InvalidOrganizeEntryError,
    OrganizeActionDefinition,
    OrganizeFactDefinition,
    OrganizeRegistry,
)
from src.platform.plugins.registry import PluginRegistry, PluginState

EXAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "organize_example_plugin"

BASE = {"id": "p", "name": "P", "version": "1.0.0", "description": "d", "author": "a", "type": "backend-only"}


def fact(key="example.labels", kind="tag_list", source="example", **extra):
    return OrganizeFactDefinition(key=key, label="Labels", subjects=("generation",), kind=kind,
                                  extract=lambda item: [], source=source, **extra)


def action(key="example.send", source="example", **extra):
    return OrganizeActionDefinition(key=key, label="Send", subjects=("generation",), apply=lambda *a: [],
                                    source=source, **extra)


def test_register_lookup_and_filter_by_subject():
    registry = OrganizeRegistry()
    registry.register_fact(fact())
    registry.register_fact(OrganizeFactDefinition(key="m", label="M", subjects=("model",), kind="enum",
                                                  extract=lambda item: None))
    registry.register_action(action())

    assert registry.fact("example.labels").source == "example"
    assert [f.key for f in registry.facts("generation")] == ["example.labels"]
    assert [f.key for f in registry.facts()] == ["example.labels", "m"]
    assert registry.action("example.send") is not None
    assert registry.actions("model") == []


def test_duplicate_keys_are_rejected():
    registry = OrganizeRegistry()
    registry.register_fact(fact())
    registry.register_action(action())

    with pytest.raises(DuplicateOrganizeEntryError):
        registry.register_fact(fact(source="other"))
    with pytest.raises(DuplicateOrganizeEntryError):
        registry.register_action(action(source="other"))


@pytest.mark.parametrize("definition", [
    lambda: fact(kind="color"),
    lambda: fact(operators=("contains",)),
    lambda: fact(triggers=("hourly",)),
    lambda: OrganizeFactDefinition(key="x", label="X", subjects=("prompt",), kind="enum", extract=lambda i: None),
    lambda: OrganizeFactDefinition(key="x", label="X", subjects=(), kind="enum", extract=lambda i: None),
])
def test_invalid_facts_are_rejected(definition):
    with pytest.raises(InvalidOrganizeEntryError):
        OrganizeRegistry().register_fact(definition())


def test_actions_with_unknown_config_kinds_are_rejected():
    with pytest.raises(InvalidOrganizeEntryError):
        OrganizeRegistry().register_action(action(config_schema=({"key": "x", "kind": "colour"},)))


def test_unregister_source_removes_only_that_source():
    registry = OrganizeRegistry()
    registry.register_fact(fact())
    registry.register_action(action())
    registry.register_fact(fact(key="core_fact", source="core"))

    registry.unregister_source("example")

    assert [f.key for f in registry.facts()] == ["core_fact"]
    assert registry.actions() == []
    assert registry.has_source("example") is False


def test_manifest_accepts_organize_sections():
    manifest = yaml.safe_load((EXAMPLE / "manifest.yml").read_text())

    schema = PluginManifestSchema.model_validate(manifest)

    assert [f.key for f in schema.organize_facts] == ["example.labels"]
    assert schema.organize_actions[0].requires_admin is True


@pytest.mark.parametrize("section,entry", [
    ("organize_facts", {"key": "labels", "label": "L", "subjects": ["generation"], "kind": "tag_list", "handler": "m.f"}),
    ("organize_facts", {"key": "a.b", "label": "L", "subjects": ["prompt"], "kind": "tag_list", "handler": "m.f"}),
    ("organize_facts", {"key": "a.b", "label": "L", "subjects": ["generation"], "kind": "colour", "handler": "m.f"}),
    ("organize_actions", {"key": "a.b", "label": "L", "subjects": ["generation"]}),
    ("organize_actions", {"key": "a.b", "label": "L", "subjects": ["generation"], "handler": "m.f", "extra": 1}),
])
def test_manifest_rejects_bad_entries(section, entry):
    with pytest.raises(Exception):
        PluginManifestSchema.model_validate({**BASE, section: [entry]})


@pytest.fixture
def plugin_registry(tmp_path):
    marketplace = tmp_path / "marketplace"
    local = tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    organize = OrganizeRegistry()
    registry = PluginRegistry(str(marketplace), str(local), organize_registry=organize)
    return registry, organize, marketplace


def test_enabling_the_plugin_registers_its_facts_and_actions_and_disabling_removes_them(plugin_registry):
    registry, organize, marketplace = plugin_registry
    shutil.copytree(EXAMPLE, marketplace / "organize-example")
    registry.discover_plugins()

    assert registry.enable_plugin("organize-example") is True
    labels = organize.fact("example.labels")
    webhook = organize.action("example.notify_webhook")
    assert labels.source == "organize-example"
    assert labels.options_handler is not None
    assert labels.triggers == ("item_created", "tags_changed")
    assert webhook.requires_admin is True
    assert webhook.undo is not None
    assert webhook.config_schema[0]["key"] == "channel"

    registry.disable_plugin("organize-example")

    assert organize.fact("example.labels") is None
    assert organize.action("example.notify_webhook") is None


def test_a_plugin_with_a_missing_handler_fails_to_enable_and_leaves_nothing(plugin_registry):
    registry, organize, marketplace = plugin_registry
    target = marketplace / "organize-example"
    shutil.copytree(EXAMPLE, target)
    manifest = yaml.safe_load((target / "manifest.yml").read_text())
    manifest["organize_actions"][0]["handler"] = "organize.missing"
    (target / "manifest.yml").write_text(yaml.dump(manifest))
    registry.discover_plugins()

    assert registry.enable_plugin("organize-example") is False
    assert registry.get_plugin_state("organize-example") == PluginState.ERROR
    assert organize.facts() == []
    assert organize.actions() == []


def test_a_plugin_fact_colliding_with_another_plugin_fails_to_enable(plugin_registry):
    registry, organize, marketplace = plugin_registry
    organize.register_fact(fact(source="someone-else"))
    shutil.copytree(EXAMPLE, marketplace / "organize-example")
    registry.discover_plugins()

    assert registry.enable_plugin("organize-example") is False
    assert organize.fact("example.labels").source == "someone-else"
