import shutil
from pathlib import Path

import pytest
import yaml

from src.platform.plugins.limit_kinds import (
    AdmissionRequest,
    DuplicateLimitKindError,
    InvalidLimitKindError,
    LimitKind,
    LimitKindRegistry,
    exceeded,
)
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry, PluginState

EXAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "limits_example_plugin"

BASE = {"id": "p", "name": "P", "version": "1.0.0", "description": "d", "author": "a", "type": "backend-only"}


def kind(key="example.credits", source="example", **extra):
    values = {"label": "Credits", "value_type": "count", "measure": lambda context: 0}
    values.update(extra)
    return LimitKind(key=key, source=source, **values)


def test_register_lookup_and_core_kinds_come_first():
    registry = LimitKindRegistry()
    registry.register(kind())
    registry.register(kind(key="storage_bytes", source="core", value_type="bytes"))

    assert registry.get("example.credits").source == "example"
    assert [k.key for k in registry.all()] == ["storage_bytes", "example.credits"]


def test_duplicate_keys_are_rejected():
    registry = LimitKindRegistry()
    registry.register(kind())

    with pytest.raises(DuplicateLimitKindError):
        registry.register(kind(source="other"))


@pytest.mark.parametrize("definition", [
    lambda: kind(key="credits"),
    lambda: kind(key="Bad.Key"),
    lambda: kind(value_type="pixels"),
    lambda: kind(window="week"),
    lambda: kind(format="stars"),
    lambda: kind(enforce_at=()),
    lambda: kind(enforce_at=("download",)),
    lambda: kind(measure=None),
    lambda: kind(measure=None, ledger=True),
    lambda: kind(warn_at=0),
    lambda: kind(value_type="usd", format="count"),
    lambda: kind(key="example.x", source="core"),
])
def test_invalid_kinds_are_rejected(definition):
    with pytest.raises(InvalidLimitKindError):
        LimitKindRegistry().register(definition())


def test_unregister_source_removes_only_that_source():
    registry = LimitKindRegistry()
    registry.register(kind())
    registry.register(kind(key="storage_bytes", source="core", value_type="bytes"))

    registry.unregister_source("example")

    assert [k.key for k in registry.all()] == ["storage_bytes"]
    assert registry.has_source("example") is False


def test_money_hides_its_values_unless_told_otherwise():
    assert kind(value_type="usd").hides_values is True
    assert kind(value_type="usd").user_format == "percent"
    assert kind(value_type="usd", admin_only_values=False).hides_values is False
    assert kind().user_format == "count"


def test_check_rules_and_what_each_point_brings_in():
    storage = kind(key="storage_bytes", source="core", value_type="bytes", enforce_at=("submit", "upload"))
    daily = kind(key="daily", source="core", measure=None, ledger=True, window="day")

    assert exceeded(10, 10, None) is True
    assert exceeded(10, 9, None) is False
    assert exceeded(10, 9, 1) is False
    assert exceeded(10, 9, 2) is True
    assert storage.incoming_for(AdmissionRequest(point="upload", user_id="u", incoming_bytes=5)) == 5
    assert storage.incoming_for(AdmissionRequest(point="submit", user_id="u")) is None
    assert daily.incoming_for(AdmissionRequest(point="submit", user_id="u")) == 1
    assert daily.applies_to(AdmissionRequest(point="upload", user_id="u")) is False


def test_manifest_accepts_the_example_section():
    schema = PluginManifestSchema.model_validate(yaml.safe_load((EXAMPLE / "manifest.yml").read_text()))

    assert [k.key for k in schema.limit_kinds] == ["example.credits", "example.projects"]
    assert schema.limit_kinds[0].ledger is True


@pytest.mark.parametrize("entry", [
    {"key": "credits", "label": "C", "value_type": "count", "enforce_at": ["submit"]},
    {"key": "a.b", "label": "C", "value_type": "pixels", "enforce_at": ["submit"]},
    {"key": "a.b", "label": "C", "value_type": "count", "enforce_at": []},
    {"key": "a.b", "label": "C", "value_type": "count", "enforce_at": ["download"]},
    {"key": "a.b", "label": "C", "value_type": "count", "enforce_at": ["submit"], "extra": 1},
    {"key": "a.b", "label": "C", "value_type": "count", "enforce_at": ["submit"], "warn_at": 2},
])
def test_manifest_rejects_bad_entries(entry):
    with pytest.raises(Exception):
        PluginManifestSchema.model_validate({**BASE, "limit_kinds": [entry]})


@pytest.fixture
def plugin_registry(tmp_path):
    marketplace = tmp_path / "marketplace"
    local = tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    kinds = LimitKindRegistry()
    registry = PluginRegistry(str(marketplace), str(local), limit_kind_registry=kinds)
    return registry, kinds, marketplace


def test_enabling_registers_the_kinds_and_disabling_removes_them(plugin_registry):
    registry, kinds, marketplace = plugin_registry
    shutil.copytree(EXAMPLE, marketplace / "limits-example")
    registry.discover_plugins()

    assert registry.enable_plugin("limits-example") is True
    credits = kinds.get("example.credits")
    projects = kinds.get("example.projects")
    assert credits.source == "limits-example"
    assert credits.ledger is True and credits.window == "month"
    assert credits.code == "credits_exhausted"
    assert projects.measure is not None and projects.applies is not None
    assert projects.code == "example.projects_exceeded"

    registry.disable_plugin("limits-example")

    assert kinds.get("example.credits") is None
    assert kinds.get("example.projects") is None


def test_a_plugin_with_a_missing_handler_fails_to_enable_and_leaves_nothing(plugin_registry):
    registry, kinds, marketplace = plugin_registry
    target = marketplace / "limits-example"
    shutil.copytree(EXAMPLE, target)
    manifest = yaml.safe_load((target / "manifest.yml").read_text())
    manifest["limit_kinds"][1]["measure_handler"] = "limits.missing"
    (target / "manifest.yml").write_text(yaml.safe_dump(manifest))
    registry.discover_plugins()

    assert registry.enable_plugin("limits-example") is False
    assert registry.get_plugin_state("limits-example") == PluginState.ERROR
    assert kinds.all() == []
