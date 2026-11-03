import pytest
import yaml
from pydantic import ValidationError

from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.prompt_resource_sources import (
    DuplicatePromptResourceSourceError,
    PromptResourceSource,
    PromptResourceSourceRegistry,
)
from src.platform.plugins.registry import PluginRegistry

ENTRY = {"id": "stack", "field_type": "lora_picker", "label": "Stack", "badge": "Stk", "members": "sources.members"}


def _source(source_id="stack", owner="core"):
    return PromptResourceSource(
        source_id=source_id, field_type="lora_picker", label="Stack", badge="Stk", members=lambda key, caller: [], source=owner,
    )


def test_a_registered_source_is_found_by_id_and_serves_its_spec():
    registry = PromptResourceSourceRegistry()
    registry.register(_source())
    assert registry.get("stack").spec() == {"id": "stack", "field_type": "lora_picker", "label": "Stack", "badge": "Stk"}
    assert registry.get("other") is None


def test_a_second_source_with_the_same_id_conflicts():
    registry = PromptResourceSourceRegistry()
    registry.register(_source())
    with pytest.raises(DuplicatePromptResourceSourceError, match="'stack'"):
        registry.register(_source(owner="plugin-b"))


def test_unregistering_a_plugin_drops_only_its_sources():
    registry = PromptResourceSourceRegistry()
    registry.register(_source("core-one"))
    registry.register(_source("mine", owner="plugin-a"))
    assert registry.unregister_source("plugin-a") == 1
    assert [entry.source_id for entry in registry.all()] == ["core-one"]


def _manifest(**extra):
    return {
        "id": "source-plugin", "name": "Source plugin", "version": "1.0.0", "description": "d", "author": "a",
        "type": "backend-only", **extra,
    }


def test_the_manifest_section_needs_every_key_and_nothing_else():
    PluginManifestSchema(**_manifest(prompt_resource_sources=[ENTRY]))
    with pytest.raises(ValidationError):
        PluginManifestSchema(**_manifest(prompt_resource_sources=[{k: v for k, v in ENTRY.items() if k != "members"}]))
    with pytest.raises(ValidationError):
        PluginManifestSchema(**_manifest(prompt_resource_sources=[{**ENTRY, "order": 1}]))


def _plugin(tmp_path, entries, handler="def members(item_key, caller):\n    return [{'kind': 'image', 'name': item_key}]\n"):
    marketplace = tmp_path / "marketplace"
    plugin_dir = marketplace / "source-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yml").write_text(yaml.safe_dump(_manifest(prompt_resource_sources=entries)))
    (plugin_dir / "sources.py").write_text(handler)
    return str(marketplace), str(tmp_path / "local")


def test_enabling_a_plugin_registers_its_source_with_the_loaded_handler_and_disabling_removes_it(tmp_path):
    sources = PromptResourceSourceRegistry()
    registry = PluginRegistry(*_plugin(tmp_path, [ENTRY]), prompt_resource_source_registry=sources)

    assert registry.enable_plugin("source-plugin"), registry.get_plugin_error("source-plugin")
    definition = sources.get("stack")
    assert (definition.label, definition.badge, definition.source) == ("Stack", "Stk", "source-plugin")
    assert definition.members("model:x", None) == [{"kind": "image", "name": "model:x"}]

    registry.disable_plugin("source-plugin")
    assert sources.get("stack") is None


def test_a_plugin_claiming_a_taken_source_id_fails_to_enable(tmp_path):
    sources = PromptResourceSourceRegistry()
    sources.register(_source())
    registry = PluginRegistry(*_plugin(tmp_path, [ENTRY]), prompt_resource_source_registry=sources)

    assert not registry.enable_plugin("source-plugin")
    assert "'stack'" in registry.get_plugin_error("source-plugin")
    assert sources.get("stack").source == "core"


def test_a_missing_members_handler_fails_the_enable(tmp_path):
    sources = PromptResourceSourceRegistry()
    registry = PluginRegistry(*_plugin(tmp_path, [ENTRY], handler="x = 1\n"), prompt_resource_source_registry=sources)

    assert not registry.enable_plugin("source-plugin")
    assert "sources.members" in registry.get_plugin_error("source-plugin")
    assert sources.get("stack") is None
